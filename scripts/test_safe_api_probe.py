from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.error import URLError

from safe_api_probe import probe, validate_target


class SafeApiProbeTests(unittest.TestCase):
    def test_http_failure_reports_status_without_body_or_credentials(self) -> None:
        secret = "test-secret-marker"
        with patch("safe_api_probe.urlopen", side_effect=URLError(f"failed for Bearer {secret}")):
            result = probe("https://example.invalid/api", secret, 1)
        output = str(result)
        self.assertEqual(result["status"], 0)
        self.assertEqual(result["error"], "URLError")
        self.assertNotIn(secret, output)

    def test_http_status_is_sufficient_evidence_and_never_emits_token(self) -> None:
        secret = "test-secret-marker"

        class Response:
            status = 401

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *_: object) -> None:
                return None

        with patch("safe_api_probe.urlopen", return_value=Response()) as open_url:
            result = probe("https://example.invalid/api", secret, 1)
        request = open_url.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), f"Bearer {secret}")
        self.assertEqual(result["status"], 401)
        self.assertEqual(result["error"], None)
        self.assertNotIn(secret, repr(result))

    def test_refuses_bearer_over_plaintext_network_http(self) -> None:
        with self.assertRaises(ValueError):
            validate_target("http://192.168.2.117:8081/api/v1/news")
        with self.assertRaises(ValueError):
            validate_target("https://attacker.example/api/v1/news")
        validate_target("http://127.0.0.1:13001/api/v1/news")
        validate_target("https://trendradar-intelligence-relay.vik1ang.workers.dev/api/v1/news")


if __name__ == "__main__":
    unittest.main()
