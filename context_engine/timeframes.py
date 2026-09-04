"""Timezone normalization and timeframe resampling.

Why normalization lives here and not in data_fetcher: fetch_ohlcv()
returns timezone-*naive* timestamps, and changing that would alter the
`isoformat()` strings backtester.export_report writes, which the
dashboard parses with `new Date(iso)` — a naive string is read as
local time, an offset-carrying one as UTC. Fixing it at the source
would silently shift every candle on the existing charts. So the
context engine localizes on the way in and leaves the existing export
path untouched.

Why resample instead of fetching each timeframe separately: five
paginated fetches can disagree at the edges (a partially-formed 4h bar
from one call, a complete one from another), and reconciling that is a
subtle source of look-ahead. Aggregating one base series upward makes
every timeframe consistent by construction.
"""
import pandas as pd

from context_engine.params import RESAMPLE_RULES, TIMEFRAMES

OHLCV_AGGREGATION = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
}


def ensure_utc(df: pd.DataFrame) -> pd.DataFrame:
    """Return `df` with a UTC-aware, sorted, deduplicated DatetimeIndex.

    Naive timestamps are *localized* (not converted) to UTC, because
    that is what Binance returns: epoch milliseconds, which are UTC by
    definition — data_fetcher just drops the marker.
    """
    if not isinstance(df.index, pd.DatetimeIndex):
        raise TypeError(f"expected a DatetimeIndex, got {type(df.index).__name__}")

    out = df.copy()
    if out.index.tz is None:
        out.index = out.index.tz_localize("UTC")
    else:
        out.index = out.index.tz_convert("UTC")

    out = out[~out.index.duplicated(keep="first")]
    return out.sort_index()


def resample_ohlcv(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Aggregate `df` up to `timeframe` on UTC-anchored bin boundaries.

    Only aggregates upward — asking for 15m bars from hourly candles
    would have to invent price action inside the hour, so it raises
    instead. Bins are labelled with their *opening* timestamp, matching
    the exchange convention data_fetcher already returns.
    """
    if timeframe not in RESAMPLE_RULES:
        raise ValueError(f"unknown timeframe {timeframe!r}; expected one of {sorted(RESAMPLE_RULES)}")

    frame = ensure_utc(df)
    if frame.empty:
        return frame

    columns = [c for c in OHLCV_AGGREGATION if c in frame.columns]
    aggregation = {c: OHLCV_AGGREGATION[c] for c in columns}

    resampled = frame.resample(RESAMPLE_RULES[timeframe], label="left", closed="left").agg(aggregation)
    # Bins with no source candles come back as all-NaN rows (a market
    # halt, or simply a base series that does not cover that span).
    # They are absences, not zero-volume bars, so drop them.
    return resampled.dropna(subset=["close"])


def build_timeframe_set(
    df: pd.DataFrame,
    timeframes: tuple = TIMEFRAMES,
    base_timeframe: str = "1h",
) -> dict:
    """Build {timeframe: DataFrame} by aggregating one base series.

    Timeframes finer than `base_timeframe` are skipped rather than
    faked: a 15m view simply does not exist in hourly data, and the
    engine treats a missing timeframe as unknown instead of guessing.
    """
    base = ensure_utc(df)
    base_minutes = _timeframe_minutes(base_timeframe)

    frames = {}
    for timeframe in timeframes:
        if _timeframe_minutes(timeframe) < base_minutes:
            continue
        resampled = resample_ohlcv(base, timeframe)
        if not resampled.empty:
            frames[timeframe] = resampled
    return frames


def slice_until(df: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Candles up to and including `as_of`.

    The single choke point for "what was knowable at time T". A bar is
    included when its opening timestamp is <= as_of, which for the
    bar containing as_of means it may still be forming — callers that
    need only closed bars pass the last closed timestamp.
    """
    frame = ensure_utc(df)
    if as_of is None:
        return frame
    timestamp = pd.Timestamp(as_of)
    if timestamp.tz is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return frame[frame.index <= timestamp]


def slice_frames_until(frames: dict, as_of: pd.Timestamp) -> dict:
    """Apply slice_until across a {timeframe: DataFrame} mapping."""
    return {timeframe: slice_until(df, as_of) for timeframe, df in frames.items()}


def _timeframe_minutes(timeframe: str) -> int:
    from context_engine.params import TIMEFRAME_MINUTES

    if timeframe not in TIMEFRAME_MINUTES:
        raise ValueError(f"unknown timeframe {timeframe!r}")
    return TIMEFRAME_MINUTES[timeframe]
