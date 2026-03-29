"""HTTPS validation and fetch helpers for quality scripts."""

from __future__ import absolute_import

from dataclasses import dataclass
from http.client import HTTPConnection, HTTPS_PORT
import ipaddress
import json
import ssl
from typing import Any, cast, Dict, List, Mapping, Set, Tuple
from urllib.error import HTTPError
from urllib.parse import ParseResult, urlparse, urlunparse


_FORBIDDEN_IP_FLAGS = (
    "is_private",
    "is_loopback",
    "is_link_local",
    "is_reserved",
    "is_multicast",
)


@dataclass(frozen=True, slots=True)
class _HttpsRequest:
    """Describe a validated HTTPS request for the fetch helpers."""

    full_url: str
    data: bytes | None
    headers: Dict[str, str]
    method: str

    def get_method(self) -> str:
        """Return the HTTP method."""
        return self.method

    def header_items(self) -> List[Tuple[str, str]]:
        """Return request headers as a list of key-value pairs."""
        return list(self.headers.items())


def _get_ip_flag(ip_value: Any, flag_name: str) -> bool:
    """Return an IP address property as a boolean."""
    value = getattr(ip_value, flag_name)
    return bool(value() if callable(value) else value)


def _is_forbidden_ip_address(ip_value: ipaddress._BaseAddress) -> bool:
    """Return ``True`` when an IP address is private or otherwise disallowed."""
    return any(_get_ip_flag(ip_value, flag_name) for flag_name in _FORBIDDEN_IP_FLAGS)


def _require_https_scheme(parsed: ParseResult, raw_url: str) -> None:
    """Reject URLs that do not use HTTPS."""
    if parsed.scheme != "https":
        raise ValueError(f"Only https URLs are allowed: {raw_url!r}")


def _normalize_hostname(parsed: ParseResult, raw_url: str) -> str:
    """Normalize a parsed hostname and reject missing credentials."""
    if not parsed.hostname:
        raise ValueError(f"URL is missing a hostname: {raw_url!r}")
    if parsed.username or parsed.password:
        raise ValueError(f"URL credentials are not allowed: {raw_url!r}")
    return parsed.hostname.lower().strip(".")


def _normalized_allowlist(values: Set[str] | None) -> Set[str]:
    """Normalize allowlist values to lowercase host labels."""
    return {value.lower().strip(".") for value in (values or set()) if value.strip(".")}


def _validate_exact_hostname(hostname: str, allowed_hosts: Set[str] | None) -> None:
    """Require the hostname to match an exact allowlist entry."""
    normalized_hosts = _normalized_allowlist(allowed_hosts)
    if normalized_hosts and hostname not in normalized_hosts:
        raise ValueError(f"URL host is not in allowlist: {hostname}")


def _validate_hostname_suffixes(
    hostname: str,
    allowed_host_suffixes: Set[str] | None,
) -> None:
    """Require the hostname to match an allowed suffix."""
    suffixes = _normalized_allowlist(allowed_host_suffixes)
    if suffixes and not any(
        hostname == suffix or hostname.endswith(f".{suffix}") for suffix in suffixes
    ):
        raise ValueError(f"URL host is not in suffix allowlist: {hostname}")


def _validate_allowed_hostname(
    hostname: str,
    *,
    allowed_hosts: Set[str] | None = None,
    allowed_host_suffixes: Set[str] | None = None,
) -> None:
    """Apply the exact-host and suffix allowlist checks."""
    _validate_exact_hostname(hostname, allowed_hosts)
    _validate_hostname_suffixes(hostname, allowed_host_suffixes)


def _validate_public_hostname(hostname: str) -> None:
    """Reject local and private hostnames."""
    try:
        ip_value = ipaddress.ip_address(hostname)
    except ValueError:
        ip_value = None

    if ip_value is not None and _is_forbidden_ip_address(ip_value):
        raise ValueError(f"Private or local addresses are not allowed: {hostname}")

    if hostname in {"localhost", "localhost.localdomain"}:
        raise ValueError("Localhost URLs are not allowed.")


def _sanitize_url(parsed: ParseResult, *, strip_query: bool) -> str:
    """Remove fragments and optional query parameters from a parsed URL."""
    sanitized = parsed._replace(fragment="", params="")
    if strip_query:
        sanitized = sanitized._replace(query="")
    return urlunparse(sanitized)


def normalize_https_url(
    raw_url: str,
    *,
    allowed_hosts: Set[str] | None = None,
    allowed_host_suffixes: Set[str] | None = None,
    strip_query: bool = False,
) -> str:
    """Validate and normalize external URLs used by control-plane scripts."""
    parsed = urlparse((raw_url or "").strip())
    _require_https_scheme(parsed, raw_url)
    hostname = _normalize_hostname(parsed, raw_url)
    _validate_allowed_hostname(
        hostname,
        allowed_hosts=allowed_hosts,
        allowed_host_suffixes=allowed_host_suffixes,
    )
    _validate_public_hostname(hostname)
    return _sanitize_url(parsed, strip_query=strip_query)


def _build_request(
    parsed: ParseResult,
    *,
    headers: Mapping[str, str] | None,
    method: str,
    data: bytes | None,
) -> _HttpsRequest:
    """Build a validated HTTPS request envelope."""
    _require_request_hostname(parsed)
    request_url = urlunparse(parsed._replace(fragment=""))
    return _HttpsRequest(
        full_url=request_url,
        data=data,
        headers=dict(headers or {}),
        method=method,
    )


def _build_request_target(parsed: ParseResult) -> str:
    """Return the path, params, and query portion for a request target."""
    target = urlunparse(("", "", parsed.path or "/", parsed.params, parsed.query, ""))
    return target or "/"


def _require_request_hostname(parsed: ParseResult) -> str:
    """Require a hostname for request URLs."""
    if not parsed.hostname:
        raise ValueError(f"Request URL is missing a hostname: {urlunparse(parsed)!r}")
    return cast(str, parsed.hostname)


def _build_tls_context() -> ssl.SSLContext:
    """Construct a hardened TLS client context."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_default_certs()
    return context


class _ValidatedTLSConnection(HTTPConnection):
    """HTTP connection that upgrades to a verified TLS socket."""

    default_port = HTTPS_PORT

    def connect(self) -> None:
        """Connect and wrap the socket in TLS."""
        super().connect()
        self.sock = _build_tls_context().wrap_socket(
            self.sock,
            server_hostname=self.host,
        )


def _prepare_https_request(
    raw_url: str,
    *,
    function_name: str,
    kwargs: Dict[str, Any],
) -> Tuple[ParseResult, Dict[str, Any]]:
    """Normalize common HTTPS request parameters for the fetch helpers."""
    allowed_hosts = kwargs.pop("allowed_hosts", None)
    allowed_host_suffixes = kwargs.pop("allowed_host_suffixes", None)
    headers = kwargs.pop("headers", None)
    method = str(kwargs.pop("method", "GET"))
    data = kwargs.pop("data", None)
    timeout = int(kwargs.pop("timeout", 30))
    if kwargs:
        raise TypeError(
            f"Unexpected {function_name} parameters: {', '.join(sorted(kwargs))}"
        )
    safe_url = normalize_https_url(
        raw_url,
        allowed_hosts=allowed_hosts,
        allowed_host_suffixes=allowed_host_suffixes,
    )
    return urlparse(safe_url), {
        "headers": headers,
        "method": method,
        "data": data,
        "timeout": timeout,
    }


def _read_json_response(
    parsed: ParseResult,
    *args: Any,
    **kwargs: Any,
) -> Tuple[Any, Dict[str, str]]:
    """Read and decode a JSON HTTPS response."""
    payload_bytes, response_headers = _read_https_response(
        "_read_json_response",
        parsed,
        *args,
        **kwargs,
    )
    return json.loads(payload_bytes.decode("utf-8")), response_headers


def _read_https_response(
    function_name: str,
    parsed: ParseResult,
    *args: Any,
    **kwargs: Any,
) -> Tuple[bytes, Dict[str, str]]:
    """Read a raw HTTPS response after validating the request arguments."""
    if args:
        raise TypeError(f"{function_name} expects keyword arguments only")
    headers = kwargs.pop("headers", None)
    method = str(kwargs.pop("method"))
    data = kwargs.pop("data", None)
    timeout = int(kwargs.pop("timeout"))
    if kwargs:
        raise TypeError(
            f"Unexpected {function_name} parameters: {', '.join(sorted(kwargs))}"
        )
    request = _build_request(parsed, headers=headers, method=method, data=data)
    hostname = _require_request_hostname(parsed)
    connection = _ValidatedTLSConnection(
        hostname,
        port=parsed.port,
        timeout=timeout,
    )
    response = None
    try:
        connection.request(
            request.get_method(),
            _build_request_target(parsed),
            body=request.data,
            headers=dict(request.header_items()),
        )
        response = connection.getresponse()
        payload_bytes = response.read()
        response_headers = {
            key.lower(): value for key, value in response.headers.items()
        }
        if response.status >= 400:
            raise HTTPError(
                request.full_url,
                response.status,
                response.reason,
                hdrs=response.headers,
                fp=None,
            )
    finally:
        if response is not None and hasattr(response, "close"):
            response.close()
        connection.close()
    return payload_bytes, response_headers


def _read_bytes_response(
    parsed: ParseResult,
    *args: Any,
    **kwargs: Any,
) -> Tuple[bytes, Dict[str, str]]:
    """Read a raw byte HTTPS response."""
    return _read_https_response("_read_bytes_response", parsed, *args, **kwargs)


def load_json_https(
    raw_url: str,
    *args: Any,
    **kwargs: Any,
) -> Tuple[Any, Dict[str, str]]:
    """Fetch and decode a JSON document over HTTPS."""
    if args:
        raise TypeError("load_json_https expects keyword arguments only")
    parsed, request_kwargs = _prepare_https_request(
        raw_url,
        function_name="load_json_https",
        kwargs=kwargs,
    )
    return _read_json_response(
        parsed,
        **request_kwargs,
    )


def load_bytes_https(
    raw_url: str,
    *args: Any,
    **kwargs: Any,
) -> Tuple[bytes, Dict[str, str]]:
    """Fetch raw bytes over HTTPS."""
    if args:
        raise TypeError("load_bytes_https expects keyword arguments only")
    parsed, request_kwargs = _prepare_https_request(
        raw_url,
        function_name="load_bytes_https",
        kwargs=kwargs,
    )
    return _read_bytes_response(
        parsed,
        **request_kwargs,
    )
