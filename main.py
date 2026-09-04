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

Known limitation, flagged deliberately: --trade exits purely on the
next EMA-crossover signal. risk_manager.stop_loss_price() is used to
size the position, but no hard stop-loss/take-profit order is placed
on the exchange — so a sharp move between candle closes isn't capped
until the next hourly check. Fine for Testnet; worth revisiting
(e.g. an actual STOP_LOSS_LIMIT order) before any real capital.
"""
import argparse
import sys

from config import BINANCE_API_KEY, SYMBOL, TIMEFRAME, USE_TESTNET
from data_fetcher import fetch_ohlcv, get_exchange
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
    from executor import get_base_asset_balance, get_quote_asset_balance, place_market_order
    from risk_manager import position_size

    exchange = exchange or get_exchange()
    df = fetch_ohlcv(exchange, symbol=SYMBOL, timeframe=TIMEFRAME, limit=SLOW_PERIOD * 3)
    data = add_signals(df)
    latest = data.iloc[-1]
    price = float(latest["close"])
    signal = int(latest["signal"])

    base_balance = get_base_asset_balance(exchange, SYMBOL)
    # Dust threshold: ignore leftover balances worth less than $10 so
    # rounding from previous fills doesn't look like an open position.
    in_position = base_balance * price > 10

    action = "hold"
    order = None

    if signal == 1 and not in_position:
        quote_balance = get_quote_asset_balance(exchange, SYMBOL)
        size = position_size(quote_balance, price)
        if size > 0:
            order = place_market_order(exchange, SYMBOL, "buy", size)
            action = "buy"
    elif signal == 0 and in_position:
        order = place_market_order(exchange, SYMBOL, "sell", base_balance)
        action = "sell"

    result = {
        "timestamp": str(latest.name),
        "price": price,
        "signal": signal,
        "in_position_before": in_position,
        "action": action,
        "order_id": order.get("id") if order else None,
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
