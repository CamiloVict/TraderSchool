"""Entry point.

Two modes:
  1. `python main.py` (default) — Phase 1 connectivity check: proves
     ccxt can reach Binance Testnet, fetches recent OHLCV candles, and
     checks API-key auth if keys are set. Places no orders.
  2. `python main.py --trade` — runs ONE trading cycle: fetches the
     latest candles, computes the EMA-crossover signal, and if it
     changed since the account's current position, places a single
     Testnet market order sized by risk_manager.position_size().

--trade is designed to be invoked once per candle close (e.g. by cron
or a systemd timer, hourly for the 1h timeframe) rather than run as an
infinite loop inside this process. That keeps each run short, easy to
log, and easy to kill/restart without losing track of state — the
"state" is just whatever the Testnet account currently holds, read
fresh from the exchange every time (see executor.get_base_asset_balance).

Every buy is immediately followed by a real STOP_LOSS_LIMIT sell order
on the exchange (see executor.place_stop_loss_order), so a sharp move
between hourly checks is capped without waiting for the next candle.
The EMA-crossover exit still applies on top of that — whichever comes
first closes the position. Known limitation, still flagged
deliberately: no take-profit order is placed (risk_manager.take_profit_price()
is computed but unused) — exits on a favorable move still wait for the
EMA to cross back, not a fixed target.

If config.USE_PATTERN_FILTER is on, a newly-confirmed bearish reversal
pattern — double-top, head-and-shoulders, or triangle (see
patterns.py) — blocks a new EMA-crossover entry. It's purely a veto on
entries, never an extra exit trigger.
"""
import argparse
import sys

from config import BINANCE_API_KEY, SYMBOL, TIMEFRAME, USE_PATTERN_FILTER, USE_TESTNET
from data_fetcher import fetch_ohlcv, get_exchange
from patterns import PATTERN_VETO_LOOKBACK, bearish_veto_mask, detect_reversal_patterns
from strategy import SLOW_PERIOD, add_signals


def check_connection() -> None:
    print(f"Connecting to Binance {'TESTNET' if USE_TESTNET else 'LIVE (!)'} ...")
    exchange = get_exchange()

    try:
        markets = exchange.load_markets()
        print(f"Connected OK. {len(markets)} markets available.")
    except Exception as exc:
        print(f"Failed to connect / load markets: {exc}")
        sys.exit(1)

    print(f"\nFetching last 10 candles for {SYMBOL} ({TIMEFRAME}) ...")
    try:
        candles = fetch_ohlcv(exchange, symbol=SYMBOL, timeframe=TIMEFRAME, limit=10)
        print(candles.to_string())
    except Exception as exc:
        print(f"Failed to fetch OHLCV data: {exc}")
        sys.exit(1)

    if BINANCE_API_KEY:
        print("\nAPI key detected, checking authenticated access (balance) ...")
        try:
            balance = exchange.fetch_balance()
            totals = balance.get("total", {}) or {}
            non_zero = {asset: amount for asset, amount in totals.items() if amount}
            print(f"Auth OK. Non-zero balances: {non_zero or '(none)'}")
        except Exception as exc:
            print(f"Auth check failed (expected if your testnet keys aren't set yet): {exc}")
    else:
        print("\nNo API key set in .env — skipping authenticated balance check.")
        print("(Public market data works fine without keys.)")


def run_trading_cycle(exchange=None) -> dict:
    """Fetch the latest signal and, if it differs from the account's
    current position, place a single Testnet order. Returns a dict
    describing what happened, and also prints it."""
    from executor import (
        cancel_order,
        get_average_fill_price,
        get_base_asset_balance,
        get_last_fill_price,
        get_open_stop_loss_orders,
        get_quote_asset_balance,
        get_total_base_asset_balance,
        place_market_order,
        place_stop_loss_order,
    )
    from risk_manager import position_size, stop_loss_price

    exchange = exchange or get_exchange()
    df = fetch_ohlcv(exchange, symbol=SYMBOL, timeframe=TIMEFRAME, limit=SLOW_PERIOD * 3)
    data = add_signals(df)
    latest = data.iloc[-1]
    price = float(latest["close"])
    signal = int(latest["signal"])

    # Total (free + locked) balance: an open stop-loss order locks the
    # coins it covers out of the *free* balance, so checking only free
    # balance would make an already-protected position look flat.
    total_balance = get_total_base_asset_balance(exchange, SYMBOL)
    # Dust threshold: ignore leftover balances worth less than $10 so
    # rounding from previous fills doesn't look like an open position.
    in_position = total_balance * price > 10

    action = "hold"
    order = None
    stop_order = None

    entry_blocked_by_pattern = False
    if USE_PATTERN_FILTER and signal == 1 and not in_position:
        pattern_signal = detect_reversal_patterns(data)
        entry_blocked_by_pattern = bool(
            bearish_veto_mask(pattern_signal, PATTERN_VETO_LOOKBACK).iloc[-1]
        )

    if signal == 1 and not in_position and not entry_blocked_by_pattern:
        quote_balance = get_quote_asset_balance(exchange, SYMBOL)
        size = position_size(quote_balance, price)
        if size > 0:
            order = place_market_order(exchange, SYMBOL, "buy", size)
            action = "buy"
            entry_price = get_average_fill_price(order) or price
            filled_amount = float(order.get("filled") or size)
            stop_order = place_stop_loss_order(
                exchange, SYMBOL, filled_amount, stop_loss_price(entry_price)
            )
    elif signal == 1 and not in_position and entry_blocked_by_pattern:
        action = "entry_blocked_by_pattern"
    elif signal == 0 and in_position:
        # Cancel the protective stop first so it doesn't compete with
        # this market sell for the same (currently locked) balance.
        for stale_order in get_open_stop_loss_orders(exchange, SYMBOL):
            cancel_order(exchange, SYMBOL, stale_order["id"])
        free_balance = get_base_asset_balance(exchange, SYMBOL)
        order = place_market_order(exchange, SYMBOL, "sell", free_balance)
        action = "sell"
    elif in_position and not get_open_stop_loss_orders(exchange, SYMBOL):
        # Self-heal: holding a position with no protective stop on the
        # exchange (e.g. a previous run crashed between the buy and the
        # stop-loss placement, or it was cancelled outside the bot).
        # Reconstruct the entry price from the last buy fill so the
        # position isn't left unprotected until the next EMA exit.
        entry_price = get_last_fill_price(exchange, SYMBOL, "buy") or price
        stop_order = place_stop_loss_order(
            exchange, SYMBOL, total_balance, stop_loss_price(entry_price)
        )
        action = "stop_loss_replaced"

    result = {
        "timestamp": str(latest.name),
        "price": price,
        "signal": signal,
        "in_position_before": in_position,
        "action": action,
        "order_id": order.get("id") if order else None,
        "stop_order_id": stop_order.get("id") if stop_order else None,
    }
    print(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Testnet crypto trading bot")
    parser.add_argument(
        "--trade",
        action="store_true",
        help="Run one live (Testnet) trading cycle instead of the connectivity check.",
    )
    args = parser.parse_args()

    if args.trade:
        if not USE_TESTNET:
            print("BINANCE_TESTNET is not 'true' — refusing to trade. See executor.py.")
            sys.exit(1)
        run_trading_cycle()
    else:
        check_connection()


if __name__ == "__main__":
    main()
