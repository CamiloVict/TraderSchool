"""Every tunable the context engine uses, in one auditable place.

Deliberately separate from the repo-root config.py: those are
deployment/secret settings read from .env (API keys, symbol, testnet
flag). These are *model* parameters — the numbers that decide what
counts as a swing, when volatility is "HIGH", how much a daily trend
is worth in the score. They belong to the strategy, get versioned
alongside it, and must be reproducible from a saved context snapshot.

Nothing here is claimed to be optimal. They are conservative starting
points chosen to be readable; optimizing them requires the
walk-forward machinery that does not exist yet, and any value that
does get optimized should be documented as such.
"""
from dataclasses import dataclass, field

# Bumped whenever the meaning of a context field changes, so an old
# snapshot is never silently compared against a new one.
CONTEXT_ENGINE_VERSION = "0.1.0"
# Bumped independently: re-weighting the score does not change what
# the other fields mean, but it does invalidate score comparisons.
WEIGHTS_VERSION = "0.1.0"

# --- Timeframes ------------------------------------------------------------
# Ordered high -> low. The engine reads context from the top and timing
# from the bottom (master prompt section 6).
TIMEFRAMES = ("1w", "1d", "4h", "1h", "15m")

# Pandas resample rules for each derived timeframe. "W-MON" anchors
# weekly bars to Monday 00:00 UTC (crypto trades through the weekend,
# so there is no session-close convention to follow — Monday is simply
# the ISO week boundary).
RESAMPLE_RULES = {
    "15m": "15min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1D",
    "1w": "W-MON",
}

# Minutes per bar, used to build the expected timestamp grid when
# checking for missing candles.
TIMEFRAME_MINUTES = {
    "15m": 15,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
    "1w": 10080,
}

# --- Structure -------------------------------------------------------------
# A swing high needs SWING_RIGHT candles to its right before it can be
# called a swing at all. That lag is the whole reason swings carry a
# `confirmed_at`: a larger value means more reliable pivots but a
# later — never earlier — signal.
SWING_LEFT = 2
SWING_RIGHT = 2
# How many confirmed swings of each kind to keep when labelling the
# HH/HL/LH/LL sequence.
STRUCTURE_SEQUENCE_LENGTH = 6
# A break of structure must *close* beyond the swing, not just wick
# through it, plus this buffer expressed as a fraction of ATR. Wick-only
# breaks are how stop hunts masquerade as trend changes.
BOS_ATR_BUFFER = 0.1

# --- Volatility ------------------------------------------------------------
ATR_PERIOD = 14
RSI_PERIOD = 14
# Window used to rank the current ATR% against its own recent history.
VOLATILITY_LOOKBACK = 100
# Percentile cutoffs (of ATR% over VOLATILITY_LOOKBACK bars) separating
# the five volatility regimes.
VOLATILITY_PERCENTILES = {
    "VERY_LOW": 10.0,
    "LOW": 30.0,
    "NORMAL": 70.0,
    "HIGH": 90.0,
}
# ATR must exceed its own average by this ratio to count as expanding.
VOLATILITY_EXPANSION_RATIO = 1.2
VOLATILITY_CONTRACTION_RATIO = 0.8

# --- Range position --------------------------------------------------------
# Below DISCOUNT_MAX of the range is a discount, above PREMIUM_MIN is a
# premium, and the band between them is equilibrium — the zone where
# reward-to-risk is worst in either direction.
DISCOUNT_MAX = 40.0
PREMIUM_MIN = 60.0
# Bars of each timeframe used to define the "swing range" high/low.
SWING_RANGE_LOOKBACK = 60

# --- Liquidity -------------------------------------------------------------
# Two highs count as "equal" (a liquidity pool) when they sit within
# this fraction of ATR of each other.
EQUAL_LEVEL_ATR_TOLERANCE = 0.15
# How many recent bars to scan for equal highs/lows.
EQUAL_LEVEL_LOOKBACK = 40
# After price trades through a level, it has this many bars to close
# back inside before the move stops counting as a sweep and starts
# counting as a genuine breakout.
SWEEP_RECLAIM_BARS = 3
# A candle body larger than this multiple of ATR counts as
# displacement — the aggressive follow-through that separates a real
# reversal from a drift back through the level.
DISPLACEMENT_ATR_MULTIPLE = 1.0
# Bars back over which a sweep is still considered "recent" context.
SWEEP_LOOKBACK = 20

# --- Sessions --------------------------------------------------------------
# Fixed UTC windows. Real London/New York hours shift with DST; a fixed
# window is the deterministic simplification, and it is wrong by an
# hour for part of the year. Handling DST properly means mapping to
# Europe/London and America/New_York, which is deferred.
SESSION_WINDOWS = {
    "ASIA": (0, 8),
    "LONDON": (7, 16),
    "NEW_YORK": (12, 21),
}

# --- Data quality ----------------------------------------------------------
# Fraction of expected candles that may be missing before the context
# is marked degraded.
MAX_MISSING_CANDLE_RATIO = 0.02
# A gap between consecutive closes larger than this multiple of ATR is
# flagged as anomalous (exchange outage, bad print, wrong symbol).
GAP_ATR_MULTIPLE = 10.0
# Fewest bars a timeframe needs before its structure is trustworthy.
MIN_CANDLES_PER_TIMEFRAME = 60


@dataclass(frozen=True)
class ScoreWeights:
    """Points each piece of evidence contributes to the context score.

    Positive weights push bullish, negative push bearish, and the
    penalty weights (event, volatility) are applied against whichever
    direction the rest of the evidence points — they shrink conviction
    rather than flipping it.
    """

    daily_trend: float = 2.0
    h4_trend: float = 2.0
    h1_trend: float = 1.0
    weekly_trend: float = 2.0
    above_weekly_equilibrium: float = 1.0
    liquidity_sweep: float = 2.0
    volume_confirmation: float = 1.0
    high_impact_event: float = -2.0
    extreme_volatility: float = -1.0


DEFAULT_WEIGHTS = ScoreWeights()

# Score thresholds -> bias label. Checked from strongest to weakest.
SCORE_THRESHOLDS = (
    (6.0, "STRONG_BULLISH"),
    (3.0, "BULLISH"),
    (-3.0, "NEUTRAL"),
    (-6.0, "BEARISH"),
)

# --- Risk / no-trade -------------------------------------------------------
# Minutes before a high-impact event during which new entries are
# blocked.
EVENT_BLACKOUT_MINUTES = 30
# Conviction below this makes "no trade" the honest answer.
MIN_CONFIDENCE = 0.35


@dataclass(frozen=True)
class ContextParams:
    """Bundle of everything above, so a caller can override parameters
    without mutating module state (which would leak across backtest
    runs and make results irreproducible)."""

    swing_left: int = SWING_LEFT
    swing_right: int = SWING_RIGHT
    atr_period: int = ATR_PERIOD
    rsi_period: int = RSI_PERIOD
    volatility_lookback: int = VOLATILITY_LOOKBACK
    swing_range_lookback: int = SWING_RANGE_LOOKBACK
    equal_level_lookback: int = EQUAL_LEVEL_LOOKBACK
    sweep_lookback: int = SWEEP_LOOKBACK
    weights: ScoreWeights = field(default_factory=lambda: DEFAULT_WEIGHTS)


DEFAULT_PARAMS = ContextParams()
