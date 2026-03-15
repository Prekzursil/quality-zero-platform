from __future__ import annotations

from dataclasses import dataclass
from http.client import HTTPSConnection
import ipaddress
import json
import ssl
from urllib.error import HTTPError
from typing import Any, Mapping, cast
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
    full_url: str
    data: bytes | None
    headers: dict[str, str]
    method: str

    def get_method(self) -> str:
        return self.method

    def header_items(self) -> list[tuple[str, str]]:
        return list(self.headers.items())


def _get_ip_flag(ip_value: Any, flag_name: str) -> bool:
    value = getattr(ip_value, flag_name)
    return bool(value() if callable(value) else value)


def _is_forbidden_ip_address(ip_value: ipaddress._BaseAddress) -> bool:
    return any(_get_ip_flag(ip_value, flag_name) for flag_name in _FORBIDDEN_IP_FLAGS)


def _require_https_scheme(parsed: ParseResult, raw_url: str) -> None:
    if parsed.scheme != "https":
        raise ValueError(f"Only https URLs are allowed: {raw_url!r}")


def _normalize_hostname(parsed: ParseResult, raw_url: str) -> str:
    if not parsed.hostname:
        raise ValueError(f"URL is missing a hostname: {raw_url!r}")
    if parsed.username or parsed.password:
        raise ValueError(f"URL credentials are not allowed: {raw_url!r}")
    return parsed.hostname.lower().strip(".")


def _normalized_allowlist(values: set[str] | None) -> set[str]:
    return {value.lower().strip(".") for value in (values or set()) if value.strip(".")}


def _validate_exact_hostname(hostname: str, allowed_hosts: set[str] | None) -> None:
    normalized_hosts = _normalized_allowlist(allowed_hosts)
    if normalized_hosts and hostname not in normalized_hosts:
        raise ValueError(f"URL host is not in allowlist: {hostname}")


def _validate_hostname_suffixes(hostname: str, allowed_host_suffixes: set[str] | None) -> None:
    suffixes = _normalized_allowlist(allowed_host_suffixes)
    if suffixes and not any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in suffixes):
        raise ValueError(f"URL host is not in suffix allowlist: {hostname}")


def _validate_allowed_hostname(
    hostname: str,
    *,
    allowed_hosts: set[str] | None = None,
    allowed_host_suffixes: set[str] | None = None,
) -> None:
    _validate_exact_hostname(hostname, allowed_hosts)
    _validate_hostname_suffixes(hostname, allowed_host_suffixes)


def _validate_public_hostname(hostname: str) -> None:
    try:
        ip_value = ipaddress.ip_address(hostname)
    except ValueError:
        ip_value = None

    if ip_value is not None and _is_forbidden_ip_address(ip_value):
        raise ValueError(f"Private or local addresses are not allowed: {hostname}")

    if hostname in {"localhost", "localhost.localdomain"}:
        raise ValueError("Localhost URLs are not allowed.")


def _sanitize_url(parsed: ParseResult, *, strip_query: bool) -> str:
    sanitized = parsed._replace(fragment="", params="")
    if strip_query:
        sanitized = sanitized._replace(query="")
    return urlunparse(sanitized)


def normalize_https_url(
    raw_url: str,
    *,
    allowed_hosts: set[str] | None = None,
    allowed_host_suffixes: set[str] | None = None,
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


def _build_request(parsed: ParseResult, *, headers: Mapping[str, str] | None, method: str, data: bytes | None) -> _HttpsRequest:
    _require_request_hostname(parsed)
    request_url = urlunparse(parsed._replace(fragment=""))
    return _HttpsRequest(
        full_url=request_url,
        data=data,
        headers=dict(headers or {}),
        method=method,
    )


def _build_request_target(parsed: ParseResult) -> str:
    target = urlunparse(("", "", parsed.path or "/", parsed.params, parsed.query, ""))
    return target or "/"


def _require_request_hostname(parsed: ParseResult) -> str:
    if not parsed.hostname:
        raise ValueError(f"Request URL is missing a hostname: {urlunparse(parsed)!r}")
    return cast(str, parsed.hostname)


def _build_tls_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_default_certs()
    return context


def _read_json_response(
    parsed: ParseResult,
    *,
    headers: Mapping[str, str] | None,
    method: str,
    data: bytes | None,
    timeout: int,
) -> tuple[Any, dict[str, str]]:
    request = _build_request(parsed, headers=headers, method=method, data=data)
    hostname = _require_request_hostname(parsed)
    connection = HTTPSConnection(
        hostname,
        port=parsed.port,
        timeout=timeout,
        context=_build_tls_context(),  # noqa: S309  # nosec B309 - validated HTTPS hosts plus an explicit TLS client context make this transport intentional and constrained.
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
        response_headers = {key.lower(): value for key, value in response.headers.items()}
        if response.status >= 400:
            raise HTTPError(request.full_url, response.status, response.reason, hdrs=response.headers, fp=None)
        payload = json.loads(payload_bytes.decode("utf-8"))
    finally:
        if response is not None and hasattr(response, "close"):
            response.close()
        connection.close()
    return payload, response_headers


def load_json_https(
    raw_url: str,
    *,
    allowed_hosts: set[str] | None = None,
    allowed_host_suffixes: set[str] | None = None,
    headers: Mapping[str, str] | None = None,
    method: str = "GET",
    data: bytes | None = None,
    timeout: int = 30,
) -> tuple[Any, dict[str, str]]:
    safe_url = normalize_https_url(
        raw_url,
        allowed_hosts=allowed_hosts,
        allowed_host_suffixes=allowed_host_suffixes,
    )
    parsed = urlparse(safe_url)
    return _read_json_response(
        parsed,
        headers=headers,
        method=method,
        data=data,
        timeout=timeout,
    )
