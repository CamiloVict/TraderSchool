"""Liquidity: where resting orders pile up, and what happens when
price reaches them.

Two kinds of level:

  - Temporal (PDH/PDL, PWH/PWL, PMH/PML). Obvious reference points that
    a large share of participants watch, so stops cluster just beyond
    them.
  - Structural (swing highs/lows, equal highs/lows). Equal highs are
    the strongest form: two rejections from the same price leave a
    visible shelf of stops above it.

The event that matters is the *sweep*: price trades through a level and
then closes back inside. That is the signature of stops being filled
without the market being able to hold the new ground.

A sweep is never an entry by itself. This module reports the event,
whether the level was reclaimed and whether the move back showed
displacement; combining that with structure and context is somebody
else's job (master prompt section 8).
"""
import pandas as pd

from context_engine.features import atr, last_value
from context_engine.params import (
    DISPLACEMENT_ATR_MULTIPLE,
    EQUAL_LEVEL_ATR_TOLERANCE,
    EQUAL_LEVEL_LOOKBACK,
    SWEEP_LOOKBACK,
    SWEEP_RECLAIM_BARS,
)
from context_engine.schema import (
    LiquidityEvent,
    LiquidityEventKind,
    LiquidityLevel,
    LiquidityState,
    SwingKind,
)

# Which resampled timeframe supplies each temporal level, and the
# label it gets in the snapshot.
TEMPORAL_LEVELS = (
    ("1d", "PDH", "PDL"),
    ("1w", "PWH", "PWL"),
)


def previous_period_levels(frames: dict, as_of=None) -> list:
    """Previous day's/week's high and low.

    Uses the *previous completed* bar, never the one in progress: the
    current day's high is still moving, so treating it as a fixed level
    would be reading the future.
    """
    levels = []
    for timeframe, high_name, low_name in TEMPORAL_LEVELS:
        frame = frames.get(timeframe)
        if frame is None or len(frame) < 2:
            continue

        window = frame if as_of is None else frame[frame.index <= pd.Timestamp(_to_iso(as_of))]
        if len(window) < 2:
            continue

        previous = window.iloc[-2]
        levels.append(
            LiquidityLevel(name=high_name, price=float(previous["high"]), kind=SwingKind.HIGH)
        )
        levels.append(
            LiquidityLevel(name=low_name, price=float(previous["low"]), kind=SwingKind.LOW)
        )
    return levels


def monthly_levels(daily: pd.DataFrame, as_of=None) -> list:
    """Previous month's high and low, derived from daily candles.

    Months are not in the standard timeframe set (nothing else needs
    them), so they are aggregated on demand here.
    """
    if daily is None or daily.empty:
        return []

    window = daily if as_of is None else daily[daily.index <= pd.Timestamp(_to_iso(as_of))]
    if window.empty:
        return []

    monthly = window.resample("MS").agg({"high": "max", "low": "min"}).dropna()
    if len(monthly) < 2:
        return []

    previous = monthly.iloc[-2]
    return [
        LiquidityLevel(name="PMH", price=float(previous["high"]), kind=SwingKind.HIGH),
        LiquidityLevel(name="PML", price=float(previous["low"]), kind=SwingKind.LOW),
    ]


def find_equal_levels(
    df: pd.DataFrame,
    tolerance_atr: float = EQUAL_LEVEL_ATR_TOLERANCE,
    lookback: int = EQUAL_LEVEL_LOOKBACK,
) -> tuple:
    """(equal_highs, equal_lows) within `lookback` bars.

    "Equal" means within a fraction of ATR, not exactly identical —
    two highs a tick apart are the same shelf of stops as far as the
    market is concerned. Scaling by ATR keeps the tolerance meaningful
    across assets and volatility regimes.
    """
    if df is None or len(df) < 3:
        return [], []

    window = df.tail(lookback)
    reference_atr = last_value(atr(df))
    if not reference_atr:
        return [], []

    tolerance = reference_atr * tolerance_atr
    return (
        _cluster(window["high"].tolist(), tolerance),
        _cluster(window["low"].tolist(), tolerance),
    )


def detect_sweeps(
    df: pd.DataFrame,
    levels: list,
    reclaim_bars: int = SWEEP_RECLAIM_BARS,
    lookback: int = SWEEP_LOOKBACK,
) -> list:
    """Sweep events among the last `lookback` candles.

    For each level, find candles that pierced it with a wick, then look
    at the following `reclaim_bars` closes:

      - closed back inside  -> SWEEP (the stops were the point)
      - stayed beyond       -> LIQUIDITY_EXPANSION (a real breakout)

    A sweep is only emitted once its verdict is settled, so an event is
    never reported before the market has actually decided.
    """
    if df is None or len(df) < 3 or not levels:
        return []

    window = df.tail(lookback + reclaim_bars)
    atr_series = atr(df).reindex(window.index)
    events = []

    for level in levels:
        pierced = (
            window["high"] > level.price
            if level.kind is SwingKind.HIGH
            else window["low"] < level.price
        )
        if not bool(pierced.any()):
            continue

        # First touch only: later candles are trading beyond a level
        # that has already been taken, which is a different event.
        first_touch = window.index[pierced.to_numpy().argmax()]
        position = window.index.get_loc(first_touch)
        follow_up = window.iloc[position : position + reclaim_bars + 1]
        if len(follow_up) < 2:
            continue  # verdict still pending; do not guess it

        closes = follow_up["close"]
        reclaimed = bool(
            (closes < level.price).any()
            if level.kind is SwingKind.HIGH
            else (closes > level.price).any()
        )

        reference_atr = float(atr_series.get(first_touch) or 0.0)
        body = float((follow_up["close"] - follow_up["open"]).abs().max())
        displacement = reference_atr > 0 and body > reference_atr * DISPLACEMENT_ATR_MULTIPLE

        if reclaimed:
            kind = (
                LiquidityEventKind.SWEEP_HIGH
                if level.kind is SwingKind.HIGH
                else LiquidityEventKind.SWEEP_LOW
            )
        else:
            kind = LiquidityEventKind.LIQUIDITY_EXPANSION

        events.append(
            LiquidityEvent(
                kind=kind,
                level_name=level.name,
                level=level.price,
                occurred_at=first_touch.isoformat(),
                reclaimed=reclaimed,
                displacement=displacement,
            )
        )

    events.sort(key=lambda e: e.occurred_at)
    return events


def analyze_liquidity(
    frames: dict,
    execution_timeframe: str = "1h",
    as_of=None,
) -> LiquidityState:
    """Levels, pools and recent events for the whole context."""
    execution = frames.get(execution_timeframe)
    if execution is None or execution.empty:
        return LiquidityState()

    levels = previous_period_levels(frames, as_of=as_of)
    levels.extend(monthly_levels(frames.get("1d"), as_of=as_of))

    equal_highs, equal_lows = find_equal_levels(execution)
    events = detect_sweeps(execution, levels)

    # Mark which levels price has already traded through, so a reader
    # can tell untouched liquidity from spent liquidity.
    swept_names = {e.level_name for e in events}
    levels = [
        LiquidityLevel(
            name=level.name,
            price=level.price,
            kind=level.kind,
            swept=level.name in swept_names,
        )
        for level in levels
    ]

    return LiquidityState(
        levels=levels,
        equal_highs=equal_highs,
        equal_lows=equal_lows,
        events=events,
        recent_event=events[-1].kind if events else None,
    )


def _cluster(prices: list, tolerance: float) -> list:
    """Prices that repeat within `tolerance`, returned as the mean of
    each cluster of two or more."""
    if tolerance <= 0:
        return []

    clusters = []
    for price in sorted(prices):
        if clusters and abs(price - clusters[-1][-1]) <= tolerance:
            clusters[-1].append(price)
        else:
            clusters.append([price])

    return [round(sum(c) / len(c), 8) for c in clusters if len(c) > 1]


def _to_iso(value) -> str:
    if isinstance(value, str):
        return value
    timestamp = pd.Timestamp(value)
    if timestamp.tz is None:
        timestamp = timestamp.tz_localize("UTC")
    return timestamp.isoformat()
