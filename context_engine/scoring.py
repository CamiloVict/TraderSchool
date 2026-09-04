"""Weighted context score.

Turns the qualitative picture into one number, and — more importantly
— shows its work. Every snapshot carries the individual components, so
a score of +6 can always be decomposed into which evidence produced it
rather than being an opaque verdict.

Two honesty constraints:

  - The weights are guesses. They are conservative and readable, not
    optimized, and `WEIGHTS_VERSION` is stamped into every snapshot so
    a score is never compared across two different weightings. Tuning
    them requires the walk-forward machinery from master prompt
    section 27, which does not exist yet.

  - Penalties shrink conviction, they do not flip it. A high-impact
    event does not make a bullish market bearish; it makes acting on
    the bullish read a worse idea. So event and volatility penalties
    are applied against the sign of the evidence, never added blindly.
"""
from context_engine.params import DEFAULT_WEIGHTS, SCORE_THRESHOLDS, WEIGHTS_VERSION
from context_engine.schema import (
    ContextScore,
    LiquidityEventKind,
    ScoreComponent,
    Trend,
    VolatilityRegime,
    Zone,
)

# Which timeframe feeds which weight.
TREND_COMPONENTS = (
    ("1w", "weekly_trend", "weekly trend"),
    ("1d", "daily_trend", "daily trend"),
    ("4h", "h4_trend", "4h trend"),
    ("1h", "h1_trend", "1h trend"),
)


def score_context(
    biases: dict,
    range_state,
    liquidity,
    volatility,
    events: list = None,
    relative_volume: float = None,
    weights=DEFAULT_WEIGHTS,
) -> ContextScore:
    """Signed context score with its component breakdown.

    Positive is bullish. `relative_volume` above 1 means the recent
    candles drew more participation than usual, which confirms whatever
    direction the rest of the evidence points at.
    """
    components = []

    for timeframe, attribute, label in TREND_COMPONENTS:
        trend = biases.get(timeframe)
        if trend is None or trend in (Trend.UNDEFINED, Trend.RANGING):
            continue
        direction = 1.0 if trend is Trend.UP else -1.0
        weight = getattr(weights, attribute)
        components.append(
            ScoreComponent(
                name=label,
                weight=weight,
                value=direction,
                contribution=weight * direction,
            )
        )

    if range_state is not None and range_state.zone is not Zone.EQUILIBRIUM:
        # Discount favours longs, premium favours shorts: this is a
        # location edge, independent of direction of travel.
        direction = 1.0 if range_state.zone is Zone.DISCOUNT else -1.0
        components.append(
            ScoreComponent(
                name=f"{range_state.name} range {range_state.zone.value.lower()}",
                weight=weights.above_weekly_equilibrium,
                value=direction,
                contribution=weights.above_weekly_equilibrium * direction,
            )
        )

    sweep = _sweep_direction(liquidity)
    if sweep:
        components.append(
            ScoreComponent(
                name="liquidity sweep reclaimed",
                weight=weights.liquidity_sweep,
                value=sweep,
                contribution=weights.liquidity_sweep * sweep,
            )
        )

    directional_total = sum(c.contribution for c in components)
    sign = 1.0 if directional_total > 0 else (-1.0 if directional_total < 0 else 0.0)

    if relative_volume is not None and relative_volume > 1.0 and sign:
        components.append(
            ScoreComponent(
                name="volume confirmation",
                weight=weights.volume_confirmation,
                value=sign,
                contribution=weights.volume_confirmation * sign,
            )
        )

    # Penalties: signed against the prevailing direction so they always
    # reduce conviction rather than inventing an opposite thesis.
    if _has_high_impact_event(events) and sign:
        components.append(
            ScoreComponent(
                name="high impact event",
                weight=weights.high_impact_event,
                value=sign,
                contribution=abs(weights.high_impact_event) * -sign,
            )
        )

    if volatility is not None and volatility.regime is VolatilityRegime.EXTREME and sign:
        components.append(
            ScoreComponent(
                name="extreme volatility",
                weight=weights.extreme_volatility,
                value=sign,
                contribution=abs(weights.extreme_volatility) * -sign,
            )
        )

    total = round(sum(c.contribution for c in components), 2)
    return ContextScore(
        total=total,
        label=label_for(total),
        weights_version=WEIGHTS_VERSION,
        components=components,
    )


def label_for(total: float) -> str:
    """Bucket a score into its bias label.

    SCORE_THRESHOLDS is ordered from most bullish down, and each entry
    is an inclusive floor, so the first match wins and anything below
    the last threshold falls through to STRONG_BEARISH.
    """
    for threshold, label in SCORE_THRESHOLDS:
        if total >= threshold:
            return label
    return "STRONG_BEARISH"


def _sweep_direction(liquidity) -> float:
    """+1 when lows were swept and reclaimed (bullish), -1 for highs.

    Only reclaimed sweeps count. A level taken and held is a breakout,
    which the range/trend components already reflect — counting it here
    too would double-weight the same evidence.
    """
    if liquidity is None or not liquidity.events:
        return 0.0

    latest = liquidity.events[-1]
    if not latest.reclaimed:
        return 0.0
    if latest.kind is LiquidityEventKind.SWEEP_LOW:
        return 1.0
    if latest.kind is LiquidityEventKind.SWEEP_HIGH:
        return -1.0
    return 0.0


def _has_high_impact_event(events: list) -> bool:
    return any(str(e.importance).upper() == "HIGH" for e in (events or []))
