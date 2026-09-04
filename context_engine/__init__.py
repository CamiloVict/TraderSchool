"""Deterministic Daily Market Context engine.

Turns raw OHLCV into a structured, falsifiable description of what the
market is doing: regime, multi-timeframe bias, structure, liquidity,
volatility, range position and session state, plus the invalidation
that would kill the hypothesis.

Everything here is deterministic and pure — the same candles always
produce the same snapshot, and no function reads the clock, the
network or the filesystem. That is what makes the output backtestable
and what keeps an LLM (if one is ever added on top) firmly in the role
of interpreter rather than calculator.

Usage:

    from context_engine import build_context, build_timeframe_set

    frames = build_timeframe_set(hourly_df)
    snapshot = build_context(frames, asset="BTC/USDT")
    print(snapshot.to_dict())
"""
from context_engine.engine import build_context
from context_engine.params import CONTEXT_ENGINE_VERSION, WEIGHTS_VERSION
from context_engine.timeframes import build_timeframe_set, ensure_utc, resample_ohlcv

__all__ = [
    "CONTEXT_ENGINE_VERSION",
    "WEIGHTS_VERSION",
    "build_context",
    "build_timeframe_set",
    "ensure_utc",
    "resample_ohlcv",
]
