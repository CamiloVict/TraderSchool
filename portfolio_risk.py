"""Cross-asset (correlated) risk cap across the two bots this repo
tracks -- PAXG (live, via main.py --trade) and BTC (backtest-only for
now, see README's "Estado del proyecto"): main.py only ever runs ONE
bot per process, each sizing its own entries against only its own
equity slice (risk_manager.position_size()). If both ever trade live
on the same account at once, each could independently open a
RISK_PER_TRADE_PCT position without either knowing the other did,
stacking real simultaneous risk higher than either config value alone
suggests.

Currently dormant in production: scalping_backtester.py's strategy
isn't wired into any live cycle yet, so in practice this almost always
sees zero balance in the other asset and never blocks anything. Built
and tested now anyway, on the same principle as the structural stop
and take-profit experiments earlier in this repo's history -- ready
for the day BTC does trade live, not invented from nothing at that
point.
"""
from config import MAX_PORTFOLIO_RISK_PCT, RISK_PER_TRADE_PCT

# The only two symbols this repo's bots ever trade -- see README's
# "Automatizarlo" section. Hardcoded rather than config-driven because
# adding a third tracked asset is a bigger change than an env var
# (a third bot, its own strategy, its own journal) and would need this
# logic revisited anyway, not just a longer list.
TRACKED_SYMBOLS = ("PAXG/USDT", "BTC/USDT")

# Same $10 dust threshold main.py's own in_position checks use, so
# "does the other bot have a position" means the same thing here as it
# does for the bot actually placing this cycle's order.
DUST_THRESHOLD_USD = 10.0


def _other_tracked_symbol(symbol: str):
    others = [s for s in TRACKED_SYMBOLS if s != symbol]
    return others[0] if len(others) == 1 else None


def other_bot_has_open_position(exchange, symbol: str) -> bool:
    """True if the account holds a non-dust balance of the OTHER
    tracked symbol's base asset. Best-effort: any failure to check
    (the other market isn't listed on this exchange/testnet, a
    transient fetch error) is treated as "can't tell, assume not
    open" -- this is a diagnostic cross-check, not `symbol`'s own
    safety net, so it must never be the reason `symbol`'s own cycle
    fails or blocks a trade it otherwise could evaluate correctly.
    """
    from executor import get_total_base_asset_balance

    other_symbol = _other_tracked_symbol(symbol)
    if other_symbol is None:
        return False
    try:
        balance = get_total_base_asset_balance(exchange, other_symbol)
        if balance <= 0:
            return False
        price = float(exchange.fetch_ticker(other_symbol)["last"])
        return balance * price > DUST_THRESHOLD_USD
    except Exception:
        return False


def portfolio_risk_limit_hit(exchange, symbol: str) -> bool:
    """True if opening a new RISK_PER_TRADE_PCT position in `symbol`
    right now, on top of whatever the OTHER tracked bot already has
    open, would exceed MAX_PORTFOLIO_RISK_PCT.

    Both bots risk the same RISK_PER_TRADE_PCT of their own equity by
    construction, so this is a plain sum of the two, not a
    correlation-adjusted one: PAXG (gold) and BTC don't have an
    established correlation to model, and a plain worst-case sum is
    the honest, conservative default until there's real multi-asset
    trade history to justify anything fancier (see this module's own
    docstring on MAX_PORTFOLIO_RISK_PCT).
    """
    if not other_bot_has_open_position(exchange, symbol):
        return False
    return (RISK_PER_TRADE_PCT * 2) > MAX_PORTFOLIO_RISK_PCT
