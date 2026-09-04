"""Fetch OHLCV market data from Binance (Testnet by default) via ccxt.

Only market-data functionality lives here (Phase 1 scope). No orders
are placed from this module.
"""
import ccxt
import pandas as pd

from config import (
    BINANCE_API_KEY,
    BINANCE_API_SECRET,
    SYMBOL,
    TIMEFRAME,
    USE_TESTNET,
)

OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def get_exchange() -> ccxt.binance:
    """Build a ccxt Binance client, pointed at the Testnet unless disabled."""
    exchange = ccxt.binance(
        {
            "apiKey": BINANCE_API_KEY,
            "secret": BINANCE_API_SECRET,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        }
    )
    if USE_TESTNET:
        exchange.set_sandbox_mode(True)
    return exchange


def _to_dataframe(raw_candles: list) -> pd.DataFrame:
    df = pd.DataFrame(raw_candles, columns=OHLCV_COLUMNS)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    return df


def fetch_ohlcv(
    exchange: ccxt.binance = None,
    symbol: str = SYMBOL,
    timeframe: str = TIMEFRAME,
    limit: int = 500,
    since: int = None,
) -> pd.DataFrame:
    """Fetch up to `limit` recent candles as a DataFrame indexed by timestamp."""
    exchange = exchange or get_exchange()
    raw_candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit, since=since)
    return _to_dataframe(raw_candles)


def fetch_ohlcv_history(
    exchange: ccxt.binance,
    symbol: str = SYMBOL,
    timeframe: str = TIMEFRAME,
    since_ms: int = None,
    limit_per_call: int = 1000,
) -> pd.DataFrame:
    """Page through Binance's OHLCV endpoint to build a longer history.

    Needed for backtester.py, which will want months of candles rather
    than the single-call `limit` cap Binance enforces (1000 per request).
    """
    all_rows: list = []
    cursor = since_ms
    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=limit_per_call)
        if not batch:
            break
        all_rows.extend(batch)
        last_ts = batch[-1][0]
        if cursor is not None and last_ts <= cursor:
            break
        cursor = last_ts + 1
        if len(batch) < limit_per_call:
            break

    df = _to_dataframe(all_rows)
    return df[~df.index.duplicated(keep="first")]
