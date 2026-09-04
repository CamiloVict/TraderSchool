"""Typed contract for everything the context engine emits.

Two rules shape this module:

1. No free text where a decision is expressed. A bias is a member of
   an enum, not the string "kind of bullish" — so a downstream consumer
   (or a backtest grouping trades by regime) can never be surprised by
   a value it has not seen before.

2. Every hypothesis carries its own evidence. `reasons` says why,
   `invalidations` says what would prove it wrong. A snapshot that
   claims BULLISH without either is not falsifiable and therefore not
   useful (master prompt section 3.3).

Enums subclass `str` so `dataclasses.asdict` and `json.dump` serialize
them to their plain string value with no custom encoder.
"""
from dataclasses import asdict, dataclass, field
from enum import Enum


def _plain(value):
    """Recursively replace enum members with their string values.

    `asdict` flattens the dataclass tree but leaves enums intact
    wherever they appear — as a field, inside a list like
    `StructureState.sequence`, or as a dict value like
    `multi_timeframe`. This walks the result and strips all of them.
    """
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


class Bias(str, Enum):
    STRONG_BULLISH = "STRONG_BULLISH"
    BULLISH = "BULLISH"
    NEUTRAL = "NEUTRAL"
    BEARISH = "BEARISH"
    STRONG_BEARISH = "STRONG_BEARISH"


class Trend(str, Enum):
    """Structural direction of a single timeframe — distinct from Bias,
    which is the graded conviction built on top of several of these."""

    UP = "UP"
    DOWN = "DOWN"
    RANGING = "RANGING"
    UNDEFINED = "UNDEFINED"


class Alignment(str, Enum):
    STRONG_ALIGNMENT = "STRONG_ALIGNMENT"
    PARTIAL_ALIGNMENT = "PARTIAL_ALIGNMENT"
    CONFLICT = "CONFLICT"


class RegimeKind(str, Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    TRANSITION = "TRANSITION"


class Phase(str, Enum):
    IMPULSE = "IMPULSE"
    PULLBACK = "PULLBACK"
    CONSOLIDATION = "CONSOLIDATION"
    EXPANSION = "EXPANSION"
    COMPRESSION = "COMPRESSION"
    UNDEFINED = "UNDEFINED"


class VolatilityRegime(str, Enum):
    VERY_LOW = "VERY_LOW"
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


class Zone(str, Enum):
    PREMIUM = "PREMIUM"
    EQUILIBRIUM = "EQUILIBRIUM"
    DISCOUNT = "DISCOUNT"


class SwingKind(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class StructurePoint(str, Enum):
    HH = "HH"
    HL = "HL"
    LH = "LH"
    LL = "LL"


class BreakKind(str, Enum):
    """BOS continues the existing trend; CHOCH is the first break
    *against* it — the earliest structural hint of a reversal."""

    BULLISH_BOS = "BULLISH_BOS"
    BEARISH_BOS = "BEARISH_BOS"
    BULLISH_CHOCH = "BULLISH_CHOCH"
    BEARISH_CHOCH = "BEARISH_CHOCH"


class LiquidityEventKind(str, Enum):
    SWEEP_HIGH = "SWEEP_HIGH"
    SWEEP_LOW = "SWEEP_LOW"
    LIQUIDITY_RECLAIM = "LIQUIDITY_RECLAIM"
    FAILED_BREAKOUT = "FAILED_BREAKOUT"
    LIQUIDITY_EXPANSION = "LIQUIDITY_EXPANSION"


class MarketState(str, Enum):
    RANGE = "RANGE"
    RANGE_EXPANSION = "RANGE_EXPANSION"
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    PULLBACK = "PULLBACK"
    BREAKOUT_ATTEMPT = "BREAKOUT_ATTEMPT"
    FAILED_BREAKOUT = "FAILED_BREAKOUT"
    REVERSAL_ATTEMPT = "REVERSAL_ATTEMPT"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    NO_TRADE = "NO_TRADE"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NONE = "NONE"


class SetupName(str, Enum):
    """Master prompt section 17. One member per setup the Setup Engine
    knows how to detect — deliberately starting with just one; adding a
    name here without a matching detector in context_engine/setups.py
    would be exactly the kind of setup the design forbids (defined but
    never actually checked)."""

    LIQUIDITY_SWEEP_RECLAIM = "LIQUIDITY_SWEEP_RECLAIM"


class Severity(str, Enum):
    """A data problem that is `FATAL` stops the engine; `WARNING` lets
    it run but stamps the snapshot as degraded so a downstream reader
    can discount it."""

    WARNING = "WARNING"
    FATAL = "FATAL"


@dataclass(frozen=True)
class DataIssue:
    code: str
    severity: Severity
    timeframe: str
    detail: str


@dataclass(frozen=True)
class DataQuality:
    valid: bool
    degraded: bool
    issues: list[DataIssue] = field(default_factory=list)


@dataclass(frozen=True)
class Swing:
    """A confirmed pivot.

    `timestamp` is when the extreme printed; `confirmed_at` is when it
    became knowable (SWING_RIGHT candles later). Any consumer filtering
    on `timestamp` instead of `confirmed_at` has silently introduced
    look-ahead bias, which is why both are always carried together.
    """

    timestamp: str
    price: float
    kind: SwingKind
    confirmed_at: str


@dataclass(frozen=True)
class StructureBreak:
    kind: BreakKind
    level: float
    broken_at: str
    reference_swing: str


@dataclass(frozen=True)
class StructureState:
    trend: Trend
    sequence: list[StructurePoint]
    last_bos: BreakKind | None
    last_choch: BreakKind | None
    phase: Phase
    last_swing_high: float | None
    last_swing_low: float | None
    swings: list[Swing] = field(default_factory=list)


@dataclass(frozen=True)
class LiquidityLevel:
    name: str
    price: float
    kind: SwingKind
    swept: bool = False


@dataclass(frozen=True)
class LiquidityEvent:
    kind: LiquidityEventKind
    level_name: str
    level: float
    occurred_at: str
    reclaimed: bool
    displacement: bool


@dataclass(frozen=True)
class LiquidityState:
    levels: list[LiquidityLevel] = field(default_factory=list)
    equal_highs: list[float] = field(default_factory=list)
    equal_lows: list[float] = field(default_factory=list)
    events: list[LiquidityEvent] = field(default_factory=list)
    recent_event: LiquidityEventKind | None = None


@dataclass(frozen=True)
class VolatilityState:
    atr: float
    atr_percent: float
    regime: VolatilityRegime
    expansion: bool
    contraction: bool
    percentile: float


@dataclass(frozen=True)
class RangeState:
    """Premium/discount is meaningless without saying premium *of what*,
    so the range name travels with the zone (master prompt section 10)."""

    name: str
    high: float
    low: float
    position_percent: float
    zone: Zone


@dataclass(frozen=True)
class SessionState:
    current: str | None
    high: float | None
    low: float | None
    range: float | None
    previous: str | None
    previous_high: float | None
    previous_low: float | None


@dataclass(frozen=True)
class MarketEvent:
    """Supplied by the caller from a trusted calendar. The engine never
    invents one (master prompt section 12)."""

    event: str
    importance: str
    time: str
    currency: str
    minutes_to_event: int | None = None


@dataclass(frozen=True)
class Regime:
    primary: RegimeKind
    volatility: VolatilityRegime
    phase: Phase


@dataclass(frozen=True)
class BiasHypothesis:
    direction: Bias
    confidence: float
    reasons: list[str] = field(default_factory=list)
    invalidations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ScoreComponent:
    name: str
    weight: float
    value: float
    contribution: float


@dataclass(frozen=True)
class ContextScore:
    total: float
    label: str
    weights_version: str
    components: list[ScoreComponent] = field(default_factory=list)


@dataclass(frozen=True)
class Invalidation:
    type: str
    level: float | None
    detail: str


@dataclass(frozen=True)
class Setup:
    """One detected trade setup (master prompt section 17/35), in the
    same evidence-carrying shape as BiasHypothesis: a name and
    direction alone are not falsifiable, so every setup also carries
    what confirmed it (`reasons`) and what would prove it wrong
    (`invalidation`) — reusing the same Invalidation the bias itself
    uses, since for this setup they are the same structural level."""

    name: SetupName
    direction: Direction
    reasons: list[str]
    invalidation: Invalidation


@dataclass(frozen=True)
class ContextSnapshot:
    """The Daily Market Context. One immutable answer to "what is the
    market doing right now, and what would prove me wrong"."""

    timestamp: str
    asset: str
    version: str
    data_quality: DataQuality
    regime: Regime
    multi_timeframe: dict[str, Trend]
    alignment: Alignment
    structure: dict[str, StructureState]
    liquidity: LiquidityState
    volatility: VolatilityState
    range: RangeState
    sessions: SessionState
    events: list[MarketEvent]
    bias: BiasHypothesis
    context_score: ContextScore
    market_state: MarketState
    # None only for the very first snapshot in a sequence (no prior
    # state to have transitioned from). See state_machine.next_state —
    # market_state above is already the post-transition value; this is
    # the state it moved on from, kept for observability (was this
    # state reached the normal way, or did the transition table hold
    # it here from something else last time?).
    previous_market_state: MarketState | None
    preferred_direction: Direction
    setups: list[Setup]
    # Names only, derived from `setups` above — kept as a separate,
    # simpler field because it existed before setups did and other
    # code (the dashboard, the CLI summary) already reads it that way.
    preferred_setups: list[str]
    avoid: list[str]
    no_trade: bool
    invalidation: Invalidation
    risk: dict

    def to_dict(self) -> dict:
        """Plain JSON-serializable dict.

        Enums are unwrapped to their string values. They would already
        serialize correctly through `json.dumps` (every one subclasses
        `str`), but leaving enum objects in the dict makes it awkward
        to inspect and compare, so they are flattened here instead.
        """
        return _plain(asdict(self))
