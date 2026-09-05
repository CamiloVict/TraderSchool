"""Persists a snapshot of the account's real balance (every non-zero
currency, not just SYMBOL's) on each `main.py --trade` cycle, appended
to a local file the dashboard reads to answer "what do I actually
hold, and how has it changed" -- the same real-account counterpart to
trade_journal.py, but for balance instead of fills.

One file for the whole account, not one per bot/symbol: both the PAXG
and BTC bots (see README's "Automatizarlo") trade the same Binance
account, so a balance snapshot is account-level regardless of which
bot's cron happened to trigger it -- there is nothing to key it by.

Deliberately just a raw snapshot per cycle (timestamp + per-currency
total), not a computed USD-equivalent total: that conversion needs a
price for every currency held, which this module has no way to know
for currencies outside whatever SYMBOL the calling cycle trades.
Pricing what it can is the dashboard's job (it already has each
symbol's candle history loaded).

Best-effort by design, same posture as trade_journal.py and
notifier.py: a snapshot failure (a transient fetch error surviving
retry.call_with_retries, a disk write error) must never fail the
trading cycle itself. The caller (main.py) is responsible for wrapping
the call in a try/except; this module raises normally rather than
swallowing its own errors, so tests can tell success from failure.
"""
import json
import os
from datetime import datetime, timezone

from retry import call_with_retries

DEFAULT_SNAPSHOT_PATH = "data/balance_history.json"


def record_balance(exchange, snapshot_path: str = DEFAULT_SNAPSHOT_PATH) -> dict:
    """Fetch the account's current balance and append a snapshot (only
    non-zero currencies) to `snapshot_path`. Returns the snapshot just
    recorded.

    Appends unconditionally rather than deduping like trade_journal.py
    does by trade id: there is no natural identity for a balance
    snapshot besides its own timestamp, and each cycle is already at
    most one snapshot (no risk of the same snapshot being re-fetched
    and double-counted the way overlapping trade history is).
    """
    balance = call_with_retries(exchange.fetch_balance)
    totals = balance.get("total", {}) or {}
    non_zero = {asset: amount for asset, amount in totals.items() if amount}

    snapshot = {
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
        "datetime": datetime.now(timezone.utc).isoformat(),
        "balances": non_zero,
    }

    history = _read(snapshot_path)
    history.append(snapshot)
    _write(snapshot_path, history)
    return snapshot


def _read(snapshot_path: str) -> list:
    if not os.path.exists(snapshot_path):
        return []
    with open(snapshot_path) as f:
        return json.load(f)


def _write(snapshot_path: str, history: list) -> None:
    directory = os.path.dirname(snapshot_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(snapshot_path, "w") as f:
        json.dump(history, f, indent=2)
