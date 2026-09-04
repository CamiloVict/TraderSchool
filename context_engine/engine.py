"""Orchestrator: raw candles in, one ContextSnapshot out.

The order below is the decision hierarchy from master prompt section
38, and it is deliberate. Data validity is checked before anything
reads a price. Higher-timeframe structure is established before lower.
Liquidity is located before the range position is interpreted. Nothing
downstream can quietly outrank something upstream, because it simply
has not run yet.

Purity is the other design constraint: `build_context` reads no clock,
no network and no files. Every input arrives as an argument, including
the event calendar and `as_of`. That is what makes a snapshot
reproducible — re-running it on the same candles must produce a
byte-identical result a year from now — and what will make it
backtestable when the analytics milestone loops it over history.
"""
import pandas as pd

from context_engine.bias import aggregate_bias, assess_alignment, describe_biases, timeframe_bias
from context_engine.features import last_value, relative_volume
from context_engine.liquidity import analyze_liquidity
from context_engine.params import (
    CONTEXT_ENGINE_VERSION,
    DEFAULT_WEIGHTS,
    EVENT_BLACKOUT_MINUTES,
    MIN_CONFIDENCE,
    SWING_LEFT,
    SWING_RIGHT,
    TIMEFRAME_MINUTES,
    TIMEFRAMES,
)
from context_engine.ranges import analyze_ranges, primary_range
from context_engine.regime import classify_regime, classify_state
from context_engine.scoring import score_context
from context_engine.sessions import analyze_sessions
from context_engine.structure import analyze_structure
from context_engine.timeframes import build_timeframe_set, slice_frames_until
from context_engine.validation import assert_valid, validate_frames
from context_engine.volatility import analyze_volatility
from context_engine.schema import (
    Alignment,
    Bias,
    BiasHypothesis,
    ContextSnapshot,
    Direction,
    Invalidation,
    MarketState,
    Trend,
    VolatilityRegime,
    Zone,
)

EXECUTION_TIMEFRAME = "1h"


def build_context(
    frames: dict,
    asset: str = None,
    as_of=None,
    events: list = None,
    weights=DEFAULT_WEIGHTS,
    risk: dict = None,
    swing_left: int = SWING_LEFT,
    swing_right: int = SWING_RIGHT,
    strict: bool = True,
) -> ContextSnapshot:
    """Build the Daily Market Context.

    `frames` maps timeframe -> OHLCV DataFrame (see
    timeframes.build_timeframe_set). `as_of` caps every timeframe to
    what was knowable at that moment; leaving it None uses the last
    candle of the execution timeframe.

    `events` is supplied by the caller from a trusted calendar. The
    engine never invents one, and an empty list means "no events
    known", not "no events exist".

    With `strict` (the default), fatal data problems raise rather than
    producing a confident-looking snapshot built on broken candles.
    """
    events = list(events or [])
    frames = {tf: df for tf, df in (frames or {}).items() if df is not None and not df.empty}

    # Cut to the decision point *before* anything inspects the data.
    # Validating first would let the quality report describe candles
    # that had not printed yet, which is look-ahead by another name:
    # the same moment would score differently depending on how much
    # future happened to be sitting in the input.
    if as_of is not None:
        frames = _rebuild_at(frames, as_of)

    # 1. DATA VALIDITY — before a single price is read.
    quality = validate_frames(frames)
    if strict:
        assert_valid(quality)

    if not frames:
        return _empty_snapshot(asset, as_of, quality, events, risk)

    # Fall back to the densest available timeframe when the usual
    # execution one is absent (`or` would ask a DataFrame for its
    # truth value, which pandas refuses).
    if EXECUTION_TIMEFRAME in frames:
        execution = frames[EXECUTION_TIMEFRAME]
    else:
        execution = frames[max(frames, key=lambda tf: len(frames[tf]))]

    timestamp = execution.index[-1]
    price = float(execution["close"].iloc[-1])

    # 2. STRUCTURE, highest timeframe first.
    structures = {
        timeframe: analyze_structure(
            frames[timeframe], as_of=timestamp, left=swing_left, right=swing_right
        )
        for timeframe in TIMEFRAMES
        if timeframe in frames
    }

    # 3. LIQUIDITY, 4. VOLATILITY, 5. SESSIONS, 6. RANGE.
    liquidity = analyze_liquidity(frames, execution_timeframe=EXECUTION_TIMEFRAME, as_of=timestamp)
    volatility = analyze_volatility(execution)
    sessions = analyze_sessions(execution)
    ranges = analyze_ranges(frames, execution_timeframe=EXECUTION_TIMEFRAME)
    range_state = primary_range(ranges)

    # 7. BIAS and alignment across timeframes.
    biases = timeframe_bias(structures)
    alignment = assess_alignment(biases)
    direction, confidence = aggregate_bias(biases)

    # 8. REGIME and state.
    regime = classify_regime(structures, volatility, alignment)
    market_state = classify_state(regime, structures, liquidity, volatility, range_state)

    # 9. SCORE.
    score = score_context(
        biases=biases,
        range_state=range_state,
        liquidity=liquidity,
        volatility=volatility,
        events=events,
        relative_volume=last_value(relative_volume(execution)),
        weights=weights,
    )

    reasons = _build_reasons(structures, range_state, liquidity, sessions)
    invalidation = _build_invalidation(structures, direction, price)
    avoid = _no_trade_conditions(
        alignment=alignment,
        volatility=volatility,
        range_state=range_state,
        confidence=confidence,
        events=events,
        quality=quality,
    )

    no_trade = bool(avoid) or market_state is MarketState.NO_TRADE
    preferred = _preferred_direction(direction, no_trade)

    return ContextSnapshot(
        timestamp=timestamp.isoformat(),
        asset=asset,
        version=CONTEXT_ENGINE_VERSION,
        data_quality=quality,
        regime=regime,
        multi_timeframe=biases,
        alignment=alignment,
        structure=structures,
        liquidity=liquidity,
        volatility=volatility,
        range=range_state,
        sessions=sessions,
        events=events,
        bias=BiasHypothesis(
            direction=direction,
            confidence=confidence,
            reasons=reasons,
            invalidations=[invalidation.detail],
        ),
        context_score=score,
        market_state=MarketState.NO_TRADE if no_trade else market_state,
        preferred_direction=preferred,
        # Reserved for the Setup Engine. Emitting a guess here would be
        # exactly the "pattern -> order" shortcut the design forbids.
        preferred_setups=[],
        avoid=avoid,
        no_trade=no_trade,
        invalidation=invalidation,
        risk=_risk_block(volatility, risk),
    )


def _rebuild_at(frames: dict, as_of) -> dict:
    """Re-derive every timeframe from the base series cut at `as_of`.

    Slicing the aggregated frames is not enough. A daily bar is
    labelled with its opening timestamp, so at 14:00 the bar labelled
    00:00 passes an `index <= as_of` filter — while its high, low and
    close were computed from all 24 hours, nine of which are still in
    the future. Structure and range built on that bar would be reading
    candles that had not printed.

    Rebuilding from the truncated base gives a genuinely partial bar
    instead: the day *so far*, which is exactly what a live feed would
    show. It also makes `as_of` produce one code path rather than two
    that agree only by luck.
    """
    if not frames:
        return {}

    base_timeframe = min(frames, key=lambda tf: TIMEFRAME_MINUTES[tf])
    base = slice_frames_until({base_timeframe: frames[base_timeframe]}, as_of)[base_timeframe]
    if base.empty:
        return {}

    return build_timeframe_set(base, timeframes=TIMEFRAMES, base_timeframe=base_timeframe)


def _build_reasons(structures, range_state, liquidity, sessions) -> list:
    """Evidence behind the bias, each item checkable on a chart."""
    reasons = describe_biases(structures)

    if range_state is not None and range_state.zone is not Zone.EQUILIBRIUM:
        reasons.append(
            f"price in {range_state.zone.value.lower()} of the {range_state.name} range "
            f"({range_state.position_percent:.0f}%)"
        )

    if liquidity is not None and liquidity.events:
        latest = liquidity.events[-1]
        detail = f"{latest.kind.value} at {latest.level_name} ({latest.level:g})"
        if latest.reclaimed:
            detail += ", reclaimed"
        if latest.displacement:
            detail += ", with displacement"
        reasons.append(detail)

    if sessions is not None and sessions.current:
        reasons.append(f"{sessions.current} session active")

    return reasons


def _build_invalidation(structures, direction: Bias, price: float) -> Invalidation:
    """The level that would falsify the current read.

    For a bullish bias it is the last swing low: below it, the higher-
    low sequence the bias rests on no longer exists. Structural, not a
    round number, and never widened to keep a hypothesis alive.
    """
    reference = structures.get("4h") or structures.get("1d") or structures.get("1h")
    if reference is None:
        return Invalidation(type="NONE", level=None, detail="no structure available to invalidate")

    if direction in (Bias.BULLISH, Bias.STRONG_BULLISH):
        level = reference.last_swing_low
        if level is not None:
            return Invalidation(
                type="CLOSE_BELOW",
                level=level,
                detail=f"a close below the last swing low at {level:g} breaks the bullish structure",
            )

    if direction in (Bias.BEARISH, Bias.STRONG_BEARISH):
        level = reference.last_swing_high
        if level is not None:
            return Invalidation(
                type="CLOSE_ABOVE",
                level=level,
                detail=f"a close above the last swing high at {level:g} breaks the bearish structure",
            )

    return Invalidation(
        type="NONE",
        level=None,
        detail="bias is neutral; there is no directional hypothesis to invalidate",
    )


def _no_trade_conditions(alignment, volatility, range_state, confidence, events, quality) -> list:
    """Reasons not to trade. An empty list is permission, not a signal.

    Deliberately a *list* rather than a boolean: "no trade" is only
    useful if it says which condition triggered it (master prompt
    section 39).
    """
    reasons = []

    if not quality.valid:
        reasons.append("data quality is not sufficient to trade on")

    if alignment is Alignment.CONFLICT:
        reasons.append("higher timeframes conflict")

    if volatility is not None and volatility.regime is VolatilityRegime.EXTREME:
        reasons.append("volatility is extreme")

    if range_state is not None and range_state.zone is Zone.EQUILIBRIUM:
        reasons.append(f"price sits mid-range on the {range_state.name} range")

    if confidence < MIN_CONFIDENCE:
        reasons.append(f"timeframe agreement is too low ({confidence:.2f})")

    for event in events:
        minutes = event.minutes_to_event
        if str(event.importance).upper() == "HIGH" and minutes is not None and 0 <= minutes <= EVENT_BLACKOUT_MINUTES:
            reasons.append(f"{event.event} in {minutes} minutes")

    return reasons


def _preferred_direction(direction: Bias, no_trade: bool) -> Direction:
    if no_trade:
        return Direction.NONE
    if direction in (Bias.BULLISH, Bias.STRONG_BULLISH):
        return Direction.LONG
    if direction in (Bias.BEARISH, Bias.STRONG_BEARISH):
        return Direction.SHORT
    return Direction.NONE


def _risk_block(volatility, risk: dict = None) -> dict:
    """Read-only view of the configured risk limits.

    Mirrors config.py so a snapshot records the limits it was produced
    under. It computes nothing and changes nothing: sizing stays in
    risk_manager.py, and no interpretation of context is ever allowed
    to widen a limit (master prompt section 21).
    """
    if risk is None:
        from config import MAX_DAILY_LOSS_PCT, RISK_PER_TRADE_PCT, STOP_LOSS_PCT

        risk = {
            "max_risk_per_trade_pct": RISK_PER_TRADE_PCT,
            "max_daily_loss_pct": MAX_DAILY_LOSS_PCT,
            "stop_loss_pct": STOP_LOSS_PCT,
        }

    return {
        **risk,
        "high_volatility": bool(
            volatility is not None
            and volatility.regime in (VolatilityRegime.HIGH, VolatilityRegime.EXTREME)
        ),
    }


def _empty_snapshot(asset, as_of, quality, events, risk) -> ContextSnapshot:
    """Snapshot for "there is nothing to analyze".

    Returned instead of None so a caller always gets the same shape and
    can read `no_trade` without special-casing. Only reachable with
    strict=False, since strict mode raises on missing data first.
    """
    from context_engine.schema import (
        LiquidityState,
        Phase,
        RangeState,
        Regime,
        RegimeKind,
        SessionState,
        StructureState,
        VolatilityState,
    )

    timestamp = pd.Timestamp(as_of).isoformat() if as_of is not None else ""

    return ContextSnapshot(
        timestamp=timestamp,
        asset=asset,
        version=CONTEXT_ENGINE_VERSION,
        data_quality=quality,
        regime=Regime(
            primary=RegimeKind.TRANSITION,
            volatility=VolatilityRegime.NORMAL,
            phase=Phase.UNDEFINED,
        ),
        multi_timeframe={},
        alignment=Alignment.PARTIAL_ALIGNMENT,
        structure={},
        liquidity=LiquidityState(),
        volatility=VolatilityState(
            atr=0.0,
            atr_percent=0.0,
            regime=VolatilityRegime.NORMAL,
            expansion=False,
            contraction=False,
            percentile=50.0,
        ),
        range=RangeState(name="none", high=0.0, low=0.0, position_percent=50.0, zone=Zone.EQUILIBRIUM),
        sessions=SessionState(
            current=None,
            high=None,
            low=None,
            range=None,
            previous=None,
            previous_high=None,
            previous_low=None,
        ),
        events=list(events or []),
        bias=BiasHypothesis(
            direction=Bias.NEUTRAL,
            confidence=0.0,
            reasons=[],
            invalidations=["no data"],
        ),
        context_score=score_context(
            biases={}, range_state=None, liquidity=None, volatility=None, events=events
        ),
        market_state=MarketState.NO_TRADE,
        preferred_direction=Direction.NONE,
        preferred_setups=[],
        avoid=["no market data available"],
        no_trade=True,
        invalidation=Invalidation(type="NONE", level=None, detail="no data"),
        risk=_risk_block(None, risk),
    )
