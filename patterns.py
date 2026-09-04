"""Classical double-top / double-bottom reversal pattern detection.

Scope, deliberately narrow (see the feasibility report this responds
to): chart patterns are historical pattern-matching over past price,
same family as the EMA crossover in strategy.py — not a forward-looking
"theoretical" model. The academic evidence for them is weak on
intraday data and, even on daily data, supports them only as a
confirmation signal, not a stand-alone strategy (Savin, Weller &
Zvingelis 2007). So here they're wired in as exactly that: a veto on
new EMA-crossover entries, not a replacement for the EMA signal or an
independent source of trades.

Pipeline: find swing highs/lows (a candle whose high/low is the
extreme within a window of neighbors on each side), then look for two
swing highs (or lows) close enough in price to be "the same level",
separated by a swing low (high) deep (tall) enough to count as a real
pullback rather than noise. The pattern only counts once price
actually breaks that intervening level — that's the "neckline" break
technical-analysis literature treats as confirmation, and it's the
candle this module reports the signal on.
"""
import pandas as pd

# How many candles a confirmed double-top's veto blocks new long
# entries for, before it's considered stale.
PATTERN_VETO_LOOKBACK = 10


def _find_swings(df: pd.DataFrame, window: int) -> list:
    """Chronological (position, price, kind) for swing highs/lows: a
    candle counts as a swing high/low if its high/low is the max/min
    within `window` candles on each side."""
    highs = df["high"]
    lows = df["low"]
    n = len(df)
    swings = []
    for i in range(window, n - window):
        lo, hi = i - window, i + window + 1
        if highs.iloc[i] == highs.iloc[lo:hi].max():
            swings.append((i, float(highs.iloc[i]), "high"))
        if lows.iloc[i] == lows.iloc[lo:hi].min():
            swings.append((i, float(lows.iloc[i]), "low"))
    swings.sort(key=lambda s: s[0])
    return swings


def detect_double_patterns(
    df: pd.DataFrame,
    pivot_window: int = 5,
    tolerance_pct: float = 1.5,
    min_depth_pct: float = 1.0,
    max_confirm_candles: int = 20,
) -> pd.Series:
    """Detect double-top/double-bottom patterns in `df` (needs 'high',
    'low', 'close' columns).

    Returns a Series aligned to df.index: -1 on the candle where a
    double-top confirms (close breaks below the valley between its two
    peaks), +1 where a double-bottom confirms (close breaks above the
    peak between its two troughs), 0 elsewhere.

    - `tolerance_pct`: how close the two peaks/troughs must be to
      count as "the same level".
    - `min_depth_pct`: how deep/tall the intervening pullback must be,
      relative to the peaks/troughs — filters out noise that technically
      has two nearby swing points but no real pattern shape.
    - `max_confirm_candles`: a setup that never breaks the neckline
      within this many candles of the second peak/trough is discarded
      rather than left signaling indefinitely.
    """
    n = len(df)
    signal = pd.Series(0, index=df.index)
    if n < pivot_window * 2 + 3:
        return signal

    swings = _find_swings(df, pivot_window)
    closes = df["close"]
    highs = [s for s in swings if s[2] == "high"]
    lows = [s for s in swings if s[2] == "low"]

    def confirm(start_pos: int, threshold: float, breakout_below: bool):
        end = min(n, start_pos + 1 + max_confirm_candles)
        for j in range(start_pos + 1, end):
            price = closes.iloc[j]
            if (breakout_below and price < threshold) or (not breakout_below and price > threshold):
                return j
        return None

    for (p1, h1, _), (p2, h2, _) in zip(highs, highs[1:]):
        if abs(h1 - h2) / ((h1 + h2) / 2) > tolerance_pct / 100:
            continue
        between = [low for low in lows if p1 < low[0] < p2]
        if not between:
            continue
        valley = min(between, key=lambda s: s[1])
        depth = (min(h1, h2) - valley[1]) / min(h1, h2)
        if depth < min_depth_pct / 100:
            continue
        confirm_pos = confirm(p2, valley[1], breakout_below=True)
        if confirm_pos is not None:
            signal.iloc[confirm_pos] = -1

    for (p1, l1, _), (p2, l2, _) in zip(lows, lows[1:]):
        if abs(l1 - l2) / ((l1 + l2) / 2) > tolerance_pct / 100:
            continue
        between = [high for high in highs if p1 < high[0] < p2]
        if not between:
            continue
        peak = max(between, key=lambda s: s[1])
        rise = (peak[1] - max(l1, l2)) / max(l1, l2)
        if rise < min_depth_pct / 100:
            continue
        confirm_pos = confirm(p2, peak[1], breakout_below=False)
        if confirm_pos is not None:
            signal.iloc[confirm_pos] = 1

    return signal


def bearish_veto_mask(pattern_signal: pd.Series, lookback: int = PATTERN_VETO_LOOKBACK) -> pd.Series:
    """True on any candle within `lookback` candles (inclusive) of a
    confirmed double-top — used to block new long entries, since a
    topping pattern argues against the uptrend an EMA crossover just
    signaled."""
    is_top = pattern_signal.eq(-1)
    return is_top.rolling(lookback, min_periods=1).max().astype(bool)
