"""Backtesting engine for the long-only EMA-crossover strategy in strategy.py.

Simulates the strategy against historical OHLCV data and reports the
metrics needed to judge it before ever touching order execution, even
on Testnet: win rate, max drawdown, total return, number of trades.

Assumptions (deliberate simplifications, worth knowing about):
  - Long-only, a single position at a time (no pyramiding, no shorting).
  - Enters and exits-by-signal at the *close* of the candle where the
    EMA crossover happens, not the next candle's open. Real fills will
    differ slightly — one reason testnet paper-trading still matters
    even after a good backtest.
  - Every entry also sets a stop-loss at risk_manager.stop_loss_price(),
    mirroring the real STOP_LOSS_LIMIT order main.py places on Testnet.
    A trade closes on whichever comes first: the candle's *low*
    touching that stop (filled at the stop price — real fills would
    trail it slightly given the STOP_LOSS_LIMIT_SLIPPAGE_PCT buffer) or
    the EMA crossing back (filled at that candle's close). No
    take-profit is simulated, matching main.py --trade today.
  - A flat per-trade fee approximates Binance's spot taker fee so the
    backtest isn't unrealistically optimistic.
  - `use_pattern_filter=True` (or CLI `--pattern-filter`) turns on the
    reversal-pattern confirmation filter from patterns.py (double-top/
    bottom, head-and-shoulders/inverse, triangles): a confirmed
    bearish pattern blocks a new EMA-crossover entry for a while. Off
    by default — it's an opt-in experiment, not a validated edge.
"""
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from config import RISK_PER_TRADE_PCT, STOP_LOSS_PCT, TAKE_PROFIT_PCT
from patterns import PATTERN_VETO_LOOKBACK, bearish_veto_mask, detect_reversal_patterns
from risk_manager import position_size, stop_loss_price
from strategy import FAST_PERIOD, SLOW_PERIOD, add_signals

TAKER_FEE_PCT = 0.1  # Binance default spot taker fee, %


def _simulate(
    df: pd.DataFrame,
    initial_capital: float,
    fast: int = None,
    slow: int = None,
    use_pattern_filter: bool = False,
):
    """Core simulation loop, shared by run_backtest() and export_report().

    `use_pattern_filter`: opt-in reversal-pattern confirmation filter
    (see patterns.py) — a confirmed bearish pattern (double-top,
    head-and-shoulders, descending/symmetric triangle) blocks new
    EMA-crossover entries for PATTERN_VETO_LOOKBACK candles. It only
    gates entries; exits (signal or stop-loss) are unaffected.

    Returns (metrics: dict, data: DataFrame with equity/drawdown columns
    added, trades: list of per-trade dicts).
    """
    kwargs = {}
    if fast is not None:
        kwargs["fast"] = fast
    if slow is not None:
        kwargs["slow"] = slow
    data = add_signals(df, **kwargs)

    warmup = slow if slow is not None else SLOW_PERIOD
    data = data.iloc[warmup:].copy()

    if use_pattern_filter:
        data["pattern_signal"] = detect_reversal_patterns(data)
        entry_blocked = bearish_veto_mask(data["pattern_signal"], PATTERN_VETO_LOOKBACK)
    else:
        data["pattern_signal"] = 0
        entry_blocked = pd.Series(False, index=data.index)

    capital = initial_capital  # total account value (cash + any open position, at cost)
    position = 0  # 0 = flat, 1 = long
    entry_price = 0.0
    entry_time = None
    stop_price = None
    size = 0.0  # base-asset units held while in position
    trades = []
    equity_curve = []
    total_fees_paid = 0.0

    for timestamp, row in data.iterrows():
        price = row["close"]
        low = row["low"]
        signal = row["signal"]

        if position == 1 and stop_price is not None and low <= stop_price:
            # Stop-loss triggers intra-candle: a resting order would
            # have filled near the stop, before we'd even see this
            # candle's close or its EMA-crossover signal.
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
        elif position == 0 and signal == 1 and not entry_blocked.loc[timestamp]:
            candidate_stop = stop_loss_price(price)
            # Same sizing main.py --trade actually places live: risking
            # RISK_PER_TRADE_PCT of capital against this stop distance,
            # not "all-in" on every signal. Deliberately not the whole
            # `capital` -- a backtest that assumes full exposure every
            # trade overstates both the return AND the risk the live
            # bot actually takes. Same zero-size guard as main.py's own
            # `if size > 0:` -- a degenerate size (e.g. capital already
            # wiped out) means no order would actually be placed live.
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

    metrics = compute_metrics(data["close"], data["equity"], data["drawdown_pct"], trades, initial_capital, total_fees_paid)
    return metrics, data, trades


def compute_metrics(
    close: pd.Series,
    equity: pd.Series,
    drawdown_pct: pd.Series,
    trades: list,
    initial_capital: float,
    total_fees_paid: float,
) -> dict:
    """Metrics shared by every backtest loop in this repo, whatever
    decides its entries/exits (EMA crossover here, the Setup Engine in
    setup_engine_backtester.py). `stop_loss_exits` counts by the exact
    exit_reason string "stop_loss"; `signal_exits` is everything else,
    so it stays correct regardless of what a given strategy calls its
    non-stop-loss exit (EMA cross, bias flip, no_trade, ...) — the
    dashboard only ever reads these two keys, never the raw reasons.
    """
    if not len(equity):
        return {
            "initial_capital": initial_capital,
            "final_capital": initial_capital,
            "total_return_pct": 0.0,
            "buy_hold_return_pct": 0.0,
            "num_trades": 0,
            "win_rate_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_trade_return_pct": 0.0,
            "best_trade_pct": 0.0,
            "worst_trade_pct": 0.0,
            "stop_loss_exits": 0,
            "signal_exits": 0,
            "avg_trade_duration_hours": 0.0,
            "total_fees_paid": 0.0,
        }

    wins = [t for t in trades if t["return_pct"] > 0]
    trade_returns = [t["return_pct"] / 100 for t in trades]
    durations_hours = [(t["exit_time"] - t["entry_time"]).total_seconds() / 3600 for t in trades]
    buy_hold_return_pct = float(close.iloc[-1] / close.iloc[0] - 1) * 100
    stop_loss_exits = sum(1 for t in trades if t["exit_reason"] == "stop_loss")

    return {
        "initial_capital": initial_capital,
        "final_capital": float(equity.iloc[-1]),
        "total_return_pct": float(equity.iloc[-1] / initial_capital - 1) * 100,
        "buy_hold_return_pct": buy_hold_return_pct,
        "num_trades": len(trades),
        "win_rate_pct": (len(wins) / len(trades) * 100) if trades else 0.0,
        "max_drawdown_pct": float(drawdown_pct.min()),
        "avg_trade_return_pct": float(np.mean(trade_returns) * 100) if trade_returns else 0.0,
        "best_trade_pct": float(max(t["return_pct"] for t in trades)) if trades else 0.0,
        "worst_trade_pct": float(min(t["return_pct"] for t in trades)) if trades else 0.0,
        "stop_loss_exits": stop_loss_exits,
        "signal_exits": len(trades) - stop_loss_exits,
        "avg_trade_duration_hours": float(np.mean(durations_hours)) if durations_hours else 0.0,
        "total_fees_paid": float(total_fees_paid),
    }


def run_backtest(
    df: pd.DataFrame,
    initial_capital: float = 1000.0,
    fast: int = None,
    slow: int = None,
    use_pattern_filter: bool = False,
) -> dict:
    """Simulate the EMA-crossover strategy over `df` and return metrics.

    `df` must have a 'close' column indexed by time (as returned by
    data_fetcher.fetch_ohlcv / fetch_ohlcv_history).
    """
    metrics, _, _ = _simulate(df, initial_capital, fast, slow, use_pattern_filter)
    return metrics


def export_report(
    df: pd.DataFrame,
    output_path: str,
    initial_capital: float = 1000.0,
    fast: int = None,
    slow: int = None,
    symbol: str = None,
    timeframe: str = None,
    is_demo: bool = False,
    use_pattern_filter: bool = False,
) -> dict:
    """Run the backtest and write a JSON report the React dashboard reads:
    metrics, per-candle OHLC/EMA/signal/equity, and the trade list.
    Returns the same dict that's written to disk.
    """
    metrics, data, trades = _simulate(df, initial_capital, fast, slow, use_pattern_filter)

    candles = [
        {
            "timestamp": ts.isoformat(),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
            "ema_fast": float(row["ema_fast"]),
            "ema_slow": float(row["ema_slow"]),
            "signal": int(row["signal"]),
            "pattern_signal": int(row["pattern_signal"]),
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
        "strategy": {"fast_ema": fast or FAST_PERIOD, "slow_ema": slow or SLOW_PERIOD},
        "risk_management": {
            "risk_per_trade_pct": RISK_PER_TRADE_PCT,
            "stop_loss_pct": STOP_LOSS_PCT,
            "take_profit_pct": TAKE_PROFIT_PCT,
        },
        "backtest_assumptions": {
            "taker_fee_pct": TAKER_FEE_PCT,
            "long_only": True,
            "single_position": True,
            "take_profit_simulated": False,
            "pattern_filter_enabled": use_pattern_filter,
            "pattern_veto_lookback_candles": PATTERN_VETO_LOOKBACK if use_pattern_filter else None,
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

    from config import SYMBOL, TIMEFRAME
    from data_fetcher import fetch_ohlcv_history, get_exchange, get_public_data_exchange

    parser = argparse.ArgumentParser(description="Backtest the EMA-crossover strategy")
    parser.add_argument(
        "--export",
        metavar="PATH",
        help="Also write a JSON report for the dashboard (e.g. dashboard/public/data/backtest.json)",
    )
    parser.add_argument("--days", type=int, default=180, help="Days of history to fetch (default 180)")
    parser.add_argument(
        "--source",
        choices=["testnet", "real"],
        default="testnet",
        help=(
            "Where to pull candles from. 'testnet' (default) only keeps a short "
            "rolling window of history, so a long --days request may come back "
            "short. 'real' reads real Binance's public market data (no API key "
            "needed, no orders placed) for a proper multi-month/year backtest — "
            "order execution (main.py --trade) still only ever uses Testnet."
        ),
    )
    parser.add_argument(
        "--pattern-filter",
        action="store_true",
        help=(
            "Opt-in reversal-pattern confirmation filter (see patterns.py): "
            "blocks a new EMA-crossover entry for a while after a bearish "
            "double-top, head-and-shoulders, or triangle confirms. Off by default."
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

    if args.export:
        report = export_report(
            history,
            args.export,
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            use_pattern_filter=args.pattern_filter,
        )
        metrics = report["metrics"]
        print(f"Report written to {args.export}")
    else:
        metrics = run_backtest(history, use_pattern_filter=args.pattern_filter)

    for key, value in metrics.items():
        print(f"{key}: {value:.2f}" if isinstance(value, float) else f"{key}: {value}")
