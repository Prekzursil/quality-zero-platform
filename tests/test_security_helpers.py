from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.security_helpers import load_json_https, normalize_https_url


class _FakeHttpResponse:
    def __init__(
        self,
        payload: str,
        headers: dict[str, str] | None = None,
        *,
        status: int = 200,
        reason: str = "OK",
    ) -> None:
        self._payload = payload.encode("utf-8")
        self._headers = dict(headers or {})
        self.status = status
        self.reason = reason

    def read(self) -> bytes:
        return self._payload

    def getheaders(self) -> list[tuple[str, str]]:
        return list(self._headers.items())


class _FakeConnectionCall:
    def __init__(self, host: str | None, port: int, timeout: int) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout


class _FakeHttpsConnection:
    def __init__(
        self,
        host: str | None,
        port: int,
        *,
        timeout: int,
        response: _FakeHttpResponse | None = None,
    ) -> None:
        self.init_call = _FakeConnectionCall(host, port, timeout)
        self.request_calls: list[dict[str, object | None]] = []
        self.closed = False
        self._response = response or _FakeHttpResponse('{"ok": true}', {"X-Test": "value"})

    def request(self, method: str, url: str, *, body: bytes | None = None, headers: dict[str, str] | None = None) -> None:
        self.request_calls.append(
            {
                "method": method,
                "url": url,
                "body": body,
                "headers": dict(headers or {}),
            }
        )

    def getresponse(self) -> _FakeHttpResponse:
        return self._response

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
        captured: list[_FakeHttpsConnection] = []

        def fake_connection(host: str | None, port: int, *, timeout: int = 30) -> _FakeHttpsConnection:
            connection = _FakeHttpsConnection(host, port, timeout=timeout)
            captured.append(connection)
            return connection

        with patch(
            "scripts.security_helpers._open_https_connection",
            side_effect=lambda parsed, timeout: fake_connection(parsed.hostname, parsed.port or 443, timeout=timeout),
        ):
            payload, headers = load_json_https(
                "https://api.github.com/repos/Prekzursil/quality-zero-platform/status#frag",
                allowed_hosts={"api.github.com"},
                headers={"Accept": "application/json"},
                timeout=15,
            )

        self.assertEqual(len(captured), 1)
        call = captured[0]
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(headers, {"x-test": "value"})
        self.assertEqual(call.init_call.host, "api.github.com")
        self.assertEqual(call.init_call.port, 443)
        self.assertEqual(call.init_call.timeout, 15)
        self.assertEqual(
            call.request_calls,
            [
                {
                    "method": "GET",
                    "url": "/repos/Prekzursil/quality-zero-platform/status",
                    "body": None,
                    "headers": {"Accept": "application/json"},
                }
            ],
        )
        self.assertTrue(call.closed)

    def test_load_json_https_raises_http_error_for_non_success_response(self) -> None:
        def fake_connection(host: str | None, port: int, *, timeout: int = 30) -> _FakeHttpsConnection:
            return _FakeHttpsConnection(
                host,
                port,
                timeout=timeout,
                response=_FakeHttpResponse("{}", status=404, reason="Not Found"),
            )

        with patch(
            "scripts.security_helpers._open_https_connection",
            side_effect=lambda parsed, timeout: fake_connection(parsed.hostname, parsed.port or 443, timeout=timeout),
        ):
            with self.assertRaisesRegex(Exception, "HTTP Error 404: Not Found"):
                load_json_https("https://api.github.com/repos/Prekzursil/quality-zero-platform/status")


if __name__ == "__main__":
    unittest.main()
