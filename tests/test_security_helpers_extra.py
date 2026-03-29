"""Test security helpers extra."""

from __future__ import absolute_import

import unittest
from urllib.error import HTTPError
from urllib.parse import urlparse
from unittest.mock import patch

from scripts import security_helpers


class _FakeBytesResponse:
    """Fake Bytes Response."""

    def __init__(self, payload: bytes, headers=None, *, status=200, reason="OK") -> None:
        """Handle init."""
        self._payload = payload
        self._headers = dict(headers or {})
        self.status = status
        self.reason = reason
        self.closed = False

    def read(self) -> bytes:
        """Handle read."""
        return self._payload

    @property
    def headers(self):
        """Handle headers."""
        return self._headers

    def close(self) -> None:
        """Handle close."""
        self.closed = True


class SecurityHelpersExtraTests(unittest.TestCase):
    """Security Helpers Extra Tests."""

    def test_prepare_https_request_validates_kwargs(self) -> None:
        """Cover prepare https request validates kwargs."""
        parsed, request_kwargs = security_helpers._prepare_https_request(
            "https://api.github.com/repos/Prekzursil/quality-zero-platform/status",
            function_name="load_bytes_https",
            kwargs={"headers": {"Accept": "application/json"}, "timeout": 15},
        )
        self.assertEqual(parsed.hostname, "api.github.com")
        self.assertEqual(request_kwargs["timeout"], 15)
        with self.assertRaisesRegex(TypeError, "Unexpected load_bytes_https parameters: extra"):
            security_helpers._prepare_https_request(
                "https://api.github.com/repos/Prekzursil/quality-zero-platform/status",
                function_name="load_bytes_https",
                kwargs={"extra": True},
            )

    def test_read_bytes_response_and_load_bytes_https_cover_success_and_error_paths(self) -> None:
        """Cover read bytes response and load bytes https cover success and error paths."""
        parsed = urlparse("https://api.github.com/repos/Prekzursil/quality-zero-platform/status")
        response = _FakeBytesResponse(b"payload", {"X-Test": "value"})
        with patch("scripts.security_helpers._ValidatedTLSConnection") as connection_cls:
            connection = connection_cls.return_value
            connection.getresponse.return_value = response
            payload, headers = security_helpers._read_bytes_response(
                parsed,
                headers={"Accept": "application/octet-stream"},
                method="GET",
                data=None,
                timeout=15,
            )
        self.assertEqual(payload, b"payload")
        self.assertEqual(headers, {"x-test": "value"})
        self.assertTrue(response.closed)
        connection.close.assert_called_once()

        error_response = _FakeBytesResponse(b"{}", status=404, reason="Not Found")
        with patch("scripts.security_helpers._ValidatedTLSConnection") as connection_cls:
            connection = connection_cls.return_value
            connection.getresponse.return_value = error_response
            with self.assertRaisesRegex(HTTPError, "HTTP Error 404: Not Found"):
                security_helpers._read_bytes_response(
                    parsed,
                    headers={"Accept": "application/octet-stream"},
                    method="GET",
                    data=None,
                    timeout=15,
                )
        self.assertTrue(error_response.closed)
        connection.close.assert_called_once()

        response = _FakeBytesResponse(b"bytes", {"X-Test": "value"})
        with patch("scripts.security_helpers._ValidatedTLSConnection") as connection_cls:
            connection = connection_cls.return_value
            connection.getresponse.return_value = response
            payload, headers = security_helpers.load_bytes_https(
                "https://api.github.com/repos/Prekzursil/quality-zero-platform/status",
                allowed_hosts={"api.github.com"},
                headers={"Accept": "application/octet-stream"},
                timeout=15,
            )
        self.assertEqual(payload, b"bytes")
        self.assertEqual(headers, {"x-test": "value"})
