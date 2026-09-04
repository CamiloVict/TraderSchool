"""Classical reversal chart-pattern detection: double-top/bottom and
head-and-shoulders/inverse.

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
import pandas as pd

from context_engine.schema import SwingKind
from context_engine.structure import find_swings

# How many candles a confirmed bearish pattern's veto blocks new long
# entries for, before it's considered stale.
PATTERN_VETO_LOOKBACK = 10


def _confirm(closes: pd.Series, start_pos: int, not_before_pos: int, threshold: float, breakout_below: bool, max_confirm_candles: int):
    """First candle position (>= not_before_pos, > start_pos) whose
    close breaks `threshold`, within `max_confirm_candles` of
    start_pos — or None if it never does."""
    n = len(closes)
    begin = max(start_pos + 1, not_before_pos)
    end = min(n, start_pos + 1 + max_confirm_candles)
    for j in range(begin, end):
        price = closes.iloc[j]
        if (breakout_below and price < threshold) or (not breakout_below and price > threshold):
            return j
    return None


def detect_reversal_patterns(
    df: pd.DataFrame,
    pivot_window: int = 5,
    tolerance_pct: float = 1.5,
    min_depth_pct: float = 1.0,
    max_confirm_candles: int = 20,
) -> pd.Series:
    """Detect double-top/double-bottom and head-and-shoulders/inverse
    patterns in `df` (needs 'high', 'low', 'close' columns).

    Returns a Series aligned to df.index: -1 on the candle where a
    bearish pattern confirms (double-top, or H&S breaking its
    neckline), +1 where a bullish one does (double-bottom, or inverse
    H&S), 0 elsewhere.

    - `tolerance_pct`: how close two peaks/troughs (the shoulders, or
      a double-top/bottom's two extremes) must be to count as "the
      same level".
    - `min_depth_pct`: how deep/tall the intervening pullback(s) must
      be, relative to the pattern's extremes — filters out noise that
      technically has the right shape but no real pattern.
    - `max_confirm_candles`: a setup that never breaks its neckline
      within this many candles of completing is discarded rather than
      left signaling indefinitely.
    """
    n = len(df)
    signal = pd.Series(0, index=df.index)
    if n < pivot_window * 2 + 3:
        return signal

    swings = sorted(find_swings(df, left=pivot_window, right=pivot_window), key=lambda s: s.timestamp)
    closes = df["close"]
    highs = [s for s in swings if s.kind is SwingKind.HIGH]
    lows = [s for s in swings if s.kind is SwingKind.LOW]

    def pos(timestamp: str) -> int:
        return df.index.get_loc(timestamp)

    def confirm(start_pos, not_before_pos, threshold, breakout_below):
        return _confirm(closes, start_pos, not_before_pos, threshold, breakout_below, max_confirm_candles)

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

    return signal


def bearish_veto_mask(pattern_signal: pd.Series, lookback: int = PATTERN_VETO_LOOKBACK) -> pd.Series:
    """True on any candle within `lookback` candles (inclusive) of a
    confirmed bearish pattern — used to block new long entries, since a
    reversal pattern argues against the uptrend an EMA crossover just
    signaled."""
    is_bearish = pattern_signal.eq(-1)
    return is_bearish.rolling(lookback, min_periods=1).max().astype(bool)
