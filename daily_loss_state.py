"""Persists the trading day's starting equity across separate
`main.py --trade` invocations.

risk_manager.DailyLossTracker is deliberately in-memory only (see its
own docstring) — correct for a single long-running process, but
main.py --trade is designed to be invoked fresh once per candle close
by cron/systemd (see main.py's module docstring), so an in-memory
tracker resets to zero on every single cycle and its circuit breaker
never actually trips. This module is the thin file-backed layer that
makes "today's starting equity" survive between cron ticks, so a fresh
DailyLossTracker built each cycle from the persisted value behaves like
one long-lived tracker would have.

Deliberately tiny: one float and one date, one file. Not a general
state store — see risk_manager.DailyLossTracker's own docstring for why
this is an accepted, revisit-before-real-capital simplification.
"""
import json
import os
from datetime import datetime, timezone

DEFAULT_STATE_PATH = "data/daily_loss_state.json"


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def load_or_init_starting_capital(current_equity: float, state_path: str = DEFAULT_STATE_PATH) -> float:
    """Today's (UTC) reference equity for the daily-loss circuit
    breaker: the persisted value if a UTC day's entry already exists,
    otherwise `current_equity` becomes that reference point and is
    written back — the first cycle of each day sets the bar the rest of
    the day is measured against.
    """
    today = _today_utc()
    state = _read(state_path)
    if state is not None and state.get("day") == today:
        return float(state["starting_capital"])
    _write(state_path, {"day": today, "starting_capital": current_equity})
    return current_equity


def _read(state_path: str):
    if not os.path.exists(state_path):
        return None
    with open(state_path) as f:
        return json.load(f)


def _write(state_path: str, data: dict) -> None:
    directory = os.path.dirname(state_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(state_path, "w") as f:
        json.dump(data, f)
