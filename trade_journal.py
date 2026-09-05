"""Persists Binance's own trade history for SYMBOL to a local file,
built up incrementally across separate `main.py --trade` invocations —
so "how has the bot actually done, live" doesn't require reading
Binance's own trade history by hand or trusting logs/trading.log's
per-cycle prints to reconstruct it.

Deliberately just the raw fills (id, side, price, amount, cost, fee),
not round-trip P&L per position: pairing entries with exits into a
metrics report (win rate, total return, ...) needs real trades to
validate that pairing logic against first, and there are none yet.
Building that on zero live data would be guessing at a shape instead of
learning it — a natural follow-up once the bot has actually traded.

Best-effort by design, same posture as notifier.py: a journal update
failure (a transient fetch error surviving retry.call_with_retries, a
disk write error) must never fail the trading cycle itself. The caller
(main.py) is responsible for wrapping the call in a try/except; this
module raises normally rather than swallowing its own errors, so tests
can tell success from failure.
"""
import json
import os

from retry import call_with_retries

DEFAULT_JOURNAL_PATH = "data/trade_journal.json"

# ccxt's raw trade dict carries more than this repo has any use for
# (order ids, taker/maker flags, raw exchange-specific fields, ...) —
# keeping only what a human or a future report would actually read
# keeps the journal file readable and stable against ccxt adding
# fields upstream.
_KEPT_FIELDS = ("id", "timestamp", "datetime", "symbol", "side", "price", "amount", "cost", "fee")


def record_trades(exchange, symbol: str, journal_path: str = DEFAULT_JOURNAL_PATH) -> list:
    """Fetch recent trades for `symbol` and merge any not already in
    the local journal, deduped by Binance's own trade id (stable and
    unique per trade — safe to key on directly, unlike a
    timestamp+price+amount tuple which two separate fills could share).
    Returns the full, updated, timestamp-sorted list.
    """
    existing = _read(journal_path)
    existing_ids = {trade["id"] for trade in existing}

    recent = call_with_retries(exchange.fetch_my_trades, symbol, limit=50)
    new_trades = [_slim(trade) for trade in recent if trade.get("id") not in existing_ids]

    updated = existing + new_trades
    updated.sort(key=lambda trade: trade["timestamp"] or 0)
    _write(journal_path, updated)
    return updated


def read_journal(journal_path: str = DEFAULT_JOURNAL_PATH) -> list:
    """The currently-persisted journal, with no fetch -- for callers
    (risk_manager.consecutive_losses via main.py) that just need what's
    already on disk as of the start of this cycle, not a fresh sync.
    """
    return _read(journal_path)


def _slim(trade: dict) -> dict:
    return {field: trade.get(field) for field in _KEPT_FIELDS}


def _read(journal_path: str) -> list:
    if not os.path.exists(journal_path):
        return []
    with open(journal_path) as f:
        return json.load(f)


def _write(journal_path: str, trades: list) -> None:
    directory = os.path.dirname(journal_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(journal_path, "w") as f:
        json.dump(trades, f, indent=2)
