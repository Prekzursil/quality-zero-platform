from __future__ import annotations

import http.client
import ipaddress
import json
from email.message import Message
from typing import Any, Mapping
from urllib.error import HTTPError
from urllib.parse import ParseResult, urlparse, urlunparse


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


def _build_request_path(parsed: ParseResult) -> str:
    request_path = parsed.path or "/"
    if parsed.query:
        request_path = f"{request_path}?{parsed.query}"
    return request_path


def _message_from_headers(response_headers: Mapping[str, str]) -> Message:
    message = Message()
    for key, value in response_headers.items():
        message[key] = value
    return message


def _open_https_connection(parsed: ParseResult, *, timeout: int) -> http.client.HTTPSConnection:
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"Request URL is missing a hostname: {urlunparse(parsed)!r}")
    return http.client.HTTPSConnection(  # noqa: S309  # nosec B309 - normalize_https_url constrains callers to validated HTTPS hosts on supported Python runtimes.
        hostname,
        parsed.port or 443,
        timeout=timeout,
    )


def _read_json_response(
    parsed: ParseResult,
    *,
    headers: Mapping[str, str] | None,
    method: str,
    data: bytes | None,
    timeout: int,
) -> tuple[Any, dict[str, str]]:
    connection = _open_https_connection(parsed, timeout=timeout)
    try:
        connection.request(
            method,
            _build_request_path(parsed),
            body=data,
            headers=dict(headers or {}),
        )
        response = connection.getresponse()
        payload_bytes = response.read()
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        if response.status >= 400:
            raise HTTPError(
                urlunparse(parsed),
                response.status,
                response.reason,
                hdrs=_message_from_headers(response_headers),
                fp=None,
            )
        payload = json.loads(payload_bytes.decode("utf-8"))
    finally:
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
