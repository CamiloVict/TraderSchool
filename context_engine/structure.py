"""Market structure: swings, HH/HL/LH/LL, BOS, CHOCH and phase.

This is the module where look-ahead bias is easiest to introduce and
most damaging, because every downstream engine trusts it.

A swing high at bar `i` is only a swing once `right` bars have printed
to its right without exceeding it. At bar `i` itself nobody knows that
yet. So `find_swings` stamps each pivot with `confirmed_at` — the
timestamp of the bar that settled it — and `swings_known_at()` filters
on that, never on when the extreme printed. Backtests that skip this
step produce beautiful equity curves and lose money live.

Breaks follow the same discipline: a BOS requires a *close* beyond the
reference swing plus an ATR-scaled buffer. A wick through a level is
how liquidity gets taken, not how structure changes.
"""
import pandas as pd

from context_engine.features import atr, last_value
from context_engine.params import (
    BOS_ATR_BUFFER,
    STRUCTURE_SEQUENCE_LENGTH,
    SWING_LEFT,
    SWING_RIGHT,
)
from context_engine.schema import (
    BreakKind,
    Phase,
    StructureBreak,
    StructurePoint,
    StructureState,
    Swing,
    SwingKind,
    Trend,
)


def find_swings(
    df: pd.DataFrame,
    left: int = SWING_LEFT,
    right: int = SWING_RIGHT,
) -> list:
    """Confirmed swing pivots, oldest first.

    A swing high needs `left` lower highs before it and `right` lower
    highs after it. Ties count as "not exceeded" on the left and as
    "exceeded" on the right, so a flat double top confirms on the
    first candle rather than never resolving.

    The last `right` bars can never contain a confirmed swing — that is
    the honest cost of pivot detection, not a bug to be optimized away.
    """
    if df is None or len(df) < left + right + 1:
        return []

    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    index = df.index

    swings = []
    for i in range(left, len(df) - right):
        window_left = slice(i - left, i)
        window_right = slice(i + 1, i + right + 1)

        if highs[i] >= highs[window_left].max() and highs[i] > highs[window_right].max():
            swings.append(
                Swing(
                    timestamp=index[i].isoformat(),
                    price=float(highs[i]),
                    kind=SwingKind.HIGH,
                    confirmed_at=index[i + right].isoformat(),
                )
            )

        if lows[i] <= lows[window_left].min() and lows[i] < lows[window_right].min():
            swings.append(
                Swing(
                    timestamp=index[i].isoformat(),
                    price=float(lows[i]),
                    kind=SwingKind.LOW,
                    confirmed_at=index[i + right].isoformat(),
                )
            )

    swings.sort(key=lambda s: (s.confirmed_at, s.timestamp))
    return swings


def swings_known_at(swings: list, as_of) -> list:
    """Subset of `swings` already confirmed at `as_of`.

    The single guard against look-ahead in this module. Comparison is
    on ISO strings, which sort correctly because every timestamp is
    UTC-normalized upstream and therefore has an identical offset.
    """
    if as_of is None:
        return list(swings)
    cutoff = _to_iso(as_of)
    return [s for s in swings if s.confirmed_at <= cutoff]


def label_sequence(swings: list) -> list:
    """Label swings as HH/HL/LH/LL by comparing each to the previous
    swing of the same kind. The first of each kind has nothing to
    compare against and is skipped."""
    sequence = []
    previous_high = None
    previous_low = None

    for swing in sorted(swings, key=lambda s: s.timestamp):
        if swing.kind is SwingKind.HIGH:
            if previous_high is not None:
                sequence.append(
                    StructurePoint.HH if swing.price > previous_high else StructurePoint.LH
                )
            previous_high = swing.price
        else:
            if previous_low is not None:
                sequence.append(
                    StructurePoint.HL if swing.price > previous_low else StructurePoint.LL
                )
            previous_low = swing.price

    return sequence


def classify_trend(sequence: list) -> Trend:
    """Trend from the two most recent structure points.

    Higher highs *and* higher lows is an uptrend; the mirror is a
    downtrend; anything mixed is ranging. Deliberately short-memory —
    it is the recent sequence that describes the current leg, and a
    longer window would keep reporting UP well after the break.
    """
    if len(sequence) < 2:
        return Trend.UNDEFINED

    recent = sequence[-2:]
    if set(recent) <= {StructurePoint.HH, StructurePoint.HL}:
        return Trend.UP
    if set(recent) <= {StructurePoint.LL, StructurePoint.LH}:
        return Trend.DOWN
    return Trend.RANGING


def find_breaks(
    df: pd.DataFrame,
    swings: list,
    as_of=None,
    atr_buffer: float = BOS_ATR_BUFFER,
) -> list:
    """Structure breaks in chronological order.

    Walks candles forward, tracking the most recent confirmed swing
    high/low *as they were known at that moment*, and records a break
    when a candle closes beyond one by more than the ATR buffer.

    Whether a break is a BOS or a CHOCH depends on the trend it
    interrupts: breaking up while the last break was also up continues
    the structure (BOS); breaking up right after a bearish break is a
    change of character (CHOCH) — the first evidence the other side has
    lost control.
    """
    if df is None or df.empty or not swings:
        return []

    frame = df if as_of is None else df[df.index <= pd.Timestamp(_to_iso(as_of))]
    if frame.empty:
        return []

    atr_series = atr(frame)
    breaks = []
    last_direction = None

    # Pivots become usable only from their confirmation bar onward.
    pending = sorted(swings, key=lambda s: s.confirmed_at)
    cursor = 0
    active_high = None
    active_low = None

    for timestamp, row in frame.iterrows():
        stamp = timestamp.isoformat()
        while cursor < len(pending) and pending[cursor].confirmed_at <= stamp:
            swing = pending[cursor]
            if swing.kind is SwingKind.HIGH:
                active_high = swing
            else:
                active_low = swing
            cursor += 1

        buffer = (atr_series.get(timestamp, 0.0) or 0.0) * atr_buffer
        close = float(row["close"])

        if active_high is not None and close > active_high.price + buffer:
            kind = (
                BreakKind.BULLISH_CHOCH
                if last_direction == "DOWN"
                else BreakKind.BULLISH_BOS
            )
            breaks.append(
                StructureBreak(
                    kind=kind,
                    level=active_high.price,
                    broken_at=stamp,
                    reference_swing=active_high.timestamp,
                )
            )
            last_direction = "UP"
            # Consume the level so the same swing is not re-broken by
            # every subsequent candle that stays above it.
            active_high = None

        elif active_low is not None and close < active_low.price - buffer:
            kind = (
                BreakKind.BEARISH_CHOCH
                if last_direction == "UP"
                else BreakKind.BEARISH_BOS
            )
            breaks.append(
                StructureBreak(
                    kind=kind,
                    level=active_low.price,
                    broken_at=stamp,
                    reference_swing=active_low.timestamp,
                )
            )
            last_direction = "DOWN"
            active_low = None

    return breaks


def classify_phase(df: pd.DataFrame, trend: Trend, swings: list) -> Phase:
    """Where in its cycle the current leg is.

    Trend plus "is price advancing or retracing" gives pullback vs.
    impulse; without a trend, the ATR of the recent window against its
    own history separates compression from expansion.
    """
    if df is None or len(df) < 10:
        return Phase.UNDEFINED

    close = float(df["close"].iloc[-1])
    recent_high = last_swing_price(swings, SwingKind.HIGH)
    recent_low = last_swing_price(swings, SwingKind.LOW)

    if trend is Trend.UP and recent_high is not None:
        # Retracing from the last swing high while the uptrend stands.
        return Phase.PULLBACK if close < recent_high else Phase.IMPULSE
    if trend is Trend.DOWN and recent_low is not None:
        return Phase.PULLBACK if close > recent_low else Phase.IMPULSE

    atr_series = atr(df)
    current = last_value(atr_series)
    average = float(atr_series.tail(50).mean()) if len(atr_series.dropna()) else None
    if current is None or not average:
        return Phase.CONSOLIDATION

    if current > average * 1.2:
        return Phase.EXPANSION
    if current < average * 0.8:
        return Phase.COMPRESSION
    return Phase.CONSOLIDATION


def last_swing_price(swings: list, kind: SwingKind):
    """Price of the most recent swing of `kind`, by print time."""
    matching = [s for s in swings if s.kind is kind]
    if not matching:
        return None
    return max(matching, key=lambda s: s.timestamp).price


def analyze_structure(
    df: pd.DataFrame,
    as_of=None,
    left: int = SWING_LEFT,
    right: int = SWING_RIGHT,
    sequence_length: int = STRUCTURE_SEQUENCE_LENGTH,
) -> StructureState:
    """Full structural picture of one timeframe as known at `as_of`."""
    if df is None or df.empty:
        return StructureState(
            trend=Trend.UNDEFINED,
            sequence=[],
            last_bos=None,
            last_choch=None,
            phase=Phase.UNDEFINED,
            last_swing_high=None,
            last_swing_low=None,
            swings=[],
        )

    all_swings = find_swings(df, left=left, right=right)
    swings = swings_known_at(all_swings, as_of)
    sequence = label_sequence(swings)
    trend = classify_trend(sequence)
    breaks = find_breaks(df, swings, as_of=as_of)

    bos = [b for b in breaks if b.kind in (BreakKind.BULLISH_BOS, BreakKind.BEARISH_BOS)]
    choch = [b for b in breaks if b.kind in (BreakKind.BULLISH_CHOCH, BreakKind.BEARISH_CHOCH)]

    return StructureState(
        trend=trend,
        sequence=sequence[-sequence_length:],
        last_bos=bos[-1].kind if bos else None,
        last_choch=choch[-1].kind if choch else None,
        phase=classify_phase(df, trend, swings),
        last_swing_high=last_swing_price(swings, SwingKind.HIGH),
        last_swing_low=last_swing_price(swings, SwingKind.LOW),
        # Only the tail is carried into the snapshot: the full pivot
        # history would dwarf everything else in the JSON.
        swings=swings[-sequence_length:],
    )


def _to_iso(value) -> str:
    if isinstance(value, str):
        return value
    timestamp = pd.Timestamp(value)
    if timestamp.tz is None:
        timestamp = timestamp.tz_localize("UTC")
    return timestamp.isoformat()
