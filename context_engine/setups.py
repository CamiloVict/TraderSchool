"""Setup Engine (master prompt section 17).

The rule this module exists to enforce: "never treat an isolated
pattern as a sufficient signal to trade." A liquidity sweep is not an
entry by itself (context_engine.liquidity's own docstring says so); a
bullish HTF bias is not an entry by itself either. A setup fires only
when several independent pieces of evidence — bias, a liquidity event,
its reclaim, its displacement, and lower-timeframe structure —
converge on the same conclusion at the same time.

Scope: LIQUIDITY_SWEEP_RECLAIM (section 43's worked example) uses
inputs the engine already had: bias from bias.py, the swept-and-
reclaimed level from liquidity.py, confirming structure from
structure.py. CHART_PATTERN_REVERSAL reuses patterns.py's reversal-
pattern detector the same way — a confirmed pattern is never sufficient
on its own (patterns.py's own docstring says so), so it only fires when
HTF bias agrees, same rule as the sweep-reclaim setup. Adding another
setup means adding another function here plus a schema.SetupName
member — never a name without a matching detector.

Known simplification, flagged deliberately: this checks that a
qualifying sweep-reclaim event AND a same-direction BOS both hold at
the same as_of point, not that the BOS's break happened strictly after
the sweep resolved. The master prompt's own confirmation chain (sweep
-> reclaim -> displacement -> BOS -> retest -> entry) implies that
ordering, but StructureState only carries the *kind* of the last BOS,
not its timestamp — enforcing strict ordering needs that timestamp
threaded through, which is a real but separate change to
structure.py's contract. Until then this is a same-snapshot
co-occurrence check, still far stricter than a single indicator.
"""
from context_engine.schema import (
    Bias,
    BreakKind,
    Direction,
    Invalidation,
    LiquidityEventKind,
    Setup,
    SetupName,
)

_BULLISH_BIAS = (Bias.BULLISH, Bias.STRONG_BULLISH)
_BEARISH_BIAS = (Bias.BEARISH, Bias.STRONG_BEARISH)


def _latest_event(liquidity, kind: LiquidityEventKind):
    """Most recent liquidity event of exactly `kind`, or None. Not the
    same as `liquidity.recent_event` — that is the last event of *any*
    kind, which may not be the one this setup cares about."""
    matching = [e for e in (liquidity.events if liquidity is not None else []) if e.kind is kind]
    return matching[-1] if matching else None


def detect_liquidity_sweep_reclaim(
    structures: dict,
    liquidity,
    bias: Bias,
    invalidation: Invalidation,
    execution_timeframe: str,
    execution_df=None,
) -> Setup:
    """LIQUIDITY_SWEEP_RECLAIM: HTF bias, a swept level that reclaimed
    with displacement, and a same-direction execution-timeframe BOS
    confirming continuation. Returns None if any leg is missing.

    Mirrors section 17's own example almost exactly: "HTF bullish" +
    "PDL swept" + "Price reclaimed PDL" + "Bullish displacement" +
    "<execution tf> bullish BOS".
    """
    if bias in _BULLISH_BIAS:
        direction = Direction.LONG
        sweep_kind = LiquidityEventKind.SWEEP_LOW
        bos_kind = BreakKind.BULLISH_BOS
        bias_label = "bullish"
        break_label = "bullish"
    elif bias in _BEARISH_BIAS:
        direction = Direction.SHORT
        sweep_kind = LiquidityEventKind.SWEEP_HIGH
        bos_kind = BreakKind.BEARISH_BOS
        bias_label = "bearish"
        break_label = "bearish"
    else:
        return None

    event = _latest_event(liquidity, sweep_kind)
    if event is None or not event.reclaimed or not event.displacement:
        return None

    execution_structure = structures.get(execution_timeframe)
    if execution_structure is None or execution_structure.last_bos is not bos_kind:
        return None

    reasons = [
        f"HTF bias {bias_label}",
        f"{event.level_name} swept at {event.level:g}",
        f"price reclaimed {event.level_name}",
        f"{break_label} displacement on the reclaim",
        f"{execution_timeframe} {break_label} BOS",
    ]
    return Setup(name=SetupName.LIQUIDITY_SWEEP_RECLAIM, direction=direction, reasons=reasons, invalidation=invalidation)


def detect_chart_pattern_reversal(
    structures: dict,
    liquidity,
    bias: Bias,
    invalidation: Invalidation,
    execution_timeframe: str,
    execution_df=None,
) -> Setup:
    """CHART_PATTERN_REVERSAL: a classical reversal chart pattern
    (double-top/bottom, head-and-shoulders/inverse, or triangle —
    patterns.detect_reversal_patterns) confirmed within the last
    PATTERN_VETO_LOOKBACK candles of the execution timeframe, in the
    same direction as HTF bias.

    The bias requirement is the whole point, not an afterthought: a
    reversal pattern with no HTF bias behind it is exactly the "isolated
    pattern" patterns.py's own docstring says is not a sufficient
    signal (weak evidence even as confirmation, per the academic
    literature it cites). Requiring both to agree is what turns it into
    one, on the same footing as LIQUIDITY_SWEEP_RECLAIM's own bias +
    liquidity-event + BOS conjunction.

    Returns None if `execution_df` is missing/too short for pattern
    detection, or no qualifying pattern confirmed recently.
    """
    # Imported here, not at module level: patterns.py itself imports
    # context_engine.schema/structure, and this package's __init__
    # imports engine.py -> setups.py before patterns.py would finish
    # initializing -- a module-level import here is a circular import
    # (patterns -> context_engine -> setups -> patterns, mid-init).
    # Deferring it until this function actually runs sidesteps that.
    from patterns import PATTERN_VETO_LOOKBACK, detect_reversal_patterns

    if execution_df is None or len(execution_df) < 20:
        return None

    if bias in _BULLISH_BIAS:
        direction = Direction.LONG
        wanted_signal = 1
        label = "bullish"
    elif bias in _BEARISH_BIAS:
        direction = Direction.SHORT
        wanted_signal = -1
        label = "bearish"
    else:
        return None

    signal = detect_reversal_patterns(execution_df)
    recent = signal.iloc[-PATTERN_VETO_LOOKBACK:]
    if wanted_signal not in recent.values:
        return None

    reasons = [
        f"HTF bias {label}",
        f"a {label} reversal chart pattern confirmed within the last {PATTERN_VETO_LOOKBACK} "
        f"{execution_timeframe} candles",
    ]
    return Setup(name=SetupName.CHART_PATTERN_REVERSAL, direction=direction, reasons=reasons, invalidation=invalidation)


def detect_setups(
    structures: dict,
    liquidity,
    bias: Bias,
    invalidation: Invalidation,
    execution_timeframe: str,
    execution_df=None,
) -> list:
    """Every setup that fires for this snapshot. Order matters only for
    display — nothing downstream currently picks "the first" over
    another; that ranking is left for whoever consumes preferred_setups
    next (the master prompt reserves that judgment for context, not for
    this engine to guess)."""
    detectors = (detect_liquidity_sweep_reclaim, detect_chart_pattern_reversal)
    setups = []
    for detector in detectors:
        setup = detector(structures, liquidity, bias, invalidation, execution_timeframe, execution_df)
        if setup is not None:
            setups.append(setup)
    return setups
