"""Market State Machine (master prompt section 15/36).

`regime.classify_state()` is a classifier: it looks at the current bar
and returns a label with no memory of what came before. That is
exactly what it says it is — the state machine is a separate concern,
built here on top of it.

Two things a classifier alone can't give you:

1. **Explicit, bounded transitions.** A single noisy candle
   classifying as REVERSAL_ATTEMPT shouldn't be able to flip a
   long-standing TREND_UP straight to TREND_DOWN in one step — section
   38's own rule is that one candle never invalidates strong HTF
   structure by itself. `next_state()` only accepts a transition listed
   in `ALLOWED_TRANSITIONS`; anything else holds the previous state for
   one more bar rather than jumping.

2. **A registry of what a state means for risk and setups** (section
   36's contract): which setups make sense in this state, which are
   forbidden outright, and a risk multiplier — an unconfirmed
   REVERSAL_ATTEMPT should risk less than a clean TREND_UP, even before
   any setup fires.

HIGH_VOLATILITY and NO_TRADE are risk overrides, not states to be
gradually transitioned into or held in: master prompt section 38 puts
risk limits above market state in the decision hierarchy, so entering
either is immediate, and so is leaving them once the classifier no
longer sees a reason to stay.
"""
from dataclasses import dataclass, field

from context_engine.schema import MarketState, SetupName

_RISK_OVERRIDES = (MarketState.HIGH_VOLATILITY, MarketState.NO_TRADE)

# One-hop transitions the state machine accepts from each state (a
# state can always "transition" to itself — that's just holding).
# Deliberately excludes HIGH_VOLATILITY/NO_TRADE here: those are
# handled unconditionally in next_state() rather than listed per-state,
# since they're reachable from (and leavable from) everywhere.
ALLOWED_TRANSITIONS = {
    MarketState.TREND_UP: {
        MarketState.TREND_UP,
        MarketState.PULLBACK,
        MarketState.REVERSAL_ATTEMPT,
        MarketState.RANGE,
    },
    MarketState.PULLBACK: {
        MarketState.PULLBACK,
        MarketState.TREND_UP,
        MarketState.TREND_DOWN,
        MarketState.REVERSAL_ATTEMPT,
        MarketState.RANGE,
    },
    MarketState.REVERSAL_ATTEMPT: {
        MarketState.REVERSAL_ATTEMPT,
        MarketState.TREND_UP,
        MarketState.TREND_DOWN,
        MarketState.FAILED_BREAKOUT,
        MarketState.RANGE,
    },
    MarketState.TREND_DOWN: {
        MarketState.TREND_DOWN,
        MarketState.PULLBACK,
        MarketState.REVERSAL_ATTEMPT,
        MarketState.RANGE,
    },
    MarketState.RANGE: {
        MarketState.RANGE,
        MarketState.RANGE_EXPANSION,
        MarketState.BREAKOUT_ATTEMPT,
        MarketState.TREND_UP,
        MarketState.TREND_DOWN,
    },
    MarketState.RANGE_EXPANSION: {
        MarketState.RANGE_EXPANSION,
        MarketState.RANGE,
        MarketState.BREAKOUT_ATTEMPT,
        MarketState.TREND_UP,
        MarketState.TREND_DOWN,
    },
    MarketState.BREAKOUT_ATTEMPT: {
        MarketState.BREAKOUT_ATTEMPT,
        MarketState.TREND_UP,
        MarketState.TREND_DOWN,
        MarketState.FAILED_BREAKOUT,
        MarketState.RANGE,
    },
    MarketState.FAILED_BREAKOUT: {
        MarketState.FAILED_BREAKOUT,
        MarketState.RANGE,
        MarketState.REVERSAL_ATTEMPT,
        MarketState.TREND_UP,
        MarketState.TREND_DOWN,
    },
}


def next_state(previous_state, classified_state: MarketState) -> MarketState:
    """The state the machine actually reports, given what it was in
    last time and what the stateless classifier reads right now.

    `previous_state` is None only for the very first snapshot in a
    sequence — there's nothing to hold, so the classified state is
    accepted outright.
    """
    if previous_state is None:
        return classified_state
    if classified_state in _RISK_OVERRIDES or previous_state in _RISK_OVERRIDES:
        return classified_state
    if classified_state == previous_state:
        return classified_state
    if classified_state in ALLOWED_TRANSITIONS.get(previous_state, ()):
        return classified_state
    return previous_state


@dataclass(frozen=True)
class StateDefinition:
    """Master prompt section 36's contract for one state."""

    state: MarketState
    entry_conditions: tuple = ()
    exit_conditions: tuple = ()
    allowed_setups: tuple = ()
    forbidden_setups: tuple = ()
    risk_modifier: float = 1.0


STATE_DEFINITIONS = {
    MarketState.TREND_UP: StateDefinition(
        state=MarketState.TREND_UP,
        entry_conditions=("HTF structure prints a higher-high/higher-low sequence",),
        exit_conditions=("price pulls back from the last swing high", "a CHOCH breaks the up-sequence"),
        allowed_setups=(SetupName.LIQUIDITY_SWEEP_RECLAIM,),
        risk_modifier=1.0,
    ),
    MarketState.PULLBACK: StateDefinition(
        state=MarketState.PULLBACK,
        entry_conditions=("trend regime holds", "phase reads PULLBACK"),
        exit_conditions=("price resumes toward the prior extreme", "a CHOCH confirms reversal instead"),
        allowed_setups=(SetupName.LIQUIDITY_SWEEP_RECLAIM,),
        risk_modifier=1.0,
    ),
    MarketState.REVERSAL_ATTEMPT: StateDefinition(
        state=MarketState.REVERSAL_ATTEMPT,
        entry_conditions=("a CHOCH just broke the prevailing structure",),
        exit_conditions=("a same-direction BOS confirms the reversal", "price fails and resumes the old trend"),
        allowed_setups=(SetupName.LIQUIDITY_SWEEP_RECLAIM,),
        risk_modifier=0.75,  # unconfirmed reversal: smaller size until it proves itself
    ),
    MarketState.TREND_DOWN: StateDefinition(
        state=MarketState.TREND_DOWN,
        entry_conditions=("HTF structure prints a lower-high/lower-low sequence",),
        exit_conditions=("price pulls back from the last swing low", "a CHOCH breaks the down-sequence"),
        allowed_setups=(SetupName.LIQUIDITY_SWEEP_RECLAIM,),
        risk_modifier=1.0,
    ),
    MarketState.RANGE: StateDefinition(
        state=MarketState.RANGE,
        entry_conditions=("higher timeframes agree on RANGING",),
        exit_conditions=("price expands beyond the range with displacement",),
        allowed_setups=(SetupName.LIQUIDITY_SWEEP_RECLAIM,),
        risk_modifier=0.75,  # no trend to lean on
    ),
    MarketState.RANGE_EXPANSION: StateDefinition(
        state=MarketState.RANGE_EXPANSION,
        entry_conditions=("a liquidity expansion event just fired",),
        exit_conditions=("the expansion resolves into a new trend or fails back into range",),
        allowed_setups=(SetupName.LIQUIDITY_SWEEP_RECLAIM,),
        risk_modifier=0.75,
    ),
    MarketState.BREAKOUT_ATTEMPT: StateDefinition(
        state=MarketState.BREAKOUT_ATTEMPT,
        entry_conditions=("price is testing a range boundary or key level",),
        exit_conditions=("the level breaks and holds (continuation)", "price fails back inside (FAILED_BREAKOUT)"),
        allowed_setups=(SetupName.LIQUIDITY_SWEEP_RECLAIM,),
        risk_modifier=0.75,
    ),
    MarketState.FAILED_BREAKOUT: StateDefinition(
        state=MarketState.FAILED_BREAKOUT,
        entry_conditions=("a swept level failed to hold beyond it (no displacement, or reclaimed)",),
        exit_conditions=("structure resumes its prior direction", "a genuine reversal confirms instead"),
        allowed_setups=(SetupName.LIQUIDITY_SWEEP_RECLAIM,),
        risk_modifier=0.5,  # this state exists because the market just faked a move
    ),
    MarketState.HIGH_VOLATILITY: StateDefinition(
        state=MarketState.HIGH_VOLATILITY,
        entry_conditions=("ATR% regime reads EXTREME",),
        exit_conditions=("volatility regime normalizes",),
        forbidden_setups=(SetupName.LIQUIDITY_SWEEP_RECLAIM,),
        risk_modifier=0.0,
    ),
    MarketState.NO_TRADE: StateDefinition(
        state=MarketState.NO_TRADE,
        entry_conditions=("one or more no-trade conditions from engine._no_trade_conditions hold",),
        exit_conditions=("every no-trade condition clears",),
        forbidden_setups=(SetupName.LIQUIDITY_SWEEP_RECLAIM,),
        risk_modifier=0.0,
    ),
}


def allowed_setup_names(state: MarketState) -> tuple:
    """Setup names the Setup Engine should even bother checking for in
    `state` — forbidden ones are excluded regardless of what
    `allowed_setups` lists, so a state only has to declare one or the
    other, not keep both lists consistent by hand."""
    definition = STATE_DEFINITIONS.get(state)
    if definition is None:
        return ()
    return tuple(name for name in definition.allowed_setups if name not in definition.forbidden_setups)
