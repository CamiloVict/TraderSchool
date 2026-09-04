"""Multi-timeframe bias and alignment.

Bias per timeframe comes from *structure* — the HH/HL sequence — not
from an indicator. An EMA crossover is a lagging summary of structure;
the sequence is the thing itself, and it is what the reasons in the
snapshot can point at ("Daily structure HH/HL") in a way somebody can
verify on a chart.

The higher timeframe sets context, the lower one sets timing. A
bearish 15m inside a bullish daily is a pullback, not a reversal — so
disagreement between adjacent timeframes is expected and must not be
reported as conflict on its own. Only disagreement among the *higher*
timeframes is a real conflict, because then there is no context left
to trade inside of (master prompt section 6).
"""
from context_engine.schema import Alignment, Bias, Trend

# Higher timeframes first. Weights decide how much each one moves the
# aggregate bias: context outranks timing.
TIMEFRAME_WEIGHTS = {
    "1w": 3.0,
    "1d": 3.0,
    "4h": 2.0,
    "1h": 1.0,
    "15m": 0.5,
}

# The timeframes that define context. Disagreement here is what makes
# a setup unsafe, regardless of what the fast charts are doing.
HIGHER_TIMEFRAMES = ("1w", "1d", "4h")


def timeframe_bias(structures: dict) -> dict:
    """{timeframe: Trend} straight from each timeframe's structure."""
    return {timeframe: state.trend for timeframe, state in structures.items()}


def assess_alignment(biases: dict) -> Alignment:
    """How much the timeframes agree.

    STRONG when every higher timeframe points the same way, CONFLICT
    when the higher timeframes contradict each other, PARTIAL for
    everything in between (typically a pullback on the fast charts).
    """
    higher = [biases[tf] for tf in HIGHER_TIMEFRAMES if tf in biases]
    directional = [t for t in higher if t in (Trend.UP, Trend.DOWN)]

    if not directional:
        return Alignment.PARTIAL_ALIGNMENT

    up = sum(1 for t in directional if t is Trend.UP)
    down = sum(1 for t in directional if t is Trend.DOWN)

    if up and down:
        return Alignment.CONFLICT

    # Unanimous across the higher timeframes, and every one of them
    # actually had a direction to give.
    if len(directional) == len(higher) and len(higher) >= 2:
        return Alignment.STRONG_ALIGNMENT

    return Alignment.PARTIAL_ALIGNMENT


def aggregate_bias(biases: dict) -> tuple:
    """(Bias, confidence) from the weighted vote of every timeframe.

    Confidence is the share of available weight pulling one way, so it
    falls naturally when timeframes disagree — no separate penalty
    needed. It is a measure of agreement, explicitly not a probability
    of the trade working.
    """
    total_weight = 0.0
    net = 0.0

    for timeframe, trend in biases.items():
        weight = TIMEFRAME_WEIGHTS.get(timeframe, 1.0)
        total_weight += weight
        if trend is Trend.UP:
            net += weight
        elif trend is Trend.DOWN:
            net -= weight

    if not total_weight:
        return Bias.NEUTRAL, 0.0

    ratio = net / total_weight
    confidence = round(abs(ratio), 2)

    if ratio >= 0.6:
        return Bias.STRONG_BULLISH, confidence
    if ratio >= 0.25:
        return Bias.BULLISH, confidence
    if ratio <= -0.6:
        return Bias.STRONG_BEARISH, confidence
    if ratio <= -0.25:
        return Bias.BEARISH, confidence
    return Bias.NEUTRAL, confidence


def describe_biases(structures: dict) -> list:
    """Human-readable reasons, one per timeframe with a real trend.

    These are the `reasons` in the snapshot: each one names the
    timeframe and the evidence, so it can be checked rather than
    believed.
    """
    reasons = []
    for timeframe in ("1w", "1d", "4h", "1h", "15m"):
        state = structures.get(timeframe)
        if state is None or state.trend in (Trend.UNDEFINED, Trend.RANGING):
            continue
        sequence = "/".join(point.value for point in state.sequence[-2:])
        label = "bullish" if state.trend is Trend.UP else "bearish"
        detail = f" ({sequence})" if sequence else ""
        reasons.append(f"{timeframe} structure {label}{detail}")
    return reasons
