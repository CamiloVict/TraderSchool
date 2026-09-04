"""Order execution against Binance.

Safety rule, non-negotiable while this bot is unproven: every
order-placing function here asserts `config.USE_TESTNET is True`
first. Real-money execution is a deliberate, separate decision for
later — never a side effect of some other change.
"""
import ccxt

from config import USE_TESTNET


class LiveTradingDisabledError(RuntimeError):
    """Raised if code tries to trade with USE_TESTNET=False before
    real-money execution has been explicitly implemented and enabled."""


def _assert_testnet() -> None:
    if not USE_TESTNET:
        raise LiveTradingDisabledError(
            "USE_TESTNET is False. Real-money order execution is not "
            "authorized yet — set BINANCE_TESTNET=true in .env."
        )


def place_market_order(exchange: ccxt.binance, symbol: str, side: str, amount: float) -> dict:
    """Place a market order on Testnet. `side` is 'buy' or 'sell'."""
    _assert_testnet()
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    if amount <= 0:
        raise ValueError(f"amount must be positive, got {amount}")
    return exchange.create_order(symbol, type="market", side=side, amount=amount)


def get_base_asset_balance(exchange: ccxt.binance, symbol: str) -> float:
    """Free balance of the base asset for `symbol` (e.g. 'BTC' in 'BTC/USDT')."""
    base_asset = symbol.split("/")[0]
    balance = exchange.fetch_balance()
    return float(balance.get("free", {}).get(base_asset, 0.0) or 0.0)


def get_quote_asset_balance(exchange: ccxt.binance, symbol: str) -> float:
    """Free balance of the quote asset for `symbol` (e.g. 'USDT' in 'BTC/USDT')."""
    quote_asset = symbol.split("/")[1]
    balance = exchange.fetch_balance()
    return float(balance.get("free", {}).get(quote_asset, 0.0) or 0.0)
