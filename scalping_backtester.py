"""Backtesting engine for scalping_strategy.py's range mean-reversion
strategy (BTC scalping bot -- see README's "Automatizarlo" section for
why it runs as its own cron, separate from the PAXG trend bot).

Separate module from backtester.py for the same reason
setup_engine_backtester.py is one too: a different strategy needs a
different simulation loop (here: a structural stop off the range low
instead of a flat %, no EMA columns to report), but the metrics that
judge any of them are the same math regardless of how entries/exits
are decided -- so compute_metrics() is shared, not duplicated.

Assumptions (same posture as backtester.py's own docstring):
  - Long-only, single position at a time, entering/exiting at the
    *close* of the candle where the condition fires.
  - The stop-loss is structural: the rolling range low that justified
    the entry, not a flat STOP_LOSS_PCT. If price trades below that,
    the "we're inside a range" premise the trade was based on is
    already wrong -- same idea as the Setup Engine backtest's
    invalidation-level stop. Falls back to risk_manager.stop_loss_price
    if that level is degenerate (>= entry price).
  - Same flat per-trade fee approximation as backtester.py.
"""
import json
from datetime import datetime, timezone

import pandas as pd

from backtester import TAKER_FEE_PCT, compute_metrics
from risk_manager import stop_loss_price
from scalping_strategy import (
    DISCOUNT_MAX,
    LOOKBACK,
    MIN_RANGE_ATR_MULTIPLE,
    PREMIUM_MIN,
    RSI_OVERSOLD,
    RSI_PERIOD,
    add_signals,
)

# A stop placed exactly at the range low has almost no room: by
# definition, an entry only fires when price is already within
# DISCOUNT_MAX% of that same low, so "at the structure" and "at the
# entry price" are nearly the same level. Pushing the stop this many
# ATRs further below gives normal noise somewhere to land without
# invalidating the trade's premise the instant it's opened.
STOP_BUFFER_ATR_MULTIPLE = 2.0


def _simulate(df: pd.DataFrame, initial_capital: float, stop_buffer_atr_multiple: float = STOP_BUFFER_ATR_MULTIPLE, **strategy_kwargs):
    """Core simulation loop, shared by run_backtest() and export_report().
    `strategy_kwargs` are passed straight through to
    scalping_strategy.add_signals (lookback, rsi_period, discount_max, ...).

    Returns (metrics: dict, data: DataFrame with equity/drawdown columns
    added, trades: list of per-trade dicts) -- same shape as
    backtester._simulate's, so the dashboard can plot either report.
    """
    data = add_signals(df, **strategy_kwargs)

    lookback = strategy_kwargs.get("lookback", LOOKBACK)
    rsi_period = strategy_kwargs.get("rsi_period", RSI_PERIOD)
    warmup = max(lookback, rsi_period)
    data = data.iloc[warmup:].copy()

    capital = initial_capital
    position = 0  # 0 = flat, 1 = long
    entry_price = 0.0
    entry_time = None
    stop_price = None
    trades = []
    equity_curve = []
    total_fees_paid = 0.0

    for timestamp, row in data.iterrows():
        price = row["close"]
        low = row["low"]
        signal = row["signal"]

        if position == 1 and stop_price is not None and low <= stop_price:
            exit_price = stop_price
            capital *= exit_price / entry_price
            fee = capital * TAKER_FEE_PCT / 100
            capital -= fee
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
        elif position == 0 and signal == 1:
            position = 1
            entry_price = price
            entry_time = timestamp
            atr_price = row["atr_pct"] / 100 * entry_price
            structural_stop = row["range_low"] - atr_price * stop_buffer_atr_multiple
            stop_price = (
                float(structural_stop) if structural_stop < entry_price else stop_loss_price(entry_price)
            )
            fee = capital * TAKER_FEE_PCT / 100
            capital -= fee
            total_fees_paid += fee
        elif position == 1 and signal == 0:
            capital *= price / entry_price
            fee = capital * TAKER_FEE_PCT / 100
            capital -= fee
            total_fees_paid += fee
            trades.append(
                {
                    "entry_time": entry_time,
                    "entry_price": float(entry_price),
                    "exit_time": timestamp,
                    "exit_price": float(price),
                    "return_pct": float((price / entry_price - 1) * 100),
                    "exit_reason": "signal",
                    "stop_loss_price": float(stop_price) if stop_price is not None else None,
                }
            )
            position = 0
            stop_price = None

        equity_curve.append(capital * (price / entry_price) if position == 1 else capital)

    data["equity"] = equity_curve
    if len(data):
        running_max = data["equity"].cummax()
        data["drawdown_pct"] = (data["equity"] - running_max) / running_max * 100
    else:
        data["drawdown_pct"] = []

    metrics = compute_metrics(data["close"], data["equity"], data["drawdown_pct"], trades, initial_capital, total_fees_paid)
    return metrics, data, trades


def run_backtest(df: pd.DataFrame, initial_capital: float = 1000.0, **strategy_kwargs) -> dict:
    """Simulate the range mean-reversion strategy over `df` and return
    metrics. `df` must have 'high', 'low', 'close' columns indexed by
    time (as returned by data_fetcher.fetch_ohlcv_history)."""
    metrics, _, _ = _simulate(df, initial_capital, **strategy_kwargs)
    return metrics


def export_report(
    df: pd.DataFrame,
    output_path: str,
    initial_capital: float = 1000.0,
    symbol: str = None,
    timeframe: str = None,
    is_demo: bool = False,
    **strategy_kwargs,
) -> dict:
    """Run the backtest and write a JSON report the React dashboard
    reads -- same metrics/trades shape as backtester.export_report's,
    with range/RSI columns per candle instead of EMA ones."""
    metrics, data, trades = _simulate(df, initial_capital, **strategy_kwargs)

    candles = [
        {
            "timestamp": ts.isoformat(),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]) if "volume" in data.columns else None,
            "rsi": float(row["rsi"]) if pd.notna(row["rsi"]) else None,
            "range_high": float(row["range_high"]) if pd.notna(row["range_high"]) else None,
            "range_low": float(row["range_low"]) if pd.notna(row["range_low"]) else None,
            "range_position_pct": float(row["range_position_pct"]),
            "signal": int(row["signal"]),
            "equity": float(row["equity"]),
            "drawdown_pct": float(row["drawdown_pct"]),
        }
        for ts, row in data.iterrows()
    ]

    report = {
        "symbol": symbol,
        "timeframe": timeframe,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "is_demo": is_demo,
        "engine": "scalping_range_reversion",
        "strategy": {
            "lookback": strategy_kwargs.get("lookback", LOOKBACK),
            "rsi_period": strategy_kwargs.get("rsi_period", RSI_PERIOD),
            "rsi_oversold": strategy_kwargs.get("rsi_oversold", RSI_OVERSOLD),
            "discount_max": strategy_kwargs.get("discount_max", DISCOUNT_MAX),
            "premium_min": strategy_kwargs.get("premium_min", PREMIUM_MIN),
            "min_range_atr_multiple": strategy_kwargs.get("min_range_atr_multiple", MIN_RANGE_ATR_MULTIPLE),
        },
        "backtest_assumptions": {
            "taker_fee_pct": TAKER_FEE_PCT,
            "long_only": True,
            "single_position": True,
            "stop_priced_off": (
                f"rolling range low at entry, minus "
                f"{strategy_kwargs.get('stop_buffer_atr_multiple', STOP_BUFFER_ATR_MULTIPLE)}x ATR "
                "(falls back to flat stop_loss_pct if degenerate)"
            ),
        },
        "metrics": metrics,
        "candles": candles,
        "trades": [
            {
                "entry_time": t["entry_time"].isoformat(),
                "entry_price": t["entry_price"],
                "exit_time": t["exit_time"].isoformat(),
                "exit_price": t["exit_price"],
                "return_pct": t["return_pct"],
                "exit_reason": t["exit_reason"],
                "stop_loss_price": t["stop_loss_price"],
            }
            for t in trades
        ],
    }

    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)

    return report


if __name__ == "__main__":
    import argparse

    from data_fetcher import fetch_ohlcv_history, get_public_data_exchange

    parser = argparse.ArgumentParser(description="Backtest the BTC range mean-reversion scalping strategy")
    parser.add_argument(
        "--export",
        metavar="PATH",
        help="Also write a JSON report for the dashboard (e.g. dashboard/public/data/backtest_btc.json)",
    )
    parser.add_argument("--symbol", default="BTC/USDT", help="Symbol to backtest (default BTC/USDT)")
    parser.add_argument("--timeframe", default="5m", help="Candle timeframe (default 5m)")
    parser.add_argument("--days", type=int, default=30, help="Days of history to fetch (default 30)")
    args = parser.parse_args()

    exchange = get_public_data_exchange()
    since_ms = exchange.parse8601(
        (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    print(f"Fetching {args.symbol} {args.timeframe} history from real Binance (last ~{args.days} days) ...")
    history = fetch_ohlcv_history(exchange, symbol=args.symbol, timeframe=args.timeframe, since_ms=since_ms)
    print(f"Got {len(history)} candles: {history.index.min()} -> {history.index.max()}\n")

    if args.export:
        report = export_report(history, args.export, symbol=args.symbol, timeframe=args.timeframe)
        metrics = report["metrics"]
        print(f"Report written to {args.export}")
    else:
        metrics = run_backtest(history)

    for key, value in metrics.items():
        print(f"{key}: {value:.2f}" if isinstance(value, float) else f"{key}: {value}")
