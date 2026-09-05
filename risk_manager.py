"""Risk management: position sizing, stop-loss/take-profit levels, and a
daily loss circuit breaker.

Required by executor.py before any order (even on Testnet) is placed.

Key idea behind `position_size`: risk a fixed % of capital PER TRADE,
not a fixed % of the position. If capital is $1000 and
RISK_PER_TRADE_PCT is 1%, a trade should lose at most $10 if the
stop-loss is hit — the position size is derived backwards from that,
given how far away (in %) the stop-loss is.

That $10 cap only holds if it counts *everything* the stop-out costs,
not just the price move: `position_size` also folds in the round-trip
taker fee (one fill to enter, one to exit), so a stopped-out trade's
real total loss — price move plus both fees — is what's actually
bounded to RISK_PER_TRADE_PCT, not just its price component. Before
this, a stop-out's real loss was quietly RISK_PER_TRADE_PCT + fees,
understating the very number the whole risk-limit system (daily/weekly
loss trackers, consecutive-loss halt, portfolio risk) is built around.
"""
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from config import (
    MAX_DAILY_LOSS_PCT,
    MAX_STOP_DISTANCE_ATR_MULTIPLE,
    MAX_WEEKLY_LOSS_PCT,
    MIN_STOP_DISTANCE_ATR_MULTIPLE,
    RISK_PER_TRADE_PCT,
    STOP_LOSS_PCT,
    STRUCTURAL_STOP_ATR_BUFFER_MULTIPLE,
    TAKE_PROFIT_PCT,
    TAKER_FEE_PCT,
)


def stop_loss_price(entry_price: float, side: str = "long") -> float:
    """Price at which an open trade should be closed to cap the loss."""
    if side == "long":
        return entry_price * (1 - STOP_LOSS_PCT / 100)
    return entry_price * (1 + STOP_LOSS_PCT / 100)


def validate_stop_distance(entry_price: float, stop_price: float, atr: float) -> tuple:
    """(ok, reason): whether `stop_price` sits within
    [MIN_STOP_DISTANCE_ATR_MULTIPLE, MAX_STOP_DISTANCE_ATR_MULTIPLE]
    ATRs of `entry_price`. `reason` is None when `ok` is True.

    Too close: inside normal noise -- the stop would trigger on
    nothing meaningful, not on the trade's premise actually breaking.
    Too far: a degenerate level (bad data, a gap, a bug in whatever
    computed it) or genuinely excessive structural risk for one trade
    -- position_size() would still cap the $ risk correctly, but a
    stop this wide is worth rejecting and looking at rather than
    silently sizing a tiny position around it.

    `atr` of None or <=0 (not enough history to compute one yet) skips
    validation rather than blocking on missing data -- the same
    fail-open posture as this repo's other diagnostic-only checks
    (see portfolio_risk.py).
    """
    if atr is None or atr <= 0:
        return True, None
    distance_atr = abs(entry_price - stop_price) / atr
    if distance_atr < MIN_STOP_DISTANCE_ATR_MULTIPLE:
        return False, (
            f"stop is {distance_atr:.2f}x ATR from entry, below the "
            f"{MIN_STOP_DISTANCE_ATR_MULTIPLE}x minimum -- likely inside normal noise"
        )
    if distance_atr > MAX_STOP_DISTANCE_ATR_MULTIPLE:
        return False, (
            f"stop is {distance_atr:.2f}x ATR from entry, above the "
            f"{MAX_STOP_DISTANCE_ATR_MULTIPLE}x maximum -- degenerate level or excessive structural risk"
        )
    return True, None


def structural_stop_price(
    df: pd.DataFrame,
    entry_price: float,
    side: str = "long",
    atr_buffer_multiple: float = STRUCTURAL_STOP_ATR_BUFFER_MULTIPLE,
) -> float:
    """Stop derived from where the trade's own premise breaks, not a
    flat %: the most recent confirmed swing against `df` (the last
    swing low for a long, the last swing high for a short), pushed
    `atr_buffer_multiple` ATRs further out so the stop has room to
    breathe instead of resting exactly on the level that invalidates
    the trade -- same idea context_engine's own Invalidation.level
    already uses as the Setup Engine's stop (see main.py, config
    USE_STRUCTURAL_STOP).

    Falls back to the flat stop_loss_price() when there isn't a usable
    swing yet (not enough history) or the swing is already on the
    wrong side of entry_price (degenerate) -- the exact fallback
    pattern already used everywhere else a structural level backs a
    stop in this repo.

    `df` only needs to be one timeframe's OHLC history (whatever the
    caller already has for its own signal) -- context_engine.structure
    is a pure, single-timeframe function with no dependency on the
    rest of that engine's multi-timeframe machinery, so this does not
    require build_context()'s much heavier history fetch.
    """
    from context_engine.features import atr, last_value
    from context_engine.structure import analyze_structure

    structure = analyze_structure(df)
    swing_level = structure.last_swing_low if side == "long" else structure.last_swing_high
    if swing_level is None:
        return stop_loss_price(entry_price, side)

    buffer = (last_value(atr(df)) or 0.0) * atr_buffer_multiple

    if side == "long":
        candidate = swing_level - buffer
        return candidate if candidate < entry_price else stop_loss_price(entry_price, side)

    candidate = swing_level + buffer
    return candidate if candidate > entry_price else stop_loss_price(entry_price, side)


def take_profit_price(entry_price: float, side: str = "long") -> float:
    """Price at which an open trade should be closed to bank the gain."""
    if side == "long":
        return entry_price * (1 + TAKE_PROFIT_PCT / 100)
    return entry_price * (1 - TAKE_PROFIT_PCT / 100)


def position_size(capital: float, entry_price: float, side: str = "long", stop_price: float = None) -> float:
    """Position size (in base asset units, e.g. BTC) such that hitting
    the stop-loss loses exactly RISK_PER_TRADE_PCT of `capital`, fees
    included.

    Example: $1000 capital, 1% risk => willing to lose $10 total.
    Stop-loss is STOP_LOSS_PCT=2% away from entry, plus a 0.2%
    round-trip taker fee (TAKER_FEE_PCT=0.1% each way) => the position
    value that loses exactly $10 across both is $10 / 0.022 ≈ $454.55,
    not the $500 a fee-blind calculation would give — sizing without
    the fee term understates the trade's real worst-case loss by
    however much the round trip costs. Capped so the position never
    exceeds the available capital (relevant when STOP_LOSS_PCT is set
    very tight).

    `stop_price`: an explicit stop level (e.g. a Setup Engine's
    structural invalidation) to size against instead of the flat
    STOP_LOSS_PCT distance below/above entry. Sizing is the only thing
    this changes — placing the actual stop order is still the caller's
    job, same as before.
    """
    if capital <= 0 or entry_price <= 0:
        return 0.0

    risk_amount = capital * (RISK_PER_TRADE_PCT / 100)
    if stop_price is None:
        stop_price = stop_loss_price(entry_price, side)
    price_risk_pct = abs(entry_price - stop_price) / entry_price
    if price_risk_pct == 0:
        return 0.0

    round_trip_fee_pct = 2 * TAKER_FEE_PCT / 100
    total_risk_pct = price_risk_pct + round_trip_fee_pct
    position_value = min(risk_amount / total_risk_pct, capital)
    return position_value / entry_price


@dataclass
class DailyLossTracker:
    """Tracks realized P&L for the current UTC day and enforces the
    MAX_DAILY_LOSS_PCT circuit breaker.

    In-memory only — resets if the process restarts. That's an
    accepted limitation for now: on Testnet the cost of under-counting
    a day's losses after a crash is zero. Revisit before real capital.

    main.py's --trade cycle runs as a brand-new process every time
    (see its module docstring), which would otherwise reset this every
    single call and make the breaker a no-op — daily_loss_state.py is
    the thin file-backed layer that persists just enough (today's
    starting equity) to build a fresh, correctly-initialized tracker
    each cycle instead.

    Usage: call `record_trade_pnl()` after each closed trade, and
    check `trading_allowed()` before opening a new one.
    """

    starting_capital: float
    _day: date = field(default_factory=date.today, init=False)
    _realized_pnl: float = field(default=0.0, init=False)

    def _roll_day_if_needed(self) -> None:
        today = date.today()
        if today != self._day:
            self._day = today
            self._realized_pnl = 0.0

    def record_trade_pnl(self, pnl: float) -> None:
        self._roll_day_if_needed()
        self._realized_pnl += pnl

    def current_loss_pct(self) -> float:
        """Today's realized loss so far, as a positive percentage of
        the day's starting capital (0 or negative = no net loss yet).
        Exposed separately from trading_allowed() so a caller can log
        *how close* the breaker is, not just whether it tripped."""
        self._roll_day_if_needed()
        if self.starting_capital <= 0:
            return 0.0
        return -self._realized_pnl / self.starting_capital * 100

    def trading_allowed(self) -> bool:
        if self.starting_capital <= 0:
            return False
        return self.current_loss_pct() < MAX_DAILY_LOSS_PCT


@dataclass
class WeeklyLossTracker:
    """Same idea as DailyLossTracker, over the current ISO week
    (Monday-start, UTC) instead of the current UTC day — see that
    class's own docstring for the in-memory-only / weekly_loss_state.py
    rationale, identical here just on a weekly key.

    A bad week can clear the daily limit's bar on every single day
    (a string of small losing days that never individually trips
    MAX_DAILY_LOSS_PCT) and still be a week worth stopping to
    reassess — the two limits are independent, not one replacing
    the other.
    """

    starting_capital: float
    _week: tuple = field(default_factory=lambda: date.today().isocalendar()[:2], init=False)
    _realized_pnl: float = field(default=0.0, init=False)

    def _roll_week_if_needed(self) -> None:
        current_week = date.today().isocalendar()[:2]
        if current_week != self._week:
            self._week = current_week
            self._realized_pnl = 0.0

    def record_trade_pnl(self, pnl: float) -> None:
        self._roll_week_if_needed()
        self._realized_pnl += pnl

    def current_loss_pct(self) -> float:
        """Same idea as DailyLossTracker.current_loss_pct(), over the
        current ISO week."""
        self._roll_week_if_needed()
        if self.starting_capital <= 0:
            return 0.0
        return -self._realized_pnl / self.starting_capital * 100

    def trading_allowed(self) -> bool:
        if self.starting_capital <= 0:
            return False
        return self.current_loss_pct() < MAX_WEEKLY_LOSS_PCT


def consecutive_losses(trades: list) -> int:
    """How many of the most recent *closed* round-trips, in a row, lost
    money — from a trade_journal.py-shaped list of raw fills.

    Pairs each sell with the buy immediately preceding it (FIFO, no
    lot-matching needed: the bot is long-only and single-position, see
    main.py's own docstring, so "the last unmatched buy before this
    sell" is never ambiguous — same rule the dashboard's
    lib/pnl.js.computeClosedTrades uses). A sell with no preceding buy
    (the account already held the asset before the journal started
    tracking it — a real case that already happened once) is skipped
    entirely: an unknown result neither extends nor breaks the streak,
    since guessing it either way would be inventing evidence.
    """
    sorted_trades = sorted(trades, key=lambda t: t.get("timestamp") or 0)

    closed_was_loss = []
    open_buy = None
    for trade in sorted_trades:
        if trade.get("side") == "buy":
            open_buy = trade
        elif trade.get("side") == "sell":
            if open_buy is not None:
                closed_was_loss.append(trade["price"] < open_buy["price"])
            open_buy = None

    streak = 0
    for was_loss in reversed(closed_was_loss):
        if not was_loss:
            break
        streak += 1
    return streak
