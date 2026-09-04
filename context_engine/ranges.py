"""Range position: premium, equilibrium or discount.

The rule this module exists to enforce: premium and discount are
meaningless without naming the range they refer to. Price can sit at a
discount of the weekly range and a premium of today's, and those are
not in conflict — they are answers to different questions. So the
range name always travels with the zone (master prompt section 10).

Buying at a discount and selling at a premium is the whole point;
equilibrium is where reward-to-risk is worst in both directions, which
is why mid-range entries end up on the no-trade list.
"""
import pandas as pd

from context_engine.params import (
    DISCOUNT_MAX,
    PREMIUM_MIN,
    SWING_RANGE_LOOKBACK,
)
from context_engine.schema import RangeState, Zone


def classify_zone(position_percent: float) -> Zone:
    if position_percent <= DISCOUNT_MAX:
        return Zone.DISCOUNT
    if position_percent >= PREMIUM_MIN:
        return Zone.PREMIUM
    return Zone.EQUILIBRIUM


def range_position(name: str, high: float, low: float, price: float) -> RangeState:
    """Where `price` sits inside [low, high], as a named range.

    A degenerate range (high == low, e.g. a brand-new session with one
    candle) is reported as equilibrium at 50%: no information, rather
    than a division by zero or a false extreme.
    """
    span = high - low
    if span <= 0:
        return RangeState(
            name=name,
            high=float(high),
            low=float(low),
            position_percent=50.0,
            zone=Zone.EQUILIBRIUM,
        )

    # Clamped: price can trade outside the reference range, and
    # "130% of the daily range" is not a position anyone can act on.
    position = (price - low) / span * 100
    position = max(0.0, min(100.0, position))

    return RangeState(
        name=name,
        high=float(high),
        low=float(low),
        position_percent=round(float(position), 2),
        zone=classify_zone(position),
    )


def timeframe_range(
    df: pd.DataFrame,
    name: str,
    lookback: int = SWING_RANGE_LOOKBACK,
) -> RangeState:
    """Range of the last `lookback` bars of one timeframe."""
    if df is None or df.empty:
        return RangeState(name=name, high=0.0, low=0.0, position_percent=50.0, zone=Zone.EQUILIBRIUM)

    window = df.tail(lookback)
    return range_position(
        name=name,
        high=float(window["high"].max()),
        low=float(window["low"].min()),
        price=float(df["close"].iloc[-1]),
    )


def analyze_ranges(frames: dict, execution_timeframe: str = "1h") -> dict:
    """Named ranges the context reports on, keyed by name.

    Weekly and daily give the higher-timeframe read; the swing range
    describes the leg currently being traded.
    """
    execution = frames.get(execution_timeframe)
    if execution is None or execution.empty:
        return {}

    price = float(execution["close"].iloc[-1])
    ranges = {}

    for timeframe, name, lookback in (("1w", "weekly", 4), ("1d", "daily", 5)):
        frame = frames.get(timeframe)
        if frame is None or frame.empty:
            continue
        window = frame.tail(lookback)
        ranges[name] = range_position(
            name=name,
            high=float(window["high"].max()),
            low=float(window["low"].min()),
            price=price,
        )

    ranges["swing"] = timeframe_range(execution, "swing")
    return ranges


def primary_range(ranges: dict) -> RangeState:
    """The range the snapshot leads with.

    Daily first: it is the reference most decisions are framed against.
    Weekly and then the swing range are fallbacks when there is not
    enough history for a daily one.
    """
    for name in ("daily", "weekly", "swing"):
        if name in ranges:
            return ranges[name]
    return RangeState(name="none", high=0.0, low=0.0, position_percent=50.0, zone=Zone.EQUILIBRIUM)
