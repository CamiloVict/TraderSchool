"""CLI to backtest the same EMA 20/50 + trend-strength filter +
pyramiding strategy already live for PAXG (see backtester.py, strategy.py,
config.py) against real XAU/USD history from OANDA -- see README's
Forex section for why XAU/USD specifically (same underlying asset as
PAXG, different market/venue) and why this reuses backtester.py's own
simulation instead of writing a parallel one.

backtester.run_backtest()/export_report()/split_into_segments() are
pure functions of an OHLCV DataFrame -- they have no idea whether the
candles came from Binance or OANDA, so there is nothing to duplicate
here beyond fetching the data and printing/exporting the same shape of
report backtester.py's own CLI already does. Every CLI flag below maps
1:1 onto backtester.py's own, for the same reason: A vs. B comparisons
between PAXG and XAU/USD should never be confused with "the two CLIs
work differently."

**Not verified against a real OANDA account yet** -- see
forex_data_fetcher.py's own docstring. Don't trust a result from this
script until fetch_candles_history() has been confirmed against a real
practice account.

**Known inaccuracy, flagged rather than hidden**: this fetches OANDA's
*midpoint* price ("M") and reuses config.TAKER_FEE_PCT (a flat %, the
right model for Binance's taker fee) as the only trading-cost
approximation -- OANDA's real cost is the bid/ask spread, not a flat
percentage commission, and trading at the midpoint silently assumes
away that spread entirely. For XAU/USD the spread is typically a few
dollars against a price in the thousands -- not necessarily huge, but
not zero either, and this backtest currently reports as if it were.
Treat every number out of this script as an optimistic upper bound
until spread-aware costs are added (see this module's own TODO in the
README's Forex section) -- a strategy that only wins by a hair once
midpoint-priced shouldn't be trusted here.
"""
import pandas as pd

from backtester import export_report, run_backtest, split_into_segments
from forex_config import FOREX_SYMBOL, FOREX_TIMEFRAME
from forex_data_fetcher import fetch_candles_history
from strategy import SLOW_PERIOD

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Backtest the same EMA+trend-strength-filter+pyramiding strategy already "
            "live for PAXG against XAU/USD real history from OANDA."
        )
    )
    parser.add_argument("--instrument", default=FOREX_SYMBOL, help=f"OANDA instrument (default {FOREX_SYMBOL})")
    parser.add_argument(
        "--granularity", default=FOREX_TIMEFRAME, help=f"OANDA granularity code (default {FOREX_TIMEFRAME})"
    )
    parser.add_argument("--days", type=int, default=365, help="Days of history to fetch (default 365)")
    parser.add_argument(
        "--export",
        metavar="PATH",
        help="Also write a JSON report for the dashboard (e.g. dashboard/public/data/backtest_forex.json)",
    )
    parser.add_argument(
        "--pattern-filter",
        action="store_true",
        help="Opt-in reversal-pattern confirmation filter -- see backtester.py's own flag of the same name.",
    )
    parser.add_argument(
        "--structural-stop",
        action="store_true",
        help="Opt-in structural stop -- see backtester.py's own flag. Probado y descartado on PAXG (see README); untested on XAU/USD.",
    )
    parser.add_argument(
        "--take-profit",
        action="store_true",
        help="Opt-in flat take-profit -- see backtester.py's own flag of the same name.",
    )
    parser.add_argument(
        "--trend-strength-filter",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Trend-strength confirmation filter, on by default (matches the live PAXG config). --no-trend-strength-filter for the raw EMA crossover.",
    )
    parser.add_argument("--min-trend-strength", type=float, default=None, metavar="ATR_MULTIPLE")
    parser.add_argument(
        "--pyramiding",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Add-on tranches on an already-open long, on by default (matches the live PAXG config). --no-pyramiding to disable.",
    )
    parser.add_argument("--max-pyramid-entries", type=int, default=None, metavar="N")
    parser.add_argument("--pyramid-trigger-atr-multiple", type=float, default=None, metavar="ATR_MULTIPLE")
    parser.add_argument("--pyramid-risk-pct", type=float, default=None, metavar="PCT")
    parser.add_argument(
        "--walk-forward",
        type=int,
        metavar="N",
        default=None,
        help="Split --days into N contiguous segments and run the same config on each -- see backtester.py's own flag.",
    )
    args = parser.parse_args()

    since = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=args.days)
    print(f"Fetching {args.instrument} {args.granularity} history from OANDA (last ~{args.days} days) ...")
    history = fetch_candles_history(args.instrument, since=since, granularity=args.granularity)
    print(f"Got {len(history)} candles: {history.index.min()} -> {history.index.max()}\n")

    run_kwargs = dict(
        use_pattern_filter=args.pattern_filter,
        use_structural_stop=args.structural_stop,
        use_take_profit=args.take_profit,
        use_trend_strength_filter=args.trend_strength_filter,
        min_trend_strength=args.min_trend_strength,
        use_pyramiding=args.pyramiding,
        max_pyramid_entries=args.max_pyramid_entries,
        pyramid_trigger_atr_multiple=args.pyramid_trigger_atr_multiple,
        pyramid_risk_pct=args.pyramid_risk_pct,
    )

    if args.walk_forward:
        segments = split_into_segments(history, args.walk_forward, min_candles=SLOW_PERIOD * 2)
        print(f"Walk-forward: {args.walk_forward} segments, identical config run on each --\n")
        segment_returns = []
        comparison_keys = (
            "total_return_pct",
            "buy_hold_return_pct",
            "num_trades",
            "win_rate_pct",
            "max_drawdown_pct",
            "sharpe_ratio",
            "profit_factor",
        )
        for i, segment in enumerate(segments, start=1):
            segment_metrics = run_backtest(segment, **run_kwargs)
            segment_returns.append(segment_metrics["total_return_pct"])
            print(
                f"--- Segment {i}/{args.walk_forward}: {segment.index.min()} -> "
                f"{segment.index.max()} ({len(segment)} candles) ---"
            )
            for key in comparison_keys:
                value = segment_metrics[key]
                print(f"  {key}: {value:.2f}" if isinstance(value, float) else f"  {key}: {value}")
            print()

        profitable = sum(1 for r in segment_returns if r > 0)
        print(
            f"Summary: {profitable}/{len(segment_returns)} segments profitable, "
            f"total_return_pct range [{min(segment_returns):.2f}, {max(segment_returns):.2f}]."
        )
    elif args.export:
        report = export_report(
            history, args.export, symbol=args.instrument, timeframe=args.granularity, **run_kwargs
        )
        metrics = report["metrics"]
        print(f"Report written to {args.export}")
        for key, value in metrics.items():
            print(f"{key}: {value:.2f}" if isinstance(value, float) else f"{key}: {value}")
    else:
        metrics = run_backtest(history, **run_kwargs)
        for key, value in metrics.items():
            print(f"{key}: {value:.2f}" if isinstance(value, float) else f"{key}: {value}")
