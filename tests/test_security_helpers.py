from __future__ import annotations

import unittest
from typing import Literal
from unittest.mock import patch

from scripts.security_helpers import load_json_https, normalize_https_url


class _FakeHttpResponse:
    def __init__(self, payload: str, headers: dict[str, str] | None = None) -> None:
        self._payload = payload.encode("utf-8")
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> Literal[False]:
        return False


class _FakeConnection:
    def __init__(self, host: str, *, timeout: int) -> None:
        self.host = host
        self.timeout = timeout
        self.request_args: dict[str, object] = {}
        self.closed = False

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None) -> None:
        self.request_args = {
            "method": method,
            "path": path,
            "body": body,
            "headers": headers or {},
        }

    def getresponse(self) -> _FakeHttpResponse:
        return _FakeHttpResponse('{"ok": true}', {"X-Test": "value"})

    def close(self) -> None:
        self.closed = True


class SecurityHelpersTests(unittest.TestCase):
    def test_normalize_https_url_rejects_non_https(self) -> None:
        with self.assertRaises(ValueError):
            normalize_https_url("file:///tmp/example.json")

    def test_load_json_https_validates_allowlisted_host_before_open(self) -> None:
        with patch("http.client.HTTPSConnection") as connection_cls:
            with self.assertRaises(ValueError):
                load_json_https("https://evil.example.com/path", allowed_hosts={"api.github.com"})
        connection_cls.assert_not_called()

    def test_load_json_https_uses_normalized_request_and_collects_headers(self) -> None:
        captured: dict[str, object] = {}

        def fake_connection(host: str, *, timeout: int):
            connection = _FakeConnection(host, timeout=timeout)
            captured["connection"] = connection
            return connection

        with (
            patch("http.client.HTTPSConnection", side_effect=fake_connection),
            patch("urllib.request.urlopen", side_effect=AssertionError("urllib.request.urlopen should not be used")),
        ):
            payload, headers = load_json_https(
                "https://api.github.com/repos/Prekzursil/quality-zero-platform/status#frag",
                allowed_hosts={"api.github.com"},
                headers={"Accept": "application/json"},
                timeout=15,
            )

        connection = captured["connection"]
        self.assertIsInstance(connection, _FakeConnection)
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(headers, {"x-test": "value"})
        self.assertEqual(connection.host, "api.github.com")
        self.assertEqual(connection.timeout, 15)
        self.assertEqual(
            connection.request_args,
            {
                "method": "GET",
                "path": "/repos/Prekzursil/quality-zero-platform/status",
                "body": None,
                "headers": {"Accept": "application/json"},
            },
        )
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
