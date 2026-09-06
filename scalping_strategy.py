"""Range mean-reversion strategy for the BTC scalping bot.

**Retired, not just untuned**: see scalping_backtester.py's own
docstring and README's walk-forward section for why -- 0/3 out-of-sample
segments profitable, worsening with every tuning attempt, the worst
segment landing on a real BTC rally that broke the range this strategy
assumes. Not connected to main.py --trade and not an active development
track; kept for reference.

This is deliberately a *different kind* of strategy from strategy.py's
EMA crossover, not the same idea run on a faster clock. A fast EMA
crossover on 5m candles mostly reacts to noise -- trend-following needs
an actual trend to follow, and most of the time a market spends
consolidating, not trending. Reversion is the complementary idea: while
price is oscillating inside a range, buy near the bottom and sell near
the top, betting on the oscillation continuing rather than a breakout
starting. It should run as its own bot/cron against BTC/USDT, with its
own state files -- see README's "Automatizarlo" section -- not mixed
into the PAXG trend bot's position tracking.

Signal logic, evaluated at each candle's close (same timing convention
as strategy.add_signals -- decide with what's fully known as of that
close, nothing later):
  - A "current range" is the high/low of the last `lookback` candles.
    Not a swing high/low or the context_engine's structural ranges --
    just a rolling window, cheap to compute every candle.
  - Only consider trading it if the range is wide enough relative to
    ATR (`min_range_atr_multiple`): a range narrower than ~2.5x ATR is
    too tight for a round-trip to clear fees + slippage, whatever the
    zone math says.
  - BUY when price is in the bottom `discount_max`% of that range AND
    RSI confirms oversold AND the current candle itself closed bullish
    (close > open) -- the zone/RSI alone is not enough evidence (see
    context_engine.setups's own rule that no single indicator is a
    sufficient signal by itself), and specifically not enough to rule
    out "this is still falling." A backtest against real BTC/USDT (see
    this repo's history) lost heavily buying oversold dips that kept
    dipping -- 62% of trades hit their stop -- because RSI-oversold is
    a *lagging* read of "it fell a lot," not a leading one of "it's
    about to turn." Requiring the entry candle to have actually closed
    up is a cheap, real confirmation that a bounce has started, at the
    cost of a slightly worse entry price than the exact bottom.
  - SELL/EXIT when price reaches the top `premium_min`% of the range.
    No confirmation gate on the exit: getting out of a position is a
    risk-reducing action, not a risk-taking one, so it deliberately
    has a lower bar than entering (same asymmetry
    setup_engine_backtester.py's exit rules use).
  - Once long, stays long until the exit condition fires (or a caller's
    own stop-loss triggers, e.g. a structural stop below the range low
    -- this module only labels *entries and signal-exits*, it does not
    place stops; see scalping_backtester.py for how those combine, the
    same split strategy.py/backtester.py already have).

Reuses context_engine.features' shared indicator primitives (rsi, atr)
and context_engine.ranges' range-position math rather than
reimplementing either -- both are already pure functions with no
dependency on the rest of that engine (structures, bias, liquidity),
so pulling them in here doesn't drag in build_context()'s much heavier
multi-timeframe machinery.
"""
import numpy as np
import pandas as pd

from context_engine.features import atr_percent, rsi

LOOKBACK = 20
RSI_PERIOD = 14
RSI_OVERSOLD = 25
DISCOUNT_MAX = 15.0
PREMIUM_MIN = 85.0
MIN_RANGE_ATR_MULTIPLE = 2.5


def add_signals(
    df: pd.DataFrame,
    lookback: int = LOOKBACK,
    rsi_period: int = RSI_PERIOD,
    rsi_oversold: float = RSI_OVERSOLD,
    discount_max: float = DISCOUNT_MAX,
    premium_min: float = PREMIUM_MIN,
    min_range_atr_multiple: float = MIN_RANGE_ATR_MULTIPLE,
    require_bullish_confirmation: bool = True,
) -> pd.DataFrame:
    """Return a copy of `df` (needs 'high', 'low', 'close') with the
    indicator columns and a `signal` column (1 = be long, 0 = be flat).

    Note: the first `max(lookback, rsi_period)` rows are a warm-up
    period (rolling range / RSI not fully formed yet) and aren't a
    reliable signal -- callers should skip them, exactly like
    strategy.add_signals's own warm-up rows.
    """
    out = df.copy()
    out["rsi"] = rsi(out["close"], period=rsi_period)
    out["atr_pct"] = atr_percent(out)
    out["range_high"] = out["high"].rolling(lookback, min_periods=lookback).max()
    out["range_low"] = out["low"].rolling(lookback, min_periods=lookback).min()

    span = (out["range_high"] - out["range_low"]).replace(0, np.nan)
    position_pct = ((out["close"] - out["range_low"]) / span * 100).clip(0, 100)
    out["range_position_pct"] = position_pct.fillna(50.0)

    range_width_pct = span / out["close"] * 100
    wide_enough = range_width_pct >= (out["atr_pct"] * min_range_atr_multiple)

    entry = wide_enough & (out["range_position_pct"] <= discount_max) & (out["rsi"] <= rsi_oversold)
    if require_bullish_confirmation:
        entry = entry & (out["close"] > out["open"])
    exit_ = out["range_position_pct"] >= premium_min

    # Stateful, like a live cycle would be: once in, stay in until the
    # exit condition fires, regardless of what the entry condition does
    # in between (e.g. RSI recovering out of oversold mid-hold must not
    # itself cause an exit -- only reaching the premium zone does).
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
