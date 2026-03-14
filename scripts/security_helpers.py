from __future__ import annotations

import http.client
import ipaddress
import json
from typing import Any, Mapping
from urllib.parse import ParseResult, urlparse, urlunparse
from urllib.request import Request, urlopen


_FORBIDDEN_IP_FLAGS = (
    "is_private",
    "is_loopback",
    "is_link_local",
    "is_reserved",
    "is_multicast",
)


def _get_ip_flag(ip_value: ipaddress._BaseAddress, flag_name: str) -> bool:
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


def _validate_allowed_hostname(
    hostname: str,
    *,
    allowed_hosts: set[str] | None = None,
    allowed_host_suffixes: set[str] | None = None,
) -> None:
    if allowed_hosts is not None and hostname not in {host.lower().strip(".") for host in allowed_hosts}:
        raise ValueError(f"URL host is not in allowlist: {hostname}")
    if allowed_host_suffixes is not None:
        suffixes = {suffix.lower().strip(".") for suffix in allowed_host_suffixes if suffix.strip(".")}
        if suffixes and not any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in suffixes):
            raise ValueError(f"URL host is not in suffix allowlist: {hostname}")


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


def _build_request(safe_url: str, *, headers: Mapping[str, str] | None, method: str, data: bytes | None) -> Request:
    return Request(
        safe_url,
        data=data,
        headers=dict(headers or {}),
        method=method,
    )


def _read_json_response(request: Request, *, timeout: int) -> tuple[Any, dict[str, str]]:
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
        response_headers = {key.lower(): value for key, value in response.headers.items()}
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
    request = _build_request(
        safe_url,
        headers=headers,
        method=method,
        data=data,
    )
    return _read_json_response(request, timeout=timeout)
