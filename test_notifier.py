"""Tests for notifier.py.

Run with: python -m unittest test_notifier -v
"""
import unittest
from unittest.mock import MagicMock, patch

import notifier


class NotifyTests(unittest.TestCase):
    def test_does_nothing_when_no_channel_is_configured(self):
        with patch("notifier.TELEGRAM_BOT_TOKEN", ""), patch("notifier.TELEGRAM_CHAT_ID", ""), patch(
            "notifier.ALERT_WEBHOOK_URL", ""
        ), patch("notifier.requests.post") as mock_post:
            notifier.notify("hello")

        mock_post.assert_not_called()

    def test_sends_telegram_when_configured(self):
        with patch("notifier.TELEGRAM_BOT_TOKEN", "tok"), patch("notifier.TELEGRAM_CHAT_ID", "123"), patch(
            "notifier.ALERT_WEBHOOK_URL", ""
        ), patch("notifier.requests.post", return_value=MagicMock(raise_for_status=lambda: None)) as mock_post:
            notifier.notify("bought PAXG/USDT")

        mock_post.assert_called_once()
        url, kwargs = mock_post.call_args[0][0], mock_post.call_args[1]
        self.assertIn("tok", url)
        self.assertEqual(kwargs["json"]["chat_id"], "123")
        self.assertEqual(kwargs["json"]["text"], "bought PAXG/USDT")

    def test_sends_webhook_when_configured(self):
        with patch("notifier.TELEGRAM_BOT_TOKEN", ""), patch("notifier.TELEGRAM_CHAT_ID", ""), patch(
            "notifier.ALERT_WEBHOOK_URL", "https://hooks.example.com/x"
        ), patch("notifier.requests.post", return_value=MagicMock(raise_for_status=lambda: None)) as mock_post:
            notifier.notify("sold PAXG/USDT")

        mock_post.assert_called_once_with(
            "https://hooks.example.com/x",
            json={"text": "sold PAXG/USDT", "content": "sold PAXG/USDT"},
            timeout=notifier.REQUEST_TIMEOUT_SECONDS,
        )

    def test_sends_to_both_channels_when_both_are_configured(self):
        with patch("notifier.TELEGRAM_BOT_TOKEN", "tok"), patch("notifier.TELEGRAM_CHAT_ID", "123"), patch(
            "notifier.ALERT_WEBHOOK_URL", "https://hooks.example.com/x"
        ), patch("notifier.requests.post", return_value=MagicMock(raise_for_status=lambda: None)) as mock_post:
            notifier.notify("hi")

        self.assertEqual(mock_post.call_count, 2)

    def test_a_failed_send_is_swallowed_not_raised(self):
        with patch("notifier.TELEGRAM_BOT_TOKEN", "tok"), patch("notifier.TELEGRAM_CHAT_ID", "123"), patch(
            "notifier.ALERT_WEBHOOK_URL", ""
        ), patch("notifier.requests.post", side_effect=ConnectionError("no route")):
            notifier.notify("hi")  # must not raise

    def test_an_http_error_response_is_also_swallowed(self):
        failing_response = MagicMock()
        failing_response.raise_for_status.side_effect = Exception("401 Unauthorized")

        with patch("notifier.TELEGRAM_BOT_TOKEN", "tok"), patch("notifier.TELEGRAM_CHAT_ID", "123"), patch(
            "notifier.ALERT_WEBHOOK_URL", ""
        ), patch("notifier.requests.post", return_value=failing_response):
            notifier.notify("hi")  # must not raise

    def test_a_failed_telegram_send_never_logs_the_bot_token(self):
        # requests/urllib3 exceptions routinely embed the full request
        # URL -- which for Telegram has the bot token baked into its
        # path -- in their own str(). Logging that verbatim would leak
        # the token in plaintext to logs/trading.log on every failure.
        secret_token = "123456:SUPER-SECRET-TOKEN"
        error_that_echoes_the_url = ConnectionError(
            f"Max retries exceeded with url: /bot{secret_token}/sendMessage"
        )

        with patch("notifier.TELEGRAM_BOT_TOKEN", secret_token), patch("notifier.TELEGRAM_CHAT_ID", "123"), patch(
            "notifier.ALERT_WEBHOOK_URL", ""
        ), patch("notifier.requests.post", side_effect=error_that_echoes_the_url):
            with self.assertLogs("trading_bot", level="WARNING") as captured:
                notifier.notify("hi")

        logged_text = "\n".join(captured.output)
        self.assertNotIn(secret_token, logged_text)

    def test_a_failed_webhook_send_never_logs_the_webhook_url(self):
        # A Slack/Discord webhook URL is itself a bearer credential --
        # anyone who has it can post to that channel -- so it gets the
        # same treatment as the Telegram token above.
        secret_url = "https://hooks.slack.com/services/T00/B00/xxxSECRETxxx"
        error_that_echoes_the_url = ConnectionError(f"Max retries exceeded with url: {secret_url}")

        with patch("notifier.TELEGRAM_BOT_TOKEN", ""), patch("notifier.TELEGRAM_CHAT_ID", ""), patch(
            "notifier.ALERT_WEBHOOK_URL", secret_url
        ), patch("notifier.requests.post", side_effect=error_that_echoes_the_url):
            with self.assertLogs("trading_bot", level="WARNING") as captured:
                notifier.notify("hi")

        logged_text = "\n".join(captured.output)
        self.assertNotIn(secret_url, logged_text)
        self.assertNotIn("xxxSECRETxxx", logged_text)


if __name__ == "__main__":
    unittest.main()
