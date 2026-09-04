"""Market regime and a minimal market-state classifier.

Regime keeps trend and volatility as separate attributes rather than
one label, because they vary independently: TRENDING_UP with EXTREME
volatility is a very different thing to trade than TRENDING_UP with
LOW volatility, and collapsing them loses exactly the distinction that
should change position size.

Scope note: `classify_state` is a *classifier*, not the state machine
of master prompt section 15. It reads the current bar and returns a
label. It has no memory of the previous state, no explicit transition
conditions and no risk modifier per state — all of that belongs to the
state-machine milestone. Keeping it deliberately simple here avoids
half-building something that later has to be unpicked.
"""
from context_engine.schema import (
    Alignment,
    LiquidityEventKind,
    MarketState,
    Phase,
    Regime,
    RegimeKind,
    Trend,
    VolatilityRegime,
    Zone,
)


def classify_regime(
    structures: dict,
    volatility,
    alignment: Alignment,
) -> Regime:
    """Primary regime plus volatility and phase attributes.

    Driven by the daily and 4h structure: those define the context the
    rest of the day is traded inside. TRANSITION is the honest answer
    when the higher timeframes disagree — the market is between
    regimes, not in one.
    """
    daily = structures.get("1d")
    four_hour = structures.get("4h")
    reference = daily or four_hour

    phase = reference.phase if reference is not None else Phase.UNDEFINED
    volatility_regime = volatility.regime if volatility is not None else VolatilityRegime.NORMAL

    if alignment is Alignment.CONFLICT:
        return Regime(primary=RegimeKind.TRANSITION, volatility=volatility_regime, phase=phase)

    trends = [s.trend for s in (daily, four_hour) if s is not None]
    if trends and all(t is Trend.UP for t in trends):
        primary = RegimeKind.TRENDING_UP
    elif trends and all(t is Trend.DOWN for t in trends):
        primary = RegimeKind.TRENDING_DOWN
    elif trends and all(t is Trend.RANGING for t in trends):
        primary = RegimeKind.RANGING
    else:
        primary = RegimeKind.TRANSITION

    return Regime(primary=primary, volatility=volatility_regime, phase=phase)


def classify_state(
    regime: Regime,
    structures: dict,
    liquidity,
    volatility,
    range_state,
) -> MarketState:
    """Single-bar market state.

    Checked in priority order, mirroring master prompt section 38:
    risk-shaped conditions (extreme volatility) outrank structure,
    structure outranks liquidity events, and liquidity outranks where
    price happens to sit in the range.
    """
    if volatility is not None and volatility.regime is VolatilityRegime.EXTREME:
        return MarketState.HIGH_VOLATILITY

    recent_event = liquidity.recent_event if liquidity is not None else None

    if recent_event is LiquidityEventKind.LIQUIDITY_EXPANSION:
        return MarketState.RANGE_EXPANSION
    if recent_event in (LiquidityEventKind.SWEEP_HIGH, LiquidityEventKind.SWEEP_LOW):
        # A swept level that price rejected is a failed breakout; the
        # direction it failed in is what the setup engine will care
        # about later.
        return MarketState.FAILED_BREAKOUT

    execution = structures.get("1h") or structures.get("4h")
    if execution is not None and execution.last_choch is not None:
        return MarketState.REVERSAL_ATTEMPT

    if regime.primary is RegimeKind.TRENDING_UP:
        return MarketState.PULLBACK if regime.phase is Phase.PULLBACK else MarketState.TREND_UP
    if regime.primary is RegimeKind.TRENDING_DOWN:
        return MarketState.PULLBACK if regime.phase is Phase.PULLBACK else MarketState.TREND_DOWN

    if regime.primary is RegimeKind.RANGING:
        if range_state is not None and range_state.zone is Zone.EQUILIBRIUM:
            # Mid-range in a range regime: no edge in either direction.
            return MarketState.NO_TRADE
        return MarketState.RANGE

    return MarketState.NO_TRADE
