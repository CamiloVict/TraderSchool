"""Persists the trading week's starting equity across separate
`main.py --trade` invocations -- the weekly counterpart to
daily_loss_state.py. See that module's own docstring for why this
file-backed layer exists at all (risk_manager's trackers are in-memory
only, and main.py --trade is a brand-new process every cron tick).

Week boundary is the ISO week (Monday-start, UTC), matching
risk_manager.WeeklyLossTracker's own rollover rule.

Deliberately tiny: one float and one (year, week) key, one file. Same
accepted simplification as daily_loss_state.py -- revisit before real
capital.
"""
import json
import os
from datetime import datetime, timezone

DEFAULT_STATE_PATH = "data/weekly_loss_state.json"


def _current_week_utc() -> str:
    iso_year, iso_week, _ = datetime.now(timezone.utc).isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def load_or_init_starting_capital(current_equity: float, state_path: str = DEFAULT_STATE_PATH) -> float:
    """This week's (ISO, UTC) reference equity for the weekly-loss
    circuit breaker: the persisted value if this week's entry already
    exists, otherwise `current_equity` becomes that reference point and
    is written back -- the first cycle of each week sets the bar the
    rest of the week is measured against.
    """
    week = _current_week_utc()
    state = _read(state_path)
    if state is not None and state.get("week") == week:
        return float(state["starting_capital"])
    _write(state_path, {"week": week, "starting_capital": current_equity})
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
