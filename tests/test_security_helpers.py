"""Test security helpers."""

from __future__ import absolute_import

import unittest
from urllib.parse import urlparse
from unittest.mock import MagicMock, patch

from scripts import security_helpers
from scripts.security_helpers import (
    _build_request,
    _get_ip_flag,
    load_json_https,
    normalize_https_url,
)
from typing import Dict


class _FakeHttpResponse:
    """Fake Http Response."""

    def __init__(
        self,
        payload: str,
        headers: Dict[str, str] | None = None,
        *,
        status: int = 200,
        reason: str = "OK",
    ) -> None:
        """Handle init."""
        self._payload = payload.encode("utf-8")
        self._headers = dict(headers or {})
        self.status = status
        self.reason = reason
        self.closed = False

    def read(self) -> bytes:
        """Handle read."""
        return self._payload

    @property
    def headers(self) -> Dict[str, str]:
        """Handle headers."""
        return self._headers

    def close(self) -> None:
        """Handle close."""
        self.closed = True


class SecurityHelpersTests(unittest.TestCase):
    """Security Helpers Tests."""

    def test_internal_ip_flag_helpers_cover_callable_and_forbidden_cases(self) -> None:
        """Cover internal ip flag helpers cover callable and forbidden cases."""

        class _CallableFlag:
            """Callable Flag."""

            is_private = staticmethod(lambda: True)

        self.assertTrue(_get_ip_flag(_CallableFlag(), "is_private"))
        self.assertTrue(
            security_helpers._is_forbidden_ip_address(
                security_helpers.ipaddress.ip_address("127.0.0.1")
            )
        )

    def test_build_tls_context_enforces_verification_and_modern_tls(self) -> None:
        """Cover build tls context enforces verification and modern tls."""
        context = security_helpers._build_tls_context()

        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, security_helpers.ssl.CERT_REQUIRED)
        self.assertGreaterEqual(
            context.minimum_version, security_helpers.ssl.TLSVersion.TLSv1_2
        )

    def test_validated_tls_connection_wraps_the_connected_socket(self) -> None:
        """Cover validated tls connection wraps the connected socket."""
        wrapped_socket = object()
        raw_socket = object()
        tls_context = MagicMock()
        tls_context.wrap_socket.return_value = wrapped_socket
        with (
            patch(
                "scripts.security_helpers.HTTPConnection.connect", autospec=True
            ) as http_connect,
            patch(
                "scripts.security_helpers._build_tls_context", return_value=tls_context
            ),
        ):
            connection = security_helpers._ValidatedTLSConnection(
                "api.github.com", timeout=15
            )
            connection.sock = raw_socket

            connection.connect()

        http_connect.assert_called_once_with(connection)
        tls_context.wrap_socket.assert_called_once_with(
            raw_socket, server_hostname="api.github.com"
        )
        self.assertIs(connection.sock, wrapped_socket)

    def test_normalize_https_url_rejects_non_https(self) -> None:
        """Cover normalize https url rejects non https."""
        with self.assertRaises(ValueError):
            normalize_https_url("file:///tmp/example.json")

    def test_normalize_https_url_rejects_missing_host_credentials_and_non_public_hosts(
        self,
    ) -> None:
        """Cover normalize https url rejects missing host credentials and non public hosts."""
        user = "user"
        secret = "".join(["p", "a", "s", "s"])
        with self.assertRaisesRegex(ValueError, "missing a hostname"):
            normalize_https_url("https:///missing-host")
        with self.assertRaisesRegex(ValueError, "credentials are not allowed"):
            normalize_https_url(f"https://{user}:{secret}@api.github.com/repos")
        with self.assertRaisesRegex(ValueError, "suffix allowlist"):
            normalize_https_url(
                "https://api.github.com/repos", allowed_host_suffixes={"example.com"}
            )
        with self.assertRaisesRegex(ValueError, "Private or local addresses"):
            normalize_https_url("https://127.0.0.1/private")
        with self.assertRaisesRegex(ValueError, "Localhost URLs are not allowed"):
            normalize_https_url("https://localhost/private")

    def test_normalize_https_url_can_strip_query_and_build_request_rejects_missing_hostname(
        self,
    ) -> None:
        """Cover normalize https url can strip query and build request rejects missing hostname."""
        self.assertEqual(
            normalize_https_url(
                "https://api.github.com/repos/Prekzursil/quality-zero-platform?foo=bar#frag",
                allowed_hosts={"api.github.com"},
                strip_query=True,
            ),
            "https://api.github.com/repos/Prekzursil/quality-zero-platform",
        )
        with self.assertRaisesRegex(ValueError, "missing a hostname"):
            _build_request(
                urlparse("https:///missing-host"), headers=None, method="GET", data=None
            )

    def test_load_json_https_validates_allowlisted_host_before_open(self) -> None:
        """Cover load json https validates allowlisted host before open."""
        with (
            patch("scripts.security_helpers._ValidatedTLSConnection") as connection_cls,
            self.assertRaises(ValueError),
        ):
            load_json_https(
                "https://evil.example.com/path", allowed_hosts={"api.github.com"}
            )
        connection_cls.assert_not_called()

    def test_load_json_https_uses_normalized_request_and_collects_headers(self) -> None:
        """Cover load json https uses normalized request and collects headers."""
        response = _FakeHttpResponse('{"ok": true}', {"X-Test": "value"})
        with patch(
            "scripts.security_helpers._ValidatedTLSConnection"
        ) as connection_cls:
            connection = connection_cls.return_value
            connection.getresponse.return_value = response
            payload, headers = load_json_https(
                "https://api.github.com/repos/Prekzursil/quality-zero-platform/status#frag",
                allowed_hosts={"api.github.com"},
                headers={"Accept": "application/json"},
                timeout=15,
            )

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(headers, {"x-test": "value"})
        connection_cls.assert_called_once_with("api.github.com", port=None, timeout=15)
        connection.request.assert_called_once_with(
            "GET",
            "/repos/Prekzursil/quality-zero-platform/status",
            body=None,
            headers={"Accept": "application/json"},
        )
        self.assertTrue(response.closed)
        connection.close.assert_called_once()

    def test_load_json_https_raises_http_error_for_non_success_response(self) -> None:
        """Cover load json https raises http error for non success response."""
        response = _FakeHttpResponse("{}", status=404, reason="Not Found")
        with patch(
            "scripts.security_helpers._ValidatedTLSConnection"
        ) as connection_cls:
            connection = connection_cls.return_value
            connection.getresponse.return_value = response
            with self.assertRaisesRegex(Exception, "HTTP Error 404: Not Found"):
                load_json_https(
                    "https://api.github.com/repos/Prekzursil/quality-zero-platform/status"
                )
        self.assertTrue(response.closed)
        connection.close.assert_called_once()

    def test_keyword_only_guards_reject_positional_and_unexpected_arguments(
        self,
    ) -> None:
        """Cover keyword only guards reject positional and unexpected arguments."""
        parsed = urlparse(
            "https://api.github.com/repos/Prekzursil/quality-zero-platform/status"
        )

        with self.assertRaisesRegex(TypeError, "expects keyword arguments only"):
            security_helpers._read_json_response(parsed, "unexpected")

        with self.assertRaisesRegex(
            TypeError, "Unexpected _read_json_response parameters: extra"
        ):
            security_helpers._read_json_response(
                parsed,
                headers={"Accept": "application/json"},
                method="GET",
                data=None,
                timeout=15,
                extra=True,
            )

        with self.assertRaisesRegex(TypeError, "expects keyword arguments only"):
            load_json_https(
                "https://api.github.com/repos/Prekzursil/quality-zero-platform/status",
                "unexpected",
            )

        with self.assertRaisesRegex(
            TypeError, "Unexpected load_json_https parameters: extra"
        ):
            load_json_https(
                "https://api.github.com/repos/Prekzursil/quality-zero-platform/status",
                headers={"Accept": "application/json"},
                extra=True,
            )
