"""Backtesting engine for trend_pullback_strategy.py -- VERSION 1 of
the "Strategy Engine V2" proposal (see trend_pullback_strategy.py's own
docstring and README for the full context and why only this one
isolated piece got built).

Separate module from backtester.py for the same reason
scalping_backtester.py/setup_engine_backtester.py are: a different
strategy needs a different simulation loop (here: a structural stop off
the pullback's own swing low, no flat STOP_LOSS_PCT, no EMA-crossover
signal to report), but the metrics that judge any of them are the same
math regardless of how entries/exits are decided -- so compute_metrics()
is shared, not duplicated.

Assumptions (same posture as backtester.py's own docstring):
  - Long-only, single position at a time, entering/exiting at the
    *close* of the candle where the condition fires.
  - The stop-loss is structural (risk_manager.structural_stop_price():
    the last confirmed swing low, ATR-buffered) -- there is no flat-%
    fallback stop for this strategy the way the EMA bot has one, since
    a stop unrelated to the pullback's own structure would contradict
    the entire premise of trading pullbacks. structural_stop_price()
    already falls back to the flat % internally if there's no usable
    swing yet, so a degenerate case still gets *a* stop, just not one
    this strategy would consider meaningful.
  - Same flat per-trade fee approximation as backtester.py.
  - No take-profit, no pyramiding: this is deliberately the smallest
    possible version of the idea, to isolate whether the structural
    pullback entry itself has any edge before adding anything else the
    spec asks for on top of it.
"""
import pandas as pd

from backtester import TAKER_FEE_PCT, compute_metrics, split_into_segments
from risk_manager import position_size, structural_stop_price
from trend_pullback_strategy import SLOW_PERIOD, add_signals


def _simulate(df: pd.DataFrame, initial_capital: float, fast: int = None, slow: int = None):
    """Core simulation loop, shared by run_backtest().

    Returns (metrics: dict, data: DataFrame with equity/drawdown columns
    added, trades: list of per-trade dicts) -- same shape backtester.py
    and scalping_backtester.py both return, so callers/tests can treat
    every backtester in this repo the same way.
    """
    kwargs = {}
    if fast is not None:
        kwargs["fast"] = fast
    if slow is not None:
        kwargs["slow"] = slow
    data = add_signals(df, **kwargs)

    warmup = slow if slow is not None else SLOW_PERIOD
    data = data.iloc[warmup:].copy()

    capital = initial_capital
    position = 0
    entry_price = 0.0
    entry_time = None
    stop_price = None
    size = 0.0
    trades = []
    equity_curve = []
    total_fees_paid = 0.0

    for timestamp, row in data.iterrows():
        price = row["close"]
        low = row["low"]
        signal = row["signal"]

        if position == 1 and stop_price is not None and low <= stop_price:
            exit_price = stop_price
            proceeds = size * exit_price
            fee = proceeds * TAKER_FEE_PCT / 100
            capital = capital - (size * entry_price) + proceeds - fee
            total_fees_paid += fee
            trades.append(
                {
                    "entry_time": entry_time,
                    "entry_price": float(entry_price),
                    "exit_time": timestamp,
                    "exit_price": float(exit_price),
                    "return_pct": float((exit_price / entry_price - 1) * 100),
                    "exit_reason": "stop_loss",
                    "stop_loss_price": float(stop_price),
                }
            )
            position = 0
            stop_price = None
            size = 0.0
        elif position == 0 and signal == 1:
            candidate_stop = structural_stop_price(data.loc[:timestamp], price)
            candidate_size = position_size(capital, price, stop_price=candidate_stop)
            if candidate_size > 0:
                position = 1
                entry_price = price
                entry_time = timestamp
                stop_price = candidate_stop
                size = candidate_size
                cost = size * entry_price
                fee = cost * TAKER_FEE_PCT / 100
                capital -= fee
                total_fees_paid += fee
        elif position == 1 and signal == 0:
            proceeds = size * price
            fee = proceeds * TAKER_FEE_PCT / 100
            capital = capital - (size * entry_price) + proceeds - fee
            total_fees_paid += fee
            trades.append(
                {
                    "entry_time": entry_time,
                    "entry_price": float(entry_price),
                    "exit_time": timestamp,
                    "exit_price": float(price),
                    "return_pct": float((price / entry_price - 1) * 100),
                    "exit_reason": "signal",
                    "stop_loss_price": float(stop_price),
                }
            )
            position = 0
            stop_price = None
            size = 0.0

        if position == 1:
            equity_curve.append(capital - (size * entry_price) + (size * price))
        else:
            equity_curve.append(capital)

    data["equity"] = equity_curve
    if len(data):
        running_max = data["equity"].cummax()
        data["drawdown_pct"] = (data["equity"] - running_max) / running_max * 100
    else:
        data["drawdown_pct"] = []

    metrics = compute_metrics(
        data["close"], data["low"], data["high"], data["equity"], data["drawdown_pct"], trades, initial_capital, total_fees_paid
    )
    return metrics, data, trades


def run_backtest(df: pd.DataFrame, initial_capital: float = 1000.0, fast: int = None, slow: int = None) -> dict:
    """Simulate the Trend Pullback strategy over `df` and return metrics.

    `df` must have 'high'/'low'/'close' columns indexed by time (as
    returned by data_fetcher.fetch_ohlcv / fetch_ohlcv_history).
    """
    metrics, _, _ = _simulate(df, initial_capital, fast, slow)
    return metrics


if __name__ == "__main__":
    import argparse

    from config import SYMBOL, TIMEFRAME
    from data_fetcher import fetch_ohlcv_history, get_exchange, get_public_data_exchange

    parser = argparse.ArgumentParser(
        description="Backtest the Trend Pullback strategy (VERSION 1 of the Strategy Engine V2 proposal)"
    )
    parser.add_argument("--days", type=int, default=365, help="Days of history to fetch (default 365)")
    parser.add_argument(
        "--source",
        choices=["testnet", "real"],
        default="testnet",
        help=(
            "Where to pull candles from. 'testnet' (default) only keeps a short "
            "rolling window of history. 'real' reads real Binance's public market "
            "data (no API key needed, no orders placed) for a proper multi-month "
            "backtest -- order execution (main.py --trade) still only ever uses "
            "Testnet, and this strategy is not wired into it regardless."
        ),
    )
    parser.add_argument(
        "--walk-forward",
        type=int,
        metavar="N",
        default=None,
        help=(
            "Split --days into N contiguous, non-overlapping segments and run "
            "the same config on each independently, same as backtester.py's own "
            "--walk-forward -- checks whether a result holds up out-of-sample "
            "instead of only ever being read against one window."
        ),
    )
    args = parser.parse_args()

    exchange = get_public_data_exchange() if args.source == "real" else get_exchange()
    since_ms = exchange.parse8601(
        (pd.Timestamp.utcnow() - pd.Timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    print(f"Fetching {SYMBOL} {TIMEFRAME} history from {args.source} (last ~{args.days} days) ...")
    history = fetch_ohlcv_history(exchange, symbol=SYMBOL, timeframe=TIMEFRAME, since_ms=since_ms)
    print(f"Got {len(history)} candles: {history.index.min()} -> {history.index.max()}\n")

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
            segment_metrics = run_backtest(segment)
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
    else:
        metrics = run_backtest(history)
        for key, value in metrics.items():
            print(f"{key}: {value:.2f}" if isinstance(value, float) else f"{key}: {value}")
