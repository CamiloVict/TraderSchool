"""Classical reversal chart-pattern detection: double-top/bottom,
head-and-shoulders/inverse, and triangles (ascending/descending/
symmetric).

Scope, deliberately narrow (see the feasibility report this responds
to): chart patterns are historical pattern-matching over past price,
same family as the EMA crossover in strategy.py — not a forward-looking
"theoretical" model. The academic evidence for them is weak on
intraday data and, even on daily data, supports them only as a
confirmation signal, not a stand-alone strategy (Savin, Weller &
Zvingelis 2007). So here they're wired in as exactly that: a veto on
new EMA-crossover entries, not a replacement for the EMA signal or an
independent source of trades.

Pivot detection is reused from context_engine.structure.find_swings
rather than reimplemented here — its swings carry `confirmed_at`
(when a pivot becomes *knowable*, not just when it printed), and this
module's confirmation search respects that: a pattern can't confirm on
a candle before both of its defining swings were actually confirmed,
even if price had already crossed the breakout level by then. Skipping
that check is exactly the look-ahead bug context_engine's own tests
guard against, and an earlier version of this module had it.
"""
import numpy as np
import pandas as pd

from context_engine.schema import SwingKind
from context_engine.structure import find_swings

# How many candles a confirmed bearish pattern's veto blocks new long
# entries for, before it's considered stale.
PATTERN_VETO_LOOKBACK = 10


def _confirm(
    closes: pd.Series,
    start_pos: int,
    not_before_pos: int,
    slope: float,
    intercept: float,
    breakout_below: bool,
    max_confirm_candles: int,
):
    """First candle position (>= not_before_pos, > start_pos) whose
    close breaks the line `slope * position + intercept`, within
    `max_confirm_candles` of start_pos — or None if it never does. A
    fixed price threshold is just the slope=0 case: the double-top/H&S
    checks below use it that way instead of a separate helper, so a
    flat neckline and a sloped triangle boundary confirm through
    exactly the same logic."""
    n = len(closes)
    begin = max(start_pos + 1, not_before_pos)
    end = min(n, start_pos + 1 + max_confirm_candles)
    for j in range(begin, end):
        threshold = _line_value(slope, intercept, j)
        price = closes.iloc[j]
        if (breakout_below and price < threshold) or (not breakout_below and price > threshold):
            return j
    return None


def _fit_line(points: list) -> tuple:
    """(slope, intercept) of the least-squares line through
    [(position, price), ...] — exact for exactly 2 points, which is
    all a classical trendline needs (two touches define it)."""
    xs = [p for p, _ in points]
    ys = [v for _, v in points]
    slope, intercept = np.polyfit(xs, ys, 1)
    return float(slope), float(intercept)


def _line_value(slope: float, intercept: float, x: int) -> float:
    return slope * x + intercept


def _is_flat(slope: float, reference_price: float, span_candles: int, flat_tolerance_pct: float) -> bool:
    """True if the line moves less than `flat_tolerance_pct` of
    `reference_price` over its span — reads as horizontal
    support/resistance rather than a sloped trendline."""
    if reference_price <= 0:
        return False
    total_move_pct = abs(slope) * span_candles / reference_price * 100
    return total_move_pct < flat_tolerance_pct


def detect_reversal_patterns(
    df: pd.DataFrame,
    pivot_window: int = 5,
    tolerance_pct: float = 1.5,
    min_depth_pct: float = 1.0,
    max_confirm_candles: int = 20,
    triangle_flat_tolerance_pct: float = 1.5,
    triangle_max_span_candles: int = 80,
) -> pd.Series:
    """Detect double-top/double-bottom, head-and-shoulders/inverse, and
    triangle patterns in `df` (needs 'high', 'low', 'close' columns).

    Returns a Series aligned to df.index: -1 on the candle where a
    bearish break confirms (double-top, H&S neckline, descending
    triangle support, or a symmetric triangle breaking down), +1 where
    a bullish one does (double-bottom, inverse H&S, ascending triangle
    resistance, or a symmetric triangle breaking up), 0 elsewhere.

    - `tolerance_pct`: how close two peaks/troughs (the shoulders, or
      a double-top/bottom's two extremes) must be to count as "the
      same level".
    - `min_depth_pct`: how deep/tall the intervening pullback(s) must
      be, relative to the pattern's extremes — filters out noise that
      technically has the right shape but no real pattern.
    - `max_confirm_candles`: a setup that never breaks its neckline (or
      triangle boundary) within this many candles of completing is
      discarded rather than left signaling indefinitely.
    - `triangle_flat_tolerance_pct` / `triangle_max_span_candles`: how
      horizontal a triangle boundary must read to count as "flat", and
      how many candles its four defining pivots may span.
    """
    n = len(df)
    signal = pd.Series(0, index=df.index)
    if n < pivot_window * 2 + 3:
        return signal

    swings = sorted(find_swings(df, left=pivot_window, right=pivot_window), key=lambda s: s.timestamp)
    closes = df["close"]
    highs = [s for s in swings if s.kind is SwingKind.HIGH]
    lows = [s for s in swings if s.kind is SwingKind.LOW]

    # The triangle scan below is O(highs x lows); resolving each
    # timestamp with df.index.get_loc() inside that loop dominated
    # runtime on a year of hourly data (~1000 swings). A precomputed
    # dict turns every lookup into O(1) — every timestamp queried below
    # is either a swing's own (already a key) or its confirmed_at
    # (always some other candle's own timestamp, so also a key).
    _positions = {timestamp.isoformat(): i for i, timestamp in enumerate(df.index)}

    def pos(timestamp: str) -> int:
        return _positions[timestamp]

    def confirm(start_pos, not_before_pos, threshold, breakout_below):
        return _confirm(closes, start_pos, not_before_pos, 0.0, threshold, breakout_below, max_confirm_candles)

    def confirm_line(start_pos, not_before_pos, slope, intercept, breakout_below):
        return _confirm(closes, start_pos, not_before_pos, slope, intercept, breakout_below, max_confirm_candles)

    # --- Double top / double bottom -----------------------------------
    for s1, s2 in zip(highs, highs[1:]):
        if abs(s1.price - s2.price) / ((s1.price + s2.price) / 2) > tolerance_pct / 100:
            continue
        p1, p2 = pos(s1.timestamp), pos(s2.timestamp)
        between = [low for low in lows if p1 < pos(low.timestamp) < p2]
        if not between:
            continue
        valley = min(between, key=lambda s: s.price)
        depth = (min(s1.price, s2.price) - valley.price) / min(s1.price, s2.price)
        if depth < min_depth_pct / 100:
            continue
        not_before = pos(max(s2.confirmed_at, valley.confirmed_at))
        confirm_pos = confirm(p2, not_before, valley.price, breakout_below=True)
        if confirm_pos is not None:
            signal.iloc[confirm_pos] = -1

    for s1, s2 in zip(lows, lows[1:]):
        if abs(s1.price - s2.price) / ((s1.price + s2.price) / 2) > tolerance_pct / 100:
            continue
        p1, p2 = pos(s1.timestamp), pos(s2.timestamp)
        between = [high for high in highs if p1 < pos(high.timestamp) < p2]
        if not between:
            continue
        peak = max(between, key=lambda s: s.price)
        rise = (peak.price - max(s1.price, s2.price)) / max(s1.price, s2.price)
        if rise < min_depth_pct / 100:
            continue
        not_before = pos(max(s2.confirmed_at, peak.confirmed_at))
        confirm_pos = confirm(p2, not_before, peak.price, breakout_below=False)
        if confirm_pos is not None:
            signal.iloc[confirm_pos] = 1

    # --- Head and shoulders / inverse -----------------------------------
    for left_shoulder, head, right_shoulder in zip(highs, highs[1:], highs[2:]):
        if not (head.price > left_shoulder.price and head.price > right_shoulder.price):
            continue
        if abs(left_shoulder.price - right_shoulder.price) / (
            (left_shoulder.price + right_shoulder.price) / 2
        ) > tolerance_pct / 100:
            continue
        p1, p2, p3 = pos(left_shoulder.timestamp), pos(head.timestamp), pos(right_shoulder.timestamp)
        left_troughs = [low for low in lows if p1 < pos(low.timestamp) < p2]
        right_troughs = [low for low in lows if p2 < pos(low.timestamp) < p3]
        if not left_troughs or not right_troughs:
            continue
        left_trough = min(left_troughs, key=lambda s: s.price)
        right_trough = min(right_troughs, key=lambda s: s.price)
        neckline = (left_trough.price + right_trough.price) / 2
        depth = (head.price - max(left_trough.price, right_trough.price)) / head.price
        if depth < min_depth_pct / 100:
            continue
        not_before = pos(max(right_shoulder.confirmed_at, right_trough.confirmed_at))
        confirm_pos = confirm(p3, not_before, neckline, breakout_below=True)
        if confirm_pos is not None:
            signal.iloc[confirm_pos] = -1

    for left_shoulder, head, right_shoulder in zip(lows, lows[1:], lows[2:]):
        if not (head.price < left_shoulder.price and head.price < right_shoulder.price):
            continue
        if abs(left_shoulder.price - right_shoulder.price) / (
            (left_shoulder.price + right_shoulder.price) / 2
        ) > tolerance_pct / 100:
            continue
        p1, p2, p3 = pos(left_shoulder.timestamp), pos(head.timestamp), pos(right_shoulder.timestamp)
        left_peaks = [high for high in highs if p1 < pos(high.timestamp) < p2]
        right_peaks = [high for high in highs if p2 < pos(high.timestamp) < p3]
        if not left_peaks or not right_peaks:
            continue
        left_peak = max(left_peaks, key=lambda s: s.price)
        right_peak = max(right_peaks, key=lambda s: s.price)
        neckline = (left_peak.price + right_peak.price) / 2
        rise = (min(left_peak.price, right_peak.price) - head.price) / head.price
        if rise < min_depth_pct / 100:
            continue
        not_before = pos(max(right_shoulder.confirmed_at, right_peak.confirmed_at))
        confirm_pos = confirm(p3, not_before, neckline, breakout_below=False)
        if confirm_pos is not None:
            signal.iloc[confirm_pos] = 1

    # --- Triangles (ascending / descending / symmetric) -----------------
    # A triangle needs two touches on each side to draw its two
    # boundary lines: the last two swing highs for the upper line, the
    # last two swing lows (up to the same point) for the lower one.
    # Shape depends on which line, if either, reads as flat:
    #   - flat top + rising bottom  -> ascending (watch the top for a
    #     bullish break)
    #   - falling top + flat bottom -> descending (watch the bottom for
    #     a bearish break)
    #   - falling top + rising bottom -> symmetric (watch both; whichever
    #     breaks first decides the direction)
    for h1, h2 in zip(highs, highs[1:]):
        ph1, ph2 = pos(h1.timestamp), pos(h2.timestamp)
        if ph2 - ph1 > triangle_max_span_candles:
            continue
        for l1, l2 in zip(lows, lows[1:]):
            pl1, pl2 = pos(l1.timestamp), pos(l2.timestamp)
            if pl2 - pl1 > triangle_max_span_candles:
                continue
            # The two lines' point-spans must genuinely overlap —
            # otherwise this is two unrelated structures, not one
            # converging shape.
            if not (pl1 < ph2 and ph1 < pl2):
                continue

            span = max(ph2, pl2) - min(ph1, pl1)
            if span < pivot_window * 2 or span > triangle_max_span_candles:
                continue

            reference_price = (h1.price + h2.price + l1.price + l2.price) / 4
            high_slope, high_intercept = _fit_line([(ph1, h1.price), (ph2, h2.price)])
            low_slope, low_intercept = _fit_line([(pl1, l1.price), (pl2, l2.price)])
            high_flat = _is_flat(high_slope, reference_price, span, triangle_flat_tolerance_pct)
            low_flat = _is_flat(low_slope, reference_price, span, triangle_flat_tolerance_pct)

            ascending = high_flat and not low_flat and low_slope > 0
            descending = low_flat and not high_flat and high_slope < 0
            symmetric = not high_flat and not low_flat and high_slope < 0 and low_slope > 0
            if not (ascending or descending or symmetric):
                continue

            last_pos = max(ph2, pl2)
            upper_now = _line_value(high_slope, high_intercept, last_pos)
            lower_now = _line_value(low_slope, low_intercept, last_pos)
            if upper_now <= lower_now:
                continue  # lines already crossed: not a still-forming triangle

            # A "flat" boundary within tolerance is easy to satisfy by
            # chance on a noisy, low-amplitude series — two lines can
            # both read as flat and still be sitting right on top of
            # each other. Require the two lines to actually start out
            # meaningfully apart, the same amplitude bar double-top/
            # bottom and H&S already apply via min_depth_pct.
            start_pos = min(ph1, pl1)
            gap_at_start = _line_value(high_slope, high_intercept, start_pos) - _line_value(
                low_slope, low_intercept, start_pos
            )
            if gap_at_start / reference_price < min_depth_pct / 100:
                continue

            not_before = pos(max(h2.confirmed_at, l2.confirmed_at))

            if ascending:
                confirm_pos = confirm_line(last_pos, not_before, high_slope, high_intercept, breakout_below=False)
                if confirm_pos is not None:
                    signal.iloc[confirm_pos] = 1
            elif descending:
                confirm_pos = confirm_line(last_pos, not_before, low_slope, low_intercept, breakout_below=True)
                if confirm_pos is not None:
                    signal.iloc[confirm_pos] = -1
            else:  # symmetric: whichever boundary breaks first wins
                begin = max(last_pos + 1, not_before)
                end = min(n, last_pos + 1 + max_confirm_candles)
                for j in range(begin, end):
                    price = closes.iloc[j]
                    if price > _line_value(high_slope, high_intercept, j):
                        signal.iloc[j] = 1
                        break
                    if price < _line_value(low_slope, low_intercept, j):
                        signal.iloc[j] = -1
                        break

    return signal


def bearish_veto_mask(pattern_signal: pd.Series, lookback: int = PATTERN_VETO_LOOKBACK) -> pd.Series:
    """True on any candle within `lookback` candles (inclusive) of a
    confirmed bearish pattern — used to block new long entries, since a
    reversal pattern argues against the uptrend an EMA crossover just
    signaled."""
    is_bearish = pattern_signal.eq(-1)
    return is_bearish.rolling(lookback, min_periods=1).max().astype(bool)
