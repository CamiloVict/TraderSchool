"""Local heartbeat file + optional external dead-man's-switch ping,
recorded after every trading cycle that completes without raising.

The local file (data/heartbeat.json) is only for quick manual
inspection ("cat data/heartbeat.json" to see when --trade last ran
successfully). It can't detect the failure mode that actually matters
most for something running unattended: the whole machine, cron/systemd,
or this bot's own process going down — a checker sharing that same
infrastructure goes dark right along with it, so nothing local can ever
notice its own silence.

HEARTBEAT_PING_URL (opt-in, empty by default — see config.py) is what
actually provides that protection: point it at a free external service
like https://healthchecks.io or https://cronitor.io, configured there
(on infrastructure outside this machine) to expect a ping at least
every ~1.5x the interval main.py --trade runs on, and to alert you if
one doesn't arrive. This module only has to GET that URL after a
successful cycle — the service's own independent schedule is what
notices silence, not anything running here.
"""
import json
import logging
import os
from datetime import datetime, timezone

import requests

from config import HEARTBEAT_PING_URL

logger = logging.getLogger("trading_bot")

DEFAULT_HEARTBEAT_PATH = "data/heartbeat.json"
REQUEST_TIMEOUT_SECONDS = 10


def record_heartbeat(heartbeat_path: str = DEFAULT_HEARTBEAT_PATH) -> None:
    """Write the local heartbeat file and, if configured, ping the
    external dead-man's-switch URL. The local write is allowed to
    raise (a disk error carries no secret, and the caller decides
    whether to swallow it — see main.py); the external ping never
    raises, see _ping_external()'s own docstring for why.
    """
    _write_local(heartbeat_path)
    if HEARTBEAT_PING_URL:
        _ping_external()


def _write_local(heartbeat_path: str) -> None:
    directory = os.path.dirname(heartbeat_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(heartbeat_path, "w") as f:
        json.dump({"last_success": datetime.now(timezone.utc).isoformat()}, f)


def _ping_external() -> None:
    try:
        requests.get(HEARTBEAT_PING_URL, timeout=REQUEST_TIMEOUT_SECONDS)
    except Exception as exc:
        # Same reasoning as notifier.py: requests/urllib3 exceptions
        # commonly embed the full request URL in their own message, and
        # a healthchecks.io/cronitor ping URL's path is itself an
        # unguessable token for that one check — logging the raw
        # exception would leak it in plaintext to logs/trading.log.
        logger.warning("Failed to ping HEARTBEAT_PING_URL (%s)", type(exc).__name__)
