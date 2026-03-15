from __future__ import annotations

import unittest
from urllib.parse import urlparse
from urllib.error import HTTPError
from unittest.mock import patch

from scripts import security_helpers
from scripts.security_helpers import _build_request, _get_ip_flag, load_json_https, normalize_https_url


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

    @property
    def headers(self) -> dict[str, str]:
        return self._headers

    def __enter__(self) -> _FakeHttpResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class SecurityHelpersTests(unittest.TestCase):
    def test_internal_ip_flag_helpers_cover_callable_and_forbidden_cases(self) -> None:
        class _CallableFlag:
            is_private = staticmethod(lambda: True)

        self.assertTrue(_get_ip_flag(_CallableFlag(), "is_private"))
        self.assertTrue(security_helpers._is_forbidden_ip_address(security_helpers.ipaddress.ip_address("127.0.0.1")))

    def test_normalize_https_url_rejects_non_https(self) -> None:
        with self.assertRaises(ValueError):
            normalize_https_url("file:///tmp/example.json")

    def test_normalize_https_url_rejects_missing_host_credentials_and_non_public_hosts(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing a hostname"):
            normalize_https_url("https:///missing-host")
        with self.assertRaisesRegex(ValueError, "credentials are not allowed"):
            normalize_https_url("https://user:pass@api.github.com/repos")
        with self.assertRaisesRegex(ValueError, "suffix allowlist"):
            normalize_https_url("https://api.github.com/repos", allowed_host_suffixes={"example.com"})
        with self.assertRaisesRegex(ValueError, "Private or local addresses"):
            normalize_https_url("https://127.0.0.1/private")
        with self.assertRaisesRegex(ValueError, "Localhost URLs are not allowed"):
            normalize_https_url("https://localhost/private")

    def test_normalize_https_url_can_strip_query_and_build_request_rejects_missing_hostname(self) -> None:
        self.assertEqual(
            normalize_https_url(
                "https://api.github.com/repos/Prekzursil/quality-zero-platform?foo=bar#frag",
                allowed_hosts={"api.github.com"},
                strip_query=True,
            ),
            "https://api.github.com/repos/Prekzursil/quality-zero-platform",
        )
        with self.assertRaisesRegex(ValueError, "missing a hostname"):
            _build_request(urlparse("https:///missing-host"), headers=None, method="GET", data=None)

    def test_load_json_https_validates_allowlisted_host_before_open(self) -> None:
        with patch("scripts.security_helpers.urlopen") as urlopen_mock:
            with self.assertRaises(ValueError):
                load_json_https("https://evil.example.com/path", allowed_hosts={"api.github.com"})
        urlopen_mock.assert_not_called()

    def test_load_json_https_uses_normalized_request_and_collects_headers(self) -> None:
        response = _FakeHttpResponse('{"ok": true}', {"X-Test": "value"})
        with patch("scripts.security_helpers.urlopen", return_value=response) as urlopen_mock:
            payload, headers = load_json_https(
                "https://api.github.com/repos/Prekzursil/quality-zero-platform/status#frag",
                allowed_hosts={"api.github.com"},
                headers={"Accept": "application/json"},
                timeout=15,
            )

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(headers, {"x-test": "value"})
        urlopen_mock.assert_called_once()
        request = urlopen_mock.call_args.args[0]
        timeout = urlopen_mock.call_args.kwargs["timeout"]
        self.assertEqual(timeout, 15)
        self.assertEqual(request.full_url, "https://api.github.com/repos/Prekzursil/quality-zero-platform/status")
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(
            {key.lower(): value for key, value in request.header_items()},
            {"accept": "application/json"},
        )

    def test_load_json_https_raises_http_error_for_non_success_response(self) -> None:
        error = HTTPError(
            "https://api.github.com/repos/Prekzursil/quality-zero-platform/status",
            404,
            "Not Found",
            hdrs=None,
            fp=None,
        )
        with patch("scripts.security_helpers.urlopen", side_effect=error):
            with self.assertRaisesRegex(Exception, "HTTP Error 404: Not Found"):
                load_json_https("https://api.github.com/repos/Prekzursil/quality-zero-platform/status")
