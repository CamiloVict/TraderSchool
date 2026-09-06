"""Moving-average crossover strategy.

Decision (explained to the user in chat, summarized here):
  - EMA (exponential), not SMA: weights recent candles more heavily,
    so the signal lags the actual price less. On a 1h chart that lag
    is the difference between catching a move and missing it.
  - Fast EMA = 20 periods (~20h), Slow EMA = 50 periods (~2 days).
    Fast enough to catch multi-day trends, slow enough to filter out
    most single-candle noise (a faster pair like 9/21 would trade far
    more often, with more false signals).
  - Long-only: BUY when the fast EMA crosses above the slow EMA
    (uptrend starting), SELL/EXIT when it crosses back below. No
    short-selling — a Spot account can't short natively, and it keeps
    the first version of the bot simple.

This module does not place any trades. It only labels each candle
with a `signal` (1 = be long, 0 = be flat) that backtester.py (and
later, the live loop) can act on.
"""
import pandas as pd

from context_engine.features import atr

FAST_PERIOD = 20
SLOW_PERIOD = 50


def add_signals(df: pd.DataFrame, fast: int = FAST_PERIOD, slow: int = SLOW_PERIOD) -> pd.DataFrame:
    """Return a copy of `df` (must have 'high'/'low'/'close' columns)
    with EMA columns, a `signal` column (1 = long, 0 = flat) from an
    EMA(fast)/EMA(slow) crossover, and a `trend_strength` column.

    `trend_strength` = |ema_fast - ema_slow| / ATR: how far apart the
    two EMAs actually are at the crossover, in volatility-normalized
    terms, not just "did they cross." A crossover with the EMAs still
    right on top of each other is exactly what a ranging/choppy market
    produces -- price oscillates back and forth across both averages
    without a real trend behind it, generating a signal that reverses
    again a few candles later (a whipsaw). This column always gets
    computed (cheap, reuses the EMAs already on hand); whether a
    caller actually gates entries on it is
    config.USE_TREND_STRENGTH_FILTER's decision, not this function's --
    see backtester.py's own use_trend_strength_filter and main.py's
    entry_blocked_by_weak_trend for where that veto is applied.

    Note: the first `slow` rows are an EMA warm-up period and aren't a
    reliable signal yet — callers (e.g. backtester.py) should skip them.
    """
    out = df.copy()
    out["ema_fast"] = out["close"].ewm(span=fast, adjust=False).mean()
    out["ema_slow"] = out["close"].ewm(span=slow, adjust=False).mean()
    out["signal"] = (out["ema_fast"] > out["ema_slow"]).astype(int)
    out["trend_strength"] = (out["ema_fast"] - out["ema_slow"]).abs() / atr(out)
    return out
