"""CLI: fetch candles, build the context, print or export it.

    python -m context_engine --source real --days 120
    python -m context_engine --export dashboard/public/data/context.json

Follows the same shape as backtester.py's CLI: `--source real` reads
real Binance public market data (no API key, no orders), `--source
testnet` reads the sandbox, which only retains a short rolling window
of history and is usually too thin for weekly structure.

Fetching is the only impure step and it lives here, not in the engine.
"""
import argparse
import json
import sys

import pandas as pd

from config import SYMBOL, TIMEFRAME
from context_engine.engine import build_context
from context_engine.params import CONTEXT_ENGINE_VERSION
from context_engine.timeframes import build_timeframe_set
from context_engine.validation import DataValidationError


def fetch_history(source: str, days: int, symbol: str, timeframe: str) -> pd.DataFrame:
    from data_fetcher import fetch_ohlcv_history, get_exchange, get_public_data_exchange

    exchange = get_public_data_exchange() if source == "real" else get_exchange()
    since_ms = exchange.parse8601(
        (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    return fetch_ohlcv_history(exchange, symbol=symbol, timeframe=timeframe, since_ms=since_ms)


def summarize(snapshot) -> str:
    """Human-readable digest — the "why" behind the snapshot, which is
    the whole point of an auditable context (master prompt section 32)."""
    data = snapshot.to_dict()
    lines = [
        f"{data['asset']} @ {data['timestamp']}  (context_engine {data['version']})",
        "",
        f"  regime        {data['regime']['primary']} / vol {data['regime']['volatility']} / {data['regime']['phase']}",
        f"  timeframes    {data['multi_timeframe']}  -> {data['alignment']}",
        f"  bias          {data['bias']['direction']} (confidence {data['bias']['confidence']})",
        f"  score         {data['context_score']['total']} -> {data['context_score']['label']}",
        f"  state         {data['market_state']}",
        f"  direction     {data['preferred_direction']}",
        f"  range         {data['range']['name']} {data['range']['zone']} "
        f"({data['range']['position_percent']}%)",
        f"  volatility    ATR {data['volatility']['atr']:.2f} "
        f"({data['volatility']['atr_percent']:.2f}%) {data['volatility']['regime']}",
        f"  session       {data['sessions']['current']}",
    ]

    if data["bias"]["reasons"]:
        lines.append("")
        lines.append("  reasons:")
        lines.extend(f"    - {reason}" for reason in data["bias"]["reasons"])

    if data["avoid"]:
        lines.append("")
        lines.append("  do not trade because:")
        lines.extend(f"    - {reason}" for reason in data["avoid"])

    lines.append("")
    lines.append(f"  invalidation  {data['invalidation']['detail']}")

    issues = data["data_quality"]["issues"]
    if issues:
        lines.append("")
        lines.append("  data quality:")
        lines.extend(f"    - [{i['severity']}] {i['timeframe']} {i['code']}: {i['detail']}" for i in issues)

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Daily Market Context")
    parser.add_argument(
        "--export",
        metavar="PATH",
        help="Write the context JSON here (e.g. dashboard/public/data/context.json)",
    )
    parser.add_argument("--days", type=int, default=120, help="Days of history to fetch (default 120)")
    parser.add_argument(
        "--source",
        choices=["testnet", "real"],
        default="real",
        help=(
            "Where to pull candles from. 'real' (default) reads real Binance public "
            "market data — no API key, no orders. 'testnet' only keeps a short "
            "rolling window, usually too little for weekly structure."
        ),
    )
    parser.add_argument("--symbol", default=SYMBOL, help=f"Market to analyze (default {SYMBOL})")
    parser.add_argument(
        "--timeframe",
        default=TIMEFRAME,
        help=f"Base timeframe to fetch and resample upward from (default {TIMEFRAME})",
    )
    parser.add_argument(
        "--allow-degraded",
        action="store_true",
        help="Build a snapshot even when the candles fail validation (marked in data_quality)",
    )
    args = parser.parse_args()

    print(
        f"Fetching {args.symbol} {args.timeframe} history from {args.source} "
        f"(last ~{args.days} days) ..."
    )
    history = fetch_history(args.source, args.days, args.symbol, args.timeframe)
    if history.empty:
        print("No candles returned; nothing to analyze.")
        sys.exit(1)
    print(f"Got {len(history)} candles: {history.index.min()} -> {history.index.max()}\n")

    frames = build_timeframe_set(history, base_timeframe=args.timeframe)

    try:
        snapshot = build_context(frames, asset=args.symbol, strict=not args.allow_degraded)
    except DataValidationError as exc:
        print(f"Refusing to build a context on invalid data:\n  {exc}")
        print("\nRe-run with --allow-degraded to build one anyway.")
        sys.exit(1)

    print(summarize(snapshot))

    if args.export:
        with open(args.export, "w") as handle:
            json.dump(snapshot.to_dict(), handle, indent=2)
        print(f"\nContext written to {args.export} (context_engine {CONTEXT_ENGINE_VERSION})")


if __name__ == "__main__":
    main()
