"""Tests for heartbeat.py.

Run with: python -m unittest test_heartbeat -v
"""
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import heartbeat as hb


class RecordHeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.heartbeat_path = os.path.join(self.tmpdir.name, "heartbeat.json")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_writes_a_timestamp_to_the_local_file(self):
        with patch("heartbeat.HEARTBEAT_PING_URL", ""):
            hb.record_heartbeat(heartbeat_path=self.heartbeat_path)

        with open(self.heartbeat_path) as f:
            saved = json.load(f)
        self.assertIn("last_success", saved)

    def test_creates_parent_directories_that_do_not_exist_yet(self):
        nested_path = os.path.join(self.tmpdir.name, "nested", "dir", "heartbeat.json")

        with patch("heartbeat.HEARTBEAT_PING_URL", ""):
            hb.record_heartbeat(heartbeat_path=nested_path)

        self.assertTrue(os.path.exists(nested_path))

    def test_does_not_ping_anything_when_no_url_is_configured(self):
        with patch("heartbeat.HEARTBEAT_PING_URL", ""), patch("heartbeat.requests.get") as mock_get:
            hb.record_heartbeat(heartbeat_path=self.heartbeat_path)

        mock_get.assert_not_called()

    def test_pings_the_configured_url(self):
        with patch("heartbeat.HEARTBEAT_PING_URL", "https://hc-ping.com/some-uuid"), patch(
            "heartbeat.requests.get", return_value=MagicMock()
        ) as mock_get:
            hb.record_heartbeat(heartbeat_path=self.heartbeat_path)

        mock_get.assert_called_once_with("https://hc-ping.com/some-uuid", timeout=hb.REQUEST_TIMEOUT_SECONDS)

    def test_a_failed_ping_does_not_raise(self):
        with patch("heartbeat.HEARTBEAT_PING_URL", "https://hc-ping.com/some-uuid"), patch(
            "heartbeat.requests.get", side_effect=ConnectionError("no route")
        ):
            hb.record_heartbeat(heartbeat_path=self.heartbeat_path)  # must not raise

    def test_a_failed_ping_never_logs_the_ping_url(self):
        # Same reasoning as notifier.py's equivalent test: requests/
        # urllib3 exceptions routinely embed the full request URL, and
        # a healthchecks.io/cronitor ping URL's path is itself an
        # unguessable token for that specific check.
        secret_url = "https://hc-ping.com/deadbeef-secret-uuid"
        error_that_echoes_the_url = ConnectionError(f"Max retries exceeded with url: {secret_url}")

        with patch("heartbeat.HEARTBEAT_PING_URL", secret_url), patch(
            "heartbeat.requests.get", side_effect=error_that_echoes_the_url
        ):
            with self.assertLogs("trading_bot", level="WARNING") as captured:
                hb.record_heartbeat(heartbeat_path=self.heartbeat_path)

        logged_text = "\n".join(captured.output)
        self.assertNotIn(secret_url, logged_text)
        self.assertNotIn("deadbeef-secret-uuid", logged_text)

    def test_a_local_write_failure_does_propagate(self):
        # Unlike the external ping, a local disk error carries no
        # secret -- letting it raise is what lets main.py's own
        # try/except decide how to handle and log it, same pattern as
        # trade_journal.py.
        unwritable_path = os.path.join(self.tmpdir.name, "nested", "dir", "heartbeat.json")
        with patch("heartbeat.HEARTBEAT_PING_URL", ""), patch("heartbeat.os.makedirs", side_effect=OSError("nope")):
            with self.assertRaises(OSError):
                hb.record_heartbeat(heartbeat_path=unwritable_path)


if __name__ == "__main__":
    unittest.main()
