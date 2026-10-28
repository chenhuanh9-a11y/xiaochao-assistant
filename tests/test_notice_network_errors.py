"""Offline error classification only; no outbound request or publication."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import publish_windows_notice as publisher


class NoticeNetworkErrorTests(unittest.TestCase):
    def test_permission_denial_retains_cause_and_never_retries(self):
        failure = urllib.error.URLError(PermissionError(13, "blocked"))
        with patch.object(publisher, "_request", side_effect=failure) as request:
            with self.assertRaises(publisher.PublishError) as caught:
                publisher.read_manifest(publisher.SERVER)
        self.assertIn("10013", str(caught.exception))
        self.assertIs(caught.exception.__cause__, failure)
        request.assert_called_once_with(publisher.SERVER + "/updates/windows.json")

    def test_raw_proxy_details_are_never_in_user_message(self):
        failure = urllib.error.URLError(PermissionError(13, "https://fixture-user:fixture-secret@proxy.invalid"))
        message = str(publisher._manifest_read_error(failure))
        for private in ("fixture-user", "fixture-secret", "proxy.invalid"):
            self.assertNotIn(private, message)

    def test_http_failure_keeps_status(self):
        with patch.object(publisher, "_request", return_value=(403, {})) as request:
            with self.assertRaises(publisher.PublishError) as caught:
                publisher.read_manifest(publisher.SERVER)
        self.assertIn("HTTP 403", str(caught.exception))
        self.assertEqual(request.call_count, 1)

    def test_valid_manifest_unchanged(self):
        data = {"version": "1.0.0", "versionCode": 1}
        with patch.object(publisher, "_request", return_value=(200, data)):
            self.assertIs(publisher.read_manifest(publisher.SERVER), data)


if __name__ == "__main__":
    unittest.main()
