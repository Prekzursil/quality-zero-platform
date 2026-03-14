from __future__ import annotations

import unittest
from email.message import Message
from typing import Literal
from urllib.request import Request
from unittest.mock import patch

from scripts.security_helpers import load_json_https, normalize_https_url


class _FakeHttpResponse:
    def __init__(self, payload: str, headers: dict[str, str] | None = None) -> None:
        self._payload = payload.encode("utf-8")
        message = Message()
        for key, value in (headers or {}).items():
            message[key] = value
        self.headers = message

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> Literal[False]:
        return False


class _FakeUrlOpenCall:
    def __init__(self, request: Request, timeout: int) -> None:
        self.request = request
        self.timeout = timeout


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
        captured: list[_FakeUrlOpenCall] = []

        def fake_urlopen(request: Request, timeout: int = 30) -> _FakeHttpResponse:
            captured.append(_FakeUrlOpenCall(request, timeout))
            return _FakeHttpResponse('{"ok": true}', {"X-Test": "value"})

        with (
            patch("scripts.security_helpers.urlopen", side_effect=fake_urlopen),
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
        self.assertEqual(call.request.full_url, "https://api.github.com/repos/Prekzursil/quality-zero-platform/status")
        self.assertEqual(call.timeout, 15)
        self.assertEqual(
            {
                "method": call.request.get_method(),
                "body": call.request.data,
                "headers": {key: value for key, value in call.request.header_items()},
            },
            {
                "method": "GET",
                "body": None,
                "headers": {"Accept": "application/json"},
            },
        )


if __name__ == "__main__":
    unittest.main()
