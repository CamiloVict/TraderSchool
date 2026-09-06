"""Trend Pullback strategy — VERSION 1 of the "Strategy Engine V2"
proposal (multi-regime, multi-strategy engine; see README's own section
for the full 25-part spec and why only this one isolated piece got
built first: the spec's own rules on optimization say not to build/
tune everything at once, and a lighter version of the same underlying
idea -- context_engine's Setup Engine, multi-timeframe bias + structure
+ liquidity -- already backtested badly against real PAXG data before
this was written. This is the cheapest, smallest deviation from the
current live baseline (EMA + trend-strength filter + pyramiding) worth
testing before committing to anything bigger.

What changes vs. the baseline: the EMA 20/50 crossover stops being the
entry trigger and becomes *context* only (see the spec's section 2) --
a long only fires once, mid-uptrend, when price pulls back and then
resolves that pullback with a confirmed bullish structure break (BOS or
CHOCH), not on every crossover. This is deliberately a single-timeframe
(1h) experiment: the full spec also wants HTF (4h/1h) alignment before
counting a setup, but the spec's own anti-overfitting rules (section
21: "few robust variables", "one variable at a time") say to test the
structural entry filter in isolation first, on the same timeframe as
the baseline, before adding a second timeframe's worth of signals on
top.

Signal logic, evaluated at each candle's close (same timing convention
as strategy.add_signals -- decide with what's known as of that close,
nothing later):
  - EMA context: ema_fast > ema_slow (same periods as strategy.py) --
    necessary but not sufficient, exactly per the spec's own worked
    example ("EMA20 > EMA50 does NOT automatically mean BUY").
  - Structural trend: context_engine.structure's own swing-based
    trend (Trend.UP) must also agree -- an independent read of the
    same "are we in an uptrend" question, not a second flag driven by
    the same EMA values.
  - Pullback: the candle immediately before the entry trigger must
    have been in Phase.PULLBACK (price below the last confirmed swing
    high, i.e. retracing) -- distinguishes "the pullback resolved into
    a genuine continuation" from a fresh breakout with no retracement
    behind it (that would be closer to the spec's own separate
    TREND_CONTINUATION/BREAKOUT_RETEST setups, out of scope for V1).
  - Confirmation: a BULLISH_BOS or BULLISH_CHOCH must confirm exactly
    on the entry candle -- the "reclaim/displacement" the spec asks
    for, reusing structure.py's own look-ahead-safe break detection
    instead of inventing new displacement logic.
  - Exit: a BEARISH_CHOCH confirming while long -- the first real
    evidence structure has turned, not just a normal pullback low
    (which would fire constantly and defeat the point of trading
    pullbacks in the first place). The stop-loss (this module doesn't
    place it -- see trend_pullback_backtester.py) is the other way a
    trade can end, exactly like strategy.py/backtester.py's own split.

Long-only, like every other strategy in this repo -- Binance Spot
Testnet execution (main.py) can't short natively, and nothing here is
wired into main.py --trade yet regardless (backtest-only until it
proves out against the baseline).

Performance note: find_swings()/find_breaks() are each computed ONCE
over the *entire* df, not re-run on a growing window per candle --
unlike context_engine.engine.build_context() (which re-resamples
multiple timeframes and genuinely needs a rolling window to stay
linear-time, see setup_engine_backtester.py's own docstring), these
two functions are already single-pass and causal by construction
(every pivot/break carries its own confirmed_at/broken_at and
swings_known_at() filters correctly for any point in time), so calling
them fresh on an expanding slice would only add unnecessary O(n^2)
cost without buying any additional look-ahead safety.
"""
import pandas as pd

from context_engine.schema import BreakKind, Phase, Trend
from context_engine.structure import (
    classify_phase,
    classify_trend,
    find_breaks,
    find_swings,
    label_sequence,
    swings_known_at,
)
from strategy import FAST_PERIOD, SLOW_PERIOD

_BULLISH_BREAKS = (BreakKind.BULLISH_BOS, BreakKind.BULLISH_CHOCH)


def add_signals(df: pd.DataFrame, fast: int = FAST_PERIOD, slow: int = SLOW_PERIOD) -> pd.DataFrame:
    """Return a copy of `df` (needs 'high'/'low'/'close') with EMA
    columns, `structural_trend`/`phase` (context_engine.structure's own
    Trend/Phase, as strings, for auditing), and a `signal` column
    (1 = be long, 0 = be flat).

    Note: the first `SWING_LEFT + SWING_RIGHT + 1` rows can never carry
    a confirmed swing (find_swings' own documented cost of pivot
    detection) and the first `slow` rows are the EMA warm-up -- callers
    should skip max(those), exactly like strategy.add_signals's own
    warm-up rows.
    """
    out = df.copy()
    out["ema_fast"] = out["close"].ewm(span=fast, adjust=False).mean()
    out["ema_slow"] = out["close"].ewm(span=slow, adjust=False).mean()

    all_swings = find_swings(out)
    all_breaks = find_breaks(out, all_swings)
    breaks_by_time = {}
    for b in all_breaks:
        breaks_by_time.setdefault(b.broken_at, []).append(b)

    trends = []
    phases = []
    entry_trigger = []
    exit_trigger = []
    for i, (timestamp, _row) in enumerate(out.iterrows()):
        known = swings_known_at(all_swings, timestamp)
        sequence = label_sequence(known)
        trend = classify_trend(sequence)
        phase = classify_phase(out.iloc[: i + 1], trend, known)
        trends.append(trend.value)
        phases.append(phase.value)

        candle_breaks = breaks_by_time.get(timestamp.isoformat(), [])
        entry_trigger.append(any(b.kind in _BULLISH_BREAKS for b in candle_breaks))
        exit_trigger.append(any(b.kind is BreakKind.BEARISH_CHOCH for b in candle_breaks))

    out["structural_trend"] = trends
    out["phase"] = phases

    ema_bullish = out["ema_fast"] > out["ema_slow"]
    structural_uptrend = out["structural_trend"] == Trend.UP.value
    # Shifted: the pullback has to have been the state *before* this
    # candle's break resolved it, not the (already-IMPULSE) phase the
    # break candle itself reads as -- see this module's own docstring.
    was_pullback = out["phase"].shift(1) == Phase.PULLBACK.value

    entry = ema_bullish & structural_uptrend & was_pullback & pd.Series(entry_trigger, index=out.index)
    exit_ = pd.Series(exit_trigger, index=out.index)

    # Stateful, same convention as scalping_strategy.add_signals: once
    # long, stay long until the structural exit fires, regardless of
    # what the entry condition does in between.
    signal = []
    holding = 0
    for should_enter, should_exit in zip(entry.fillna(False), exit_.fillna(False)):
        if holding == 0 and should_enter:
            holding = 1
        elif holding == 1 and should_exit:
            holding = 0
        signal.append(holding)
    out["signal"] = signal

    return out
