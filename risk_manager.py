"""Risk management: position sizing, stop-loss/take-profit levels, and a
daily loss circuit breaker.

Required by executor.py before any order (even on Testnet) is placed.

Key idea behind `position_size`: risk a fixed % of capital PER TRADE,
not a fixed % of the position. If capital is $1000 and
RISK_PER_TRADE_PCT is 1%, a trade should lose at most $10 if the
stop-loss is hit — the position size is derived backwards from that,
given how far away (in %) the stop-loss is.
"""
from dataclasses import dataclass, field
from datetime import date

from config import MAX_DAILY_LOSS_PCT, RISK_PER_TRADE_PCT, STOP_LOSS_PCT, TAKE_PROFIT_PCT


def stop_loss_price(entry_price: float, side: str = "long") -> float:
    """Price at which an open trade should be closed to cap the loss."""
    if side == "long":
        return entry_price * (1 - STOP_LOSS_PCT / 100)
    return entry_price * (1 + STOP_LOSS_PCT / 100)


def take_profit_price(entry_price: float, side: str = "long") -> float:
    """Price at which an open trade should be closed to bank the gain."""
    if side == "long":
        return entry_price * (1 + TAKE_PROFIT_PCT / 100)
    return entry_price * (1 - TAKE_PROFIT_PCT / 100)


def position_size(capital: float, entry_price: float, side: str = "long", stop_price: float = None) -> float:
    """Position size (in base asset units, e.g. BTC) such that hitting
    the stop-loss loses exactly RISK_PER_TRADE_PCT of `capital`.

    Example: $1000 capital, 1% risk => willing to lose $10. Stop-loss
    is STOP_LOSS_PCT=2% away from entry => position value can be
    $10 / 0.02 = $500, so size = $500 / entry_price. Capped so the
    position never exceeds the available capital (relevant when
    STOP_LOSS_PCT is set very tight).

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

    position_value = min(risk_amount / price_risk_pct, capital)
    return position_value / entry_price


@dataclass
class DailyLossTracker:
    """Tracks realized P&L for the current UTC day and enforces the
    MAX_DAILY_LOSS_PCT circuit breaker.

    In-memory only — resets if the process restarts. That's an
    accepted limitation for now: on Testnet the cost of under-counting
    a day's losses after a crash is zero. Revisit before real capital.

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

    def trading_allowed(self) -> bool:
        self._roll_day_if_needed()
        if self.starting_capital <= 0:
            return False
        loss_pct = -self._realized_pnl / self.starting_capital * 100
        return loss_pct < MAX_DAILY_LOSS_PCT
