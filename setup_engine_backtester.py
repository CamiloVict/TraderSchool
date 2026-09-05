"""Backtest for main.py's Setup Engine trading cycle
(config.USE_SETUP_ENGINE — see context_engine/ and the README section
"Setup Engine y Market State Machine").

Why this is a separate module from backtester.py rather than another
mode of it: the EMA backtest's loop is a vectorized signal column
(strategy.add_signals() computes the whole "be long or not" series up
front); the Setup Engine's decision needs a freshly-built
ContextSnapshot *at every candle*, which is a fundamentally different
shape of loop. Forcing both into one function would make neither
loop's logic easy to follow. compute_metrics() is shared instead of
duplicated — that part genuinely doesn't depend on how a strategy
decides.

The performance problem, and how this loop copes with it:
build_context() re-resamples every timeframe from scratch on every
call, so its cost scales with how much history it's fed — bigger
history in, larger fixed dresampling cost. Feeding it a history window
that grows with every candle of a backtest signature would carve a
year-long backtest into O(n^2) work (empirically, ~50-100 minutes for
one year of 1h candles). This loop pins the window instead: at each
candle it re-slices the *last* `context_window_days` of history ending
there, exactly like a live main.py --trade cycle already does (its
fetch is always a fixed number of days, never everything since
genesis). That turns the cost linear in the number of candles
backtested — still slow (context_engine's own resampling is not
cheap), but predictable, and it never grows unbounded. Expect on the
order of one minute per ~1,000 candles tested; start with `--days`
in the low hundreds, not a full year, until you know how long your
machine actually takes.

One thing this loop does *better* than main.py's live cycle: it
carries `previous_state` forward from its own last iteration's
computed `market_state`, instead of main.py's trick of building
context twice (once as-of the prior candle, to recover a previous
state it has no other memory of). A backtest loop already has that
memory for free.

Exits, besides the stop-loss/bias-flip/no_trade ones already here, also
fire directly on a freshly-confirmed bearish chart pattern (see the
`bearish_pattern` check below) — deliberately *not* gated on bias
agreeing first, unlike CHART_PATTERN_REVERSAL's entry rule. Closing a
position early on weaker evidence is a different risk trade-off than
opening one on it.
"""
import json
from datetime import datetime, timezone

import pandas as pd

from backtester import TAKER_FEE_PCT, compute_metrics
from config import CONTEXT_HISTORY_DAYS, RISK_PER_TRADE_PCT, STOP_LOSS_PCT
from context_engine.engine import build_context
from context_engine.schema import Bias, Direction
from context_engine.timeframes import build_timeframe_set
from patterns import PATTERN_VETO_LOOKBACK, bearish_veto_mask, detect_reversal_patterns
from risk_manager import position_size, stop_loss_price

SETUP_ENGINE_VERSION = "0.1.0"


def simulate_setup_engine(
    history: pd.DataFrame,
    initial_capital: float = 1000.0,
    context_window_days: int = CONTEXT_HISTORY_DAYS,
    timeframe: str = "1h",
    asset: str = None,
):
    """Simulate main.py's Setup Engine cycle over `history` (hourly
    OHLCV, DatetimeIndex).

    Returns (metrics: dict, trades: list, snapshots: list of
    per-candle dicts with market_state/bias/setup names — enough detail
    to plot without keeping every full ContextSnapshot in memory).
    """
    window = context_window_days * 24  # hours, matching `timeframe="1h"`
    if len(history) <= window:
        raise ValueError(
            f"history has {len(history)} candles; need more than context_window_days "
            f"({context_window_days} = {window} hourly candles) to test even one decision. "
            "Fetch more history or lower --context-window-days."
        )

    capital = initial_capital  # total account value (cash + any open position, at cost)
    position = 0
    entry_price = 0.0
    entry_time = None
    stop_price = None
    size = 0.0  # base-asset units held while in position
    trades = []
    equity_curve = []
    total_fees_paid = 0.0
    previous_state = None
    snapshots = []
    tested_index = history.index[window:]

    for i in range(window, len(history)):
        base = history.iloc[i - window : i + 1]
        frames = build_timeframe_set(base, base_timeframe=timeframe)
        context = build_context(frames, asset=asset, previous_state=previous_state)
        previous_state = context.market_state

        timestamp = history.index[i]
        price = float(history["close"].iloc[i])
        low = float(history["low"].iloc[i])

        if position == 1 and stop_price is not None and low <= stop_price:
            proceeds = size * stop_price
            fee = proceeds * TAKER_FEE_PCT / 100
            capital = capital - (size * entry_price) + proceeds - fee
            total_fees_paid += fee
            trades.append(
                {
                    "entry_time": entry_time,
                    "entry_price": float(entry_price),
                    "exit_time": timestamp,
                    "exit_price": float(stop_price),
                    "return_pct": float((stop_price / entry_price - 1) * 100),
                    "exit_reason": "stop_loss",
                    "stop_loss_price": float(stop_price),
                }
            )
            position = 0
            stop_price = None
            size = 0.0
        elif position == 0:
            long_setup = next((s for s in context.setups if s.direction == Direction.LONG), None)
            if long_setup is not None and not context.no_trade:
                candidate_stop = long_setup.invalidation.level
                if candidate_stop is None or candidate_stop >= price:
                    candidate_stop = stop_loss_price(price)  # degenerate level: fall back to the flat %
                # Same sizing main.py --trade actually places live: risking
                # RISK_PER_TRADE_PCT of capital against this stop, not the
                # whole account -- see backtester.py's own fix for why
                # "100% of capital every trade" overstates both return and
                # risk. Same zero-size guard as main.py's `if size > 0:`.
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
        elif position == 1:
            bullish = context.bias.direction in (Bias.BULLISH, Bias.STRONG_BULLISH)
            # A freshly-confirmed bearish chart pattern closes the
            # position directly, on top of (not instead of) the bias/
            # no_trade exits below. Unlike CHART_PATTERN_REVERSAL's
            # entry rule, this does *not* require HTF bias to agree
            # first: getting out early is a risk-reducing action, not a
            # risk-taking one, so the bar is deliberately lower than for
            # an entry (same asymmetry the master prompt draws between
            # entering and protecting an existing position). Computed
            # lazily -- only while actually holding something to exit --
            # to avoid doubling patterns.detect_reversal_patterns()'s
            # cost on every candle of an already-slow backtest.
            bearish_pattern = bool(bearish_veto_mask(detect_reversal_patterns(base), PATTERN_VETO_LOOKBACK).iloc[-1])
            if context.no_trade or not bullish or bearish_pattern:
                proceeds = size * price
                fee = proceeds * TAKER_FEE_PCT / 100
                capital = capital - (size * entry_price) + proceeds - fee
                total_fees_paid += fee
                if context.no_trade:
                    exit_reason = "no_trade"
                elif not bullish:
                    exit_reason = "bias_flip"
                else:
                    exit_reason = "bearish_pattern"
                trades.append(
                    {
                        "entry_time": entry_time,
                        "entry_price": float(entry_price),
                        "exit_time": timestamp,
                        "exit_price": float(price),
                        "return_pct": float((price / entry_price - 1) * 100),
                        "exit_reason": exit_reason,
                        "stop_loss_price": float(stop_price) if stop_price is not None else None,
                    }
                )
                position = 0
                stop_price = None
                size = 0.0

        if position == 1:
            equity_curve.append(capital - (size * entry_price) + (size * price))
        else:
            equity_curve.append(capital)
        snapshots.append(
            {
                "timestamp": timestamp.isoformat(),
                "market_state": context.market_state.value,
                "bias": context.bias.direction.value,
                "setups": [s.name.value for s in context.setups],
                "no_trade": context.no_trade,
            }
        )

    close = history["close"].iloc[window:]
    equity = pd.Series(equity_curve, index=tested_index)
    running_max = equity.cummax()
    drawdown_pct = (equity - running_max) / running_max * 100

    # OHLC + equity/drawdown per tested candle, added after the fact
    # rather than inside the loop above: `equity`/`drawdown_pct` are
    # only knowable as full series (drawdown needs the running max over
    # everything seen so far), and this list is already in the same
    # order as `tested_index`. This is what lets the dashboard plot a
    # setup-engine report with the same TradingChart/EquityChart
    # components used for the EMA report, which expect OHLC + equity on
    # every "candle".
    has_volume = "volume" in history.columns
    for offset, snapshot in enumerate(snapshots):
        row = history.iloc[window + offset]
        snapshot["open"] = float(row["open"])
        snapshot["high"] = float(row["high"])
        snapshot["low"] = float(row["low"])
        snapshot["close"] = float(row["close"])
        # None (not 0 or 1.0) when the source history has no volume
        # column at all -- e.g. a cached export that predates volume
        # being retained (see backtester.py's own export_report). A
        # fabricated constant would silently misrepresent real data;
        # None is honest about "we don't know."
        snapshot["volume"] = float(row["volume"]) if has_volume else None
        snapshot["equity"] = float(equity_curve[offset])
        snapshot["drawdown_pct"] = float(drawdown_pct.iloc[offset])

    metrics = compute_metrics(close, equity, drawdown_pct, trades, initial_capital, total_fees_paid)
    return metrics, trades, snapshots


def export_report(
    history: pd.DataFrame,
    output_path: str,
    initial_capital: float = 1000.0,
    context_window_days: int = CONTEXT_HISTORY_DAYS,
    timeframe: str = "1h",
    asset: str = None,
    is_demo: bool = False,
) -> dict:
    """Run the Setup Engine backtest and write a JSON report — same
    metrics/trades/candles shape as backtester.export_report's (so the
    dashboard's TradingChart/EquityChart work unmodified), except each
    "candle" carries market_state/bias/setups/no_trade instead of the
    EMA report's ema_fast/ema_slow/signal columns, since there's no
    equivalent single indicator here to plot."""
    metrics, trades, snapshots = simulate_setup_engine(
        history, initial_capital, context_window_days, timeframe, asset
    )

    report = {
        "symbol": asset,
        "timeframe": timeframe,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "is_demo": is_demo,
        "engine": "setup_engine",
        "setup_engine_version": SETUP_ENGINE_VERSION,
        "context_window_days": context_window_days,
        "risk_management": {
            "risk_per_trade_pct": RISK_PER_TRADE_PCT,
            "stop_loss_pct": STOP_LOSS_PCT,
        },
        "backtest_assumptions": {
            "taker_fee_pct": TAKER_FEE_PCT,
            "long_only": True,
            "single_position": True,
            "stop_priced_off": "setup invalidation level (falls back to flat stop_loss_pct if missing/invalid)",
        },
        "metrics": metrics,
        "candles": snapshots,
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
    import time

    from config import SYMBOL, TIMEFRAME
    from data_fetcher import fetch_ohlcv_history, get_public_data_exchange

    parser = argparse.ArgumentParser(
        description=(
            "Backtest the Setup Engine trading cycle (config.USE_SETUP_ENGINE). "
            "Slow — see this module's docstring before running a long --days."
        )
    )
    parser.add_argument(
        "--export",
        metavar="PATH",
        help="Also write a JSON report (e.g. dashboard/public/data/setup_engine_backtest.json)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=270,
        help=(
            "Days of history to fetch AND test decisions over (default 270). Must "
            "exceed --context-window-days, or there is nothing to test — every candle "
            "up to that point is spent on the first decision's own lookback window."
        ),
    )
    parser.add_argument(
        "--context-window-days",
        type=int,
        default=90,
        help=(
            "Rolling history window fed to build_context() at each candle "
            "(default 90 here, for a fast first run — main.py itself defaults to "
            f"CONTEXT_HISTORY_DAYS={CONTEXT_HISTORY_DAYS} for more faithful weekly structure). "
            "Larger = more faithful weekly structure, linearly slower."
        ),
    )
    args = parser.parse_args()

    if args.days <= args.context_window_days:
        print(
            f"--days ({args.days}) must exceed --context-window-days "
            f"({args.context_window_days}) to test at least one decision."
        )
        raise SystemExit(1)

    exchange = get_public_data_exchange()
    since_ms = exchange.parse8601(
        (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    print(f"Fetching {SYMBOL} {TIMEFRAME} history from real Binance (last ~{args.days} days) ...")
    history = fetch_ohlcv_history(exchange, symbol=SYMBOL, timeframe=TIMEFRAME, since_ms=since_ms)
    tested_candles = max(0, len(history) - args.context_window_days * 24)
    print(
        f"Got {len(history)} candles: {history.index.min()} -> {history.index.max()}\n"
        f"Testing {tested_candles} decisions with a {args.context_window_days}-day rolling "
        "context window. This can take a while — see this module's docstring.\n"
    )

    start = time.time()
    if args.export:
        report = export_report(
            history, args.export, context_window_days=args.context_window_days, asset=SYMBOL, timeframe=TIMEFRAME
        )
        metrics = report["metrics"]
        print(f"Report written to {args.export}")
    else:
        metrics, _, _ = simulate_setup_engine(
            history, context_window_days=args.context_window_days, timeframe=TIMEFRAME, asset=SYMBOL
        )
    elapsed = time.time() - start

    for key, value in metrics.items():
        print(f"{key}: {value:.2f}" if isinstance(value, float) else f"{key}: {value}")
    print(f"\n(backtest took {elapsed:.1f}s)")
