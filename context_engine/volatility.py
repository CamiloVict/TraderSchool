"""Volatility regime.

Classified by percentile against the asset's *own* recent history
rather than an absolute threshold. An ATR of 0.8% is calm for BTC and
wild for a stablecoin pair, and even for one asset the meaning of
"normal" drifts across a year. Ranking each reading against the last
`VOLATILITY_LOOKBACK` bars keeps the label comparable over time and
across symbols.

Regime and trend are independent: a market can trend in low volatility
or chop violently. They are separate fields, never collapsed into one
label (master prompt section 5).
"""
import pandas as pd

from context_engine.features import atr, atr_percent, last_value
from context_engine.params import (
    ATR_PERIOD,
    VOLATILITY_CONTRACTION_RATIO,
    VOLATILITY_EXPANSION_RATIO,
    VOLATILITY_LOOKBACK,
    VOLATILITY_PERCENTILES,
)
from context_engine.schema import VolatilityRegime


def classify_regime(percentile: float) -> VolatilityRegime:
    """Map an ATR% percentile (0-100) onto the five-way regime."""
    if percentile is None:
        return VolatilityRegime.NORMAL
    if percentile < VOLATILITY_PERCENTILES["VERY_LOW"]:
        return VolatilityRegime.VERY_LOW
    if percentile < VOLATILITY_PERCENTILES["LOW"]:
        return VolatilityRegime.LOW
    if percentile < VOLATILITY_PERCENTILES["NORMAL"]:
        return VolatilityRegime.NORMAL
    if percentile < VOLATILITY_PERCENTILES["HIGH"]:
        return VolatilityRegime.HIGH
    return VolatilityRegime.EXTREME


def analyze_volatility(
    df: pd.DataFrame,
    period: int = ATR_PERIOD,
    lookback: int = VOLATILITY_LOOKBACK,
):
    """ATR, ATR%, regime and whether volatility is expanding.

    Returns a VolatilityState. With too little history to rank against,
    it reports NORMAL rather than guessing — an unranked reading is
    unknown, not average, and calling it EXTREME would gate trading on
    nothing.
    """
    from context_engine.schema import VolatilityState

    if df is None or df.empty:
        return VolatilityState(
            atr=0.0,
            atr_percent=0.0,
            regime=VolatilityRegime.NORMAL,
            expansion=False,
            contraction=False,
            percentile=50.0,
        )

    atr_series = atr(df, period)
    atr_pct_series = atr_percent(df, period)

    current_atr = last_value(atr_series, 0.0)
    current_pct = last_value(atr_pct_series, 0.0)

    history = atr_pct_series.dropna().tail(lookback)
    if len(history) < max(10, period):
        percentile = 50.0
        regime = VolatilityRegime.NORMAL
    else:
        percentile = float((history <= current_pct).mean() * 100)
        regime = classify_regime(percentile)

    # Expansion compares the current ATR to its own recent average:
    # the percentile says "how unusual", this says "which direction".
    average_atr = float(atr_series.dropna().tail(lookback).mean()) if len(atr_series.dropna()) else 0.0
    expansion = bool(average_atr and current_atr > average_atr * VOLATILITY_EXPANSION_RATIO)
    contraction = bool(average_atr and current_atr < average_atr * VOLATILITY_CONTRACTION_RATIO)

    return VolatilityState(
        atr=round(float(current_atr), 8),
        atr_percent=round(float(current_pct), 4),
        regime=regime,
        expansion=expansion,
        contraction=contraction,
        percentile=round(percentile, 2),
    )
