"""Best-effort external notifications (Telegram and/or a generic
webhook) for main.py --trade's outcomes — so watching logs/trading.log
by hand isn't the only way to know the bot did something, or failed.

Deliberately best-effort: a notification failure (bad token, network
blip, misconfigured URL) must never fail the trading cycle itself —
that would turn an alerting nice-to-have into a new way for --trade to
break. Every send is wrapped so a failure only ever logs a warning.

Both channels are opt-in and off by default (see config.py) — silence
until configured is the right default here, same as every other
behavior-changing flag in this repo. Configure either, both, or
neither; `notify()` fans out to whichever are set.
"""
import logging

import requests

from config import ALERT_WEBHOOK_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger("trading_bot")

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
REQUEST_TIMEOUT_SECONDS = 10


def notify(message: str) -> None:
    """Best-effort fan-out to every configured channel. Does nothing
    (not even a network call) if none are configured."""
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        _send_telegram(message)
    if ALERT_WEBHOOK_URL:
        _send_webhook(message)


def _send_telegram(message: str) -> None:
    url = TELEGRAM_API_URL.format(token=TELEGRAM_BOT_TOKEN)
    try:
        response = requests.post(
            url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
    except Exception as exc:
        # Not exc_info=True / str(exc): requests/urllib3 exceptions
        # routinely embed the full request URL in their own message
        # (e.g. "Max retries exceeded with url: /bot<TOKEN>/sendMessage
        # ..."), and that URL has the bot token baked into its path —
        # logging it verbatim would leak the token in plaintext to
        # logs/trading.log on every failed send. The exception's class
        # name alone is enough to diagnose the failure without that risk.
        logger.warning("Failed to send Telegram notification (%s)", type(exc).__name__)


def _send_webhook(message: str) -> None:
    # Both "text" (Slack-style incoming webhooks) and "content"
    # (Discord's required field) are sent together so one URL works
    # with either without the caller having to say which service it
    # is — each side ignores the key it doesn't recognize.
    try:
        response = requests.post(
            ALERT_WEBHOOK_URL, json={"text": message, "content": message}, timeout=REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
    except Exception as exc:
        # Same reasoning as _send_telegram: a Slack/Discord webhook URL
        # is itself a bearer credential (anyone who has it can post to
        # that channel), and requests/urllib3 exceptions tend to echo
        # the full URL they were calling. Log the exception type only.
        logger.warning("Failed to send webhook notification (%s)", type(exc).__name__)
