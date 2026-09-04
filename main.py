"""Phase 1 entry point: verify Binance Testnet connectivity and data flow.

This does NOT run the bot yet — strategy, backtesting, risk management
and order execution are all unimplemented (see the TODOs in their
respective modules). It only proves that:
  1. ccxt can reach Binance Testnet in sandbox mode.
  2. Historical OHLCV candles can be fetched and parsed into a DataFrame.
  3. API key auth works, if keys are set, by fetching the testnet balance.

Run with: python main.py
"""
import sys

from config import BINANCE_API_KEY, SYMBOL, TIMEFRAME, USE_TESTNET
from data_fetcher import fetch_ohlcv, get_exchange


def main() -> None:
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


if __name__ == "__main__":
    main()
