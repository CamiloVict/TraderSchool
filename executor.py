"""Order execution against Binance.

Safety rule, non-negotiable while this bot is unproven: every
order-placing function here asserts `config.USE_TESTNET is True`
first. Real-money execution is a deliberate, separate decision for
later — never a side effect of some other change.
"""
from __future__ import annotations

from datetime import datetime, timezone

import ccxt

from config import STOP_LOSS_LIMIT_SLIPPAGE_PCT, USE_TESTNET
from retry import call_with_retries


def _client_order_id(symbol: str, side: str) -> str:
    """Deterministic id for a symbol+side+current-UTC-hour, passed as
    Binance's `newClientOrderId`.

    The failure mode this guards against: a create_order() call times
    out client-side (a ccxt.NetworkError) with no way to tell whether
    the order actually reached Binance and filled anyway. Retrying
    blindly risks placing it twice — this repo deliberately does not
    wrap order placement in retry.call_with_retries for that reason.
    Instead, since a real resubmission (a retry, or the same hourly
    --trade cycle running twice) recomputes the exact same id, Binance
    itself rejects the duplicate rather than executing it a second
    time. Bucketed by hour, not by exact timestamp, because it needs to
    match across two separate process invocations up to an hour apart,
    not just within one.
    """
    compact_symbol = symbol.replace("/", "")
    hour_bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    return f"bot{compact_symbol}{side[0]}{hour_bucket}"


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
    return exchange.create_order(
        symbol,
        type="market",
        side=side,
        amount=amount,
        params={"newClientOrderId": _client_order_id(symbol, side)},
    )


def place_stop_loss_order(
    exchange: ccxt.binance,
    symbol: str,
    amount: float,
    stop_price: float,
    limit_price: float = None,
) -> dict:
    """Place a protective STOP_LOSS_LIMIT sell order on Testnet.

    Once the market trades at/below `stop_price`, this becomes a limit
    sell at `limit_price` — defaulting to a hair below `stop_price`
    (STOP_LOSS_LIMIT_SLIPPAGE_PCT) so it still fills during a fast drop
    instead of resting unfilled above the market. Passing `type="limit"`
    plus a `stopPrice` param is ccxt's unified way of asking Binance for
    a STOP_LOSS_LIMIT order (as opposed to a plain, immediately-active
    limit order).
    """
    _assert_testnet()
    if amount <= 0:
        raise ValueError(f"amount must be positive, got {amount}")
    if stop_price <= 0:
        raise ValueError(f"stop_price must be positive, got {stop_price}")
    if limit_price is None:
        limit_price = stop_price * (1 - STOP_LOSS_LIMIT_SLIPPAGE_PCT / 100)
    return exchange.create_order(
        symbol,
        type="limit",
        side="sell",
        amount=amount,
        price=limit_price,
        params={"stopPrice": stop_price, "newClientOrderId": _client_order_id(symbol, "sell")},
    )


def cancel_order(exchange: ccxt.binance, symbol: str, order_id: str) -> dict | None:
    """Cancel an open order. Not gated by _assert_testnet(): cancelling
    only reduces exposure, so it's safe regardless of trading mode.
    Returns None (instead of raising) if the order already filled or
    was cancelled — the caller's goal ("make sure this order is gone")
    is satisfied either way."""
    try:
        return exchange.cancel_order(order_id, symbol)
    except ccxt.OrderNotFound:
        return None


def get_open_stop_loss_orders(exchange: ccxt.binance, symbol: str) -> list:
    """Open protective stop-loss sell orders for `symbol`.

    Used to check whether an open position already has its stop in
    place, and to cancel it before an EMA-signal exit sells the same
    balance. `triggerPrice` is ccxt's unified field for a stop order's
    trigger — checking it (rather than `type`, which ccxt normalizes
    STOP_LOSS_LIMIT down to plain "limit" for spot) is what actually
    distinguishes a stop order from a regular resting limit order.
    """
    open_orders = call_with_retries(exchange.fetch_open_orders, symbol)
    return [o for o in open_orders if o.get("side") == "sell" and o.get("triggerPrice")]


def get_average_fill_price(order: dict) -> float:
    """Best-effort average fill price for a (typically market) order
    response, used right after a buy to anchor the stop-loss price to
    what was actually paid rather than the signal candle's close."""
    average = order.get("average")
    if average:
        return float(average)
    filled = order.get("filled") or 0
    cost = order.get("cost") or 0
    if filled:
        return float(cost) / float(filled)
    return float(order.get("price") or 0)


def get_last_fill_price(exchange: ccxt.binance, symbol: str, side: str) -> float:
    """Average price of the most recent filled trade on `side` ('buy'
    or 'sell') for `symbol`. Used to reconstruct an entry price when no
    local state is kept — e.g. self-healing a missing stop-loss order
    after a crash between the buy and the stop-loss placement. Returns
    0.0 if no matching trade is found."""
    trades = call_with_retries(exchange.fetch_my_trades, symbol, limit=20)
    for trade in reversed(trades):
        if trade.get("side") == side:
            return float(trade["price"])
    return 0.0


def get_base_asset_balance(exchange: ccxt.binance, symbol: str) -> float:
    """Free (available to trade/withdraw) balance of the base asset for
    `symbol` (e.g. 'BTC' in 'BTC/USDT'). An open stop-loss order locks
    the coins it covers out of this — use get_total_base_asset_balance()
    to check "are we in a position" once stop-loss orders are in play."""
    base_asset = symbol.split("/")[0]
    balance = call_with_retries(exchange.fetch_balance)
    return float(balance.get("free", {}).get(base_asset, 0.0) or 0.0)


def get_total_base_asset_balance(exchange: ccxt.binance, symbol: str) -> float:
    """Free + locked balance of the base asset. 'Locked' includes
    whatever an open protective stop-loss order has reserved, so this
    (not the free-only balance) is the correct measure of "are we in a
    position" once stop-loss orders are in play."""
    base_asset = symbol.split("/")[0]
    balance = call_with_retries(exchange.fetch_balance)
    return float(balance.get("total", {}).get(base_asset, 0.0) or 0.0)


def get_quote_asset_balance(exchange: ccxt.binance, symbol: str) -> float:
    """Free balance of the quote asset for `symbol` (e.g. 'USDT' in 'BTC/USDT')."""
    quote_asset = symbol.split("/")[1]
    balance = call_with_retries(exchange.fetch_balance)
    return float(balance.get("free", {}).get(quote_asset, 0.0) or 0.0)
