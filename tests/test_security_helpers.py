from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.security_helpers import load_json_https, normalize_https_url


class _FakeResponse:
    def __init__(self, payload: str, headers: dict[str, str] | None = None) -> None:
        self._payload = payload.encode("utf-8")
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class SecurityHelpersTests(unittest.TestCase):
    def test_normalize_https_url_rejects_non_https(self) -> None:
        with self.assertRaises(ValueError):
            normalize_https_url("http://example.com")

    def test_load_json_https_validates_allowlisted_host_before_open(self) -> None:
        with patch("urllib.request.urlopen") as urlopen:
            with self.assertRaises(ValueError):
                load_json_https("https://evil.example.com/path", allowed_hosts={"api.github.com"})
        urlopen.assert_not_called()

    def test_load_json_https_uses_normalized_request_and_collects_headers(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout=0):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["method"] = request.get_method()
            captured["timeout"] = timeout
            return _FakeResponse('{"ok": true}', {"X-Test": "value"})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            payload, headers = load_json_https(
                "https://api.github.com/repos/Prekzursil/quality-zero-platform/status#frag",
                allowed_hosts={"api.github.com"},
                headers={"Accept": "application/json"},
                timeout=15,
            )

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(headers, {"x-test": "value"})
        self.assertEqual(captured["url"], "https://api.github.com/repos/Prekzursil/quality-zero-platform/status")
        self.assertEqual(captured["method"], "GET")
        self.assertEqual(captured["timeout"], 15)
        self.assertEqual(captured["headers"], {"Accept": "application/json"})


if __name__ == "__main__":
    unittest.main()
