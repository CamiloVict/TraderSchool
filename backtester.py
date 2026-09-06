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
  - `use_structural_stop=True` (config.USE_STRUCTURAL_STOP) swaps the
    flat STOP_LOSS_PCT stop for risk_manager.structural_stop_price()
    (last confirmed swing low, from context_engine.structure) — the
    same building block the Setup Engine's own stop already uses. Off
    by default for the same reason the pattern filter is.
  - `use_take_profit=True` (CLI `--take-profit`) adds a flat target at
    risk_manager.take_profit_price() (TAKE_PROFIT_PCT), checked against
    the candle's *high*. Off by default, and meant to stay an
    experiment rather than a default: this is a trend-following
    strategy, and its edge usually comes from letting a winning trade
    run to its own signal exit rather than capping it at a fixed
    target.
  - `use_trend_strength_filter=True` (config.USE_TREND_STRENGTH_FILTER,
    or CLI `--trend-strength-filter`) blocks a new EMA-crossover entry
    unless strategy.py's own trend_strength column (EMA separation, in
    ATRs) is at least `min_trend_strength` (default
    config.MIN_TREND_STRENGTH_ATR_MULTIPLE). Targets the same root
    cause as the pattern filter from a different angle: a crossover
    where the EMAs are still right on top of each other is what a
    ranging/choppy market produces, and tends to whipsaw. Off by
    default -- an opt-in experiment, not a validated edge.
  - CLI `--walk-forward N`: runs the same, unchanged config across N
    contiguous historical segments instead of one pass over the whole
    window, and prints a per-segment comparison (see
    split_into_segments()). Answers whether a result holds up outside
    the exact window it was read on -- this repo doesn't auto-fit
    parameters from data, so it isn't walk-forward *optimization*, just
    an out-of-sample sanity check on whatever config was chosen by hand.
"""
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from config import (
    MIN_TREND_STRENGTH_ATR_MULTIPLE,
    RISK_PER_TRADE_PCT,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TAKER_FEE_PCT,
)
from patterns import PATTERN_VETO_LOOKBACK, bearish_veto_mask, detect_reversal_patterns
from risk_manager import position_size, stop_loss_price, structural_stop_price, take_profit_price
from strategy import FAST_PERIOD, SLOW_PERIOD, add_signals


def _simulate(
    df: pd.DataFrame,
    initial_capital: float,
    fast: int = None,
    slow: int = None,
    use_pattern_filter: bool = False,
    use_structural_stop: bool = False,
    use_take_profit: bool = False,
    use_trend_strength_filter: bool = False,
    min_trend_strength: float = None,
):
    """Core simulation loop, shared by run_backtest() and export_report().

    `use_pattern_filter`: opt-in reversal-pattern confirmation filter
    (see patterns.py) — a confirmed bearish pattern (double-top,
    head-and-shoulders, descending/symmetric triangle) blocks new
    EMA-crossover entries for PATTERN_VETO_LOOKBACK candles. It only
    gates entries; exits (signal or stop-loss) are unaffected.

    `use_trend_strength_filter`: opt-in confirmation filter using
    strategy.py's own `trend_strength` column (EMA separation, in
    ATRs) — blocks a new EMA-crossover entry unless it's at least
    `min_trend_strength` (defaults to config.MIN_TREND_STRENGTH_ATR_MULTIPLE
    when None). Only gates entries; exits are unaffected, same as the
    pattern filter above.

    `use_take_profit`: opt-in flat target at risk_manager.
    take_profit_price() (TAKE_PROFIT_PCT), checked against the
    candle's *high*. This is deliberately the cheap experiment to run
    first, before ever building a structural/liquidity target: a
    trend-following strategy's edge usually comes from letting a big
    winner run to its natural signal exit, so a fixed cap easily costs
    more than it protects. Test with this flag before assuming a
    fancier target is worth building.

    `use_structural_stop`: see this module's own docstring. When
    computing it, only `data.loc[:timestamp]` (history up to and
    including the current candle) is ever passed to
    structural_stop_price() -- never the full `data` -- so a swing
    that only exists because of candles still in the bot's future
    can never back-date into today's stop (context_engine.structure's
    own look-ahead discipline only protects `analyze_structure`'s
    swing/break outputs; naively passing it the whole series would
    still leak the *current* price level through `classify_phase`'s
    unguarded `df["close"].iloc[-1]`).

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

    trend_strength_threshold = (
        min_trend_strength if min_trend_strength is not None else MIN_TREND_STRENGTH_ATR_MULTIPLE
    )

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
    target_price = None
    size = 0.0  # base-asset units held while in position
    trades = []
    equity_curve = []
    total_fees_paid = 0.0

    for timestamp, row in data.iterrows():
        price = row["close"]
        low = row["low"]
        high = row["high"]
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
            target_price = None
            size = 0.0
        elif position == 1 and target_price is not None and high >= target_price:
            # Same intra-candle-first priority as the stop-loss check
            # above (checked before this candle's own signal), and
            # ahead of the signal exit below: a resting limit order
            # would have filled once the high touched it, whatever the
            # close or the EMA end up doing this candle.
            exit_price = target_price
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
                    "exit_reason": "take_profit",
                    "stop_loss_price": float(stop_price) if stop_price is not None else None,
                }
            )
            position = 0
            stop_price = None
            target_price = None
            size = 0.0
        elif (
            position == 0
            and signal == 1
            and not entry_blocked.loc[timestamp]
            and (not use_trend_strength_filter or row["trend_strength"] >= trend_strength_threshold)
        ):
            if use_structural_stop:
                candidate_stop = structural_stop_price(data.loc[:timestamp], price)
            else:
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
                target_price = take_profit_price(price) if use_take_profit else None
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
            target_price = None
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


def _infer_periods_per_year(index: pd.DatetimeIndex) -> float:
    """Candle spacing varies by caller (1h for the EMA/Setup Engine
    backtests, 30m for the BTC scalper) -- infer it from the actual
    index instead of hardcoding a timeframe, so Sharpe/Sortino
    annualize correctly whatever candle size produced `equity`."""
    if len(index) < 2:
        return 365 * 24.0
    seconds_per_candle = (index[-1] - index[0]).total_seconds() / (len(index) - 1)
    if seconds_per_candle <= 0:
        return 365 * 24.0
    return (365 * 24 * 3600) / seconds_per_candle


def _sharpe_ratio(returns: pd.Series, periods_per_year: float) -> float:
    """Annualized Sharpe off per-candle equity returns, 0% risk-free
    rate (the standard simplification for a short crypto backtest).
    Includes candles spent flat (0% return) same as the equity
    they're computed from -- honest about the strategy's real
    per-period volatility, at the cost of being noisy for a strategy
    that trades as rarely as this one; treat it as a rough signal."""
    if len(returns) < 2 or returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(periods_per_year))


def _sortino_ratio(returns: pd.Series, periods_per_year: float) -> float:
    """Same as _sharpe_ratio but penalizing only downside deviation
    (semi-deviation against a 0% target) -- a strategy with occasional
    big up-candles and otherwise-flat returns shouldn't be punished
    for volatility that was never a loss."""
    if len(returns) < 2:
        return 0.0
    downside = returns[returns < 0]
    if len(downside) == 0:
        return 0.0  # no losing candle observed -- undefined, not "infinite skill"
    downside_deviation = np.sqrt((downside**2).mean())
    if downside_deviation == 0:
        return 0.0
    return float(returns.mean() / downside_deviation * np.sqrt(periods_per_year))


def _trade_pnl_usd(equity: pd.Series, initial_capital: float, entry_time, exit_time) -> float:
    """Net $ P&L of one trade (all fees included), read off the
    already-fee-adjusted equity curve rather than re-deriving it from
    entry/exit price and size -- avoids duplicating each simulate()
    loop's own fee bookkeeping here. `entry_loc - 1` is the last candle
    before the position opened; falls back to initial_capital when the
    trade opened on the very first tested candle (no earlier candle to
    read)."""
    entry_loc = equity.index.get_loc(entry_time)
    equity_before_entry = float(equity.iloc[entry_loc - 1]) if entry_loc > 0 else initial_capital
    equity_at_exit = float(equity.loc[exit_time])
    return equity_at_exit - equity_before_entry


def compute_metrics(
    close: pd.Series,
    low: pd.Series,
    high: pd.Series,
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

    Also mutates each dict in `trades` in place, adding `mae_pct`/
    `mfe_pct` (see below) -- cheap to compute here since `low`/`high`
    and each trade's entry_time/exit_time are already on hand, and it
    means every backtester gets per-trade excursion data on its trade
    list for free, not just the aggregate.

    `sharpe_ratio`/`sortino_ratio`: annualized off the full per-candle
    equity curve (0% risk-free rate) -- see _sharpe_ratio/_sortino_ratio.
    `profit_factor`: gross $ profit / gross $ loss across closed trades;
    `None` when every closed trade won (nothing to divide by -- not
    the same as "0 risk", so this deliberately isn't infinity or 0).
    `avg_mae_pct`/`avg_mfe_pct`: average Max Adverse/Favorable
    Excursion -- how far a trade dipped against entry (MAE, <=0) and
    how far it ran in its favor (MFE, >=0) at any point before exit,
    whatever it actually exited at. Useful for judging stop/target
    distance against what trades actually do intra-trade, not just
    where they ended up (e.g. this is what would have answered "is a
    4% take-profit even in the right neighborhood" up front).
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
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "profit_factor": 0.0,
            "avg_mae_pct": 0.0,
            "avg_mfe_pct": 0.0,
        }

    wins = [t for t in trades if t["return_pct"] > 0]
    trade_returns = [t["return_pct"] / 100 for t in trades]
    durations_hours = [(t["exit_time"] - t["entry_time"]).total_seconds() / 3600 for t in trades]
    buy_hold_return_pct = float(close.iloc[-1] / close.iloc[0] - 1) * 100
    stop_loss_exits = sum(1 for t in trades if t["exit_reason"] == "stop_loss")

    equity_returns = equity.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    periods_per_year = _infer_periods_per_year(equity.index)

    gross_profit = 0.0
    gross_loss = 0.0
    mae_values = []
    mfe_values = []
    for t in trades:
        pnl_usd = _trade_pnl_usd(equity, initial_capital, t["entry_time"], t["exit_time"])
        if pnl_usd > 0:
            gross_profit += pnl_usd
        else:
            gross_loss += -pnl_usd

        trade_low = low.loc[t["entry_time"]:t["exit_time"]]
        trade_high = high.loc[t["entry_time"]:t["exit_time"]]
        mae_pct = min(0.0, float(trade_low.min() / t["entry_price"] - 1) * 100) if len(trade_low) else 0.0
        mfe_pct = max(0.0, float(trade_high.max() / t["entry_price"] - 1) * 100) if len(trade_high) else 0.0
        t["mae_pct"] = mae_pct
        t["mfe_pct"] = mfe_pct
        mae_values.append(mae_pct)
        mfe_values.append(mfe_pct)

    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = None  # every closed trade won -- nothing to divide by
    else:
        profit_factor = 0.0

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
        "sharpe_ratio": _sharpe_ratio(equity_returns, periods_per_year),
        "sortino_ratio": _sortino_ratio(equity_returns, periods_per_year),
        "profit_factor": profit_factor,
        "avg_mae_pct": float(np.mean(mae_values)) if mae_values else 0.0,
        "avg_mfe_pct": float(np.mean(mfe_values)) if mfe_values else 0.0,
    }


def split_into_segments(df: pd.DataFrame, n_segments: int, min_candles: int) -> list:
    """Split `df` into `n_segments` contiguous, non-overlapping chunks
    by row count (the last segment absorbs the remainder, so it can be
    up to `n_segments - 1` candles bigger than the others).

    Backs the CLI's `--walk-forward` out-of-sample check: this repo's
    strategies don't auto-fit parameters from data (FAST_PERIOD,
    STOP_LOSS_PCT, etc. are all config, chosen by a human), so a
    classic walk-forward *optimization* doesn't apply here. What this
    answers instead is the more basic and, in this session, more
    concrete risk: every parameter change so far (RSI thresholds, ATR
    buffers, TAKE_PROFIT_PCT...) got tuned by rerunning against the
    *same* 30-day real window each time -- which is exactly how you'd
    accidentally fit noise in that one window rather than find a real
    edge. Running the same, unchanged config across several separate
    historical segments checks whether a result holds up outside the
    window it was read on, without needing an optimizer to exist.

    Raises ValueError if a resulting segment would have fewer than
    `min_candles` rows -- too little history split too many ways
    produces segments too short to trust (e.g. shorter than the
    strategy's own EMA warmup).

    One artifact worth knowing about: a segment boundary can land in
    the middle of what would otherwise be one continuous trade. If a
    position is still open on the segment's last candle, that
    segment's `final_capital`/`total_return_pct` mark it to market same
    as a normal single-window backtest would at the data's end -- but
    none of that trade's closed-trade stats (win_rate_pct,
    profit_factor, ...) reflect it yet, since it never actually closed
    within the segment. A segment can therefore show a positive
    total_return_pct with a 0% win rate if its only closed trades lost
    but it happened to be sitting on an open paper gain when the
    segment ended.
    """
    if n_segments < 2:
        raise ValueError("n_segments must be at least 2 -- a single segment isn't a comparison")
    total = len(df)
    segment_size = total // n_segments
    if segment_size < min_candles:
        raise ValueError(
            f"{total} candles split into {n_segments} segments gives ~{segment_size} candles each, "
            f"fewer than the {min_candles} needed for a meaningful backtest here -- fetch more "
            f"history (--days) or ask for fewer segments."
        )
    segments = []
    for i in range(n_segments):
        start = i * segment_size
        end = total if i == n_segments - 1 else (i + 1) * segment_size
        segments.append(df.iloc[start:end])
    return segments


def run_backtest(
    df: pd.DataFrame,
    initial_capital: float = 1000.0,
    fast: int = None,
    slow: int = None,
    use_pattern_filter: bool = False,
    use_structural_stop: bool = False,
    use_take_profit: bool = False,
    use_trend_strength_filter: bool = False,
    min_trend_strength: float = None,
) -> dict:
    """Simulate the EMA-crossover strategy over `df` and return metrics.

    `df` must have a 'close' column indexed by time (as returned by
    data_fetcher.fetch_ohlcv / fetch_ohlcv_history).
    """
    metrics, _, _ = _simulate(
        df,
        initial_capital,
        fast,
        slow,
        use_pattern_filter,
        use_structural_stop,
        use_take_profit,
        use_trend_strength_filter,
        min_trend_strength,
    )
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
    use_structural_stop: bool = False,
    use_take_profit: bool = False,
    use_trend_strength_filter: bool = False,
    min_trend_strength: float = None,
) -> dict:
    """Run the backtest and write a JSON report the React dashboard reads:
    metrics, per-candle OHLC/EMA/signal/equity, and the trade list.
    Returns the same dict that's written to disk.
    """
    metrics, data, trades = _simulate(
        df,
        initial_capital,
        fast,
        slow,
        use_pattern_filter,
        use_structural_stop,
        use_take_profit,
        use_trend_strength_filter,
        min_trend_strength,
    )

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
            "trend_strength": float(row["trend_strength"]),
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
            "take_profit_simulated": use_take_profit,
            "pattern_filter_enabled": use_pattern_filter,
            "pattern_veto_lookback_candles": PATTERN_VETO_LOOKBACK if use_pattern_filter else None,
            "trend_strength_filter_enabled": use_trend_strength_filter,
            "min_trend_strength_atr_multiple": (
                (min_trend_strength if min_trend_strength is not None else MIN_TREND_STRENGTH_ATR_MULTIPLE)
                if use_trend_strength_filter
                else None
            ),
            "stop_priced_off": "structural (last swing low, ATR-buffered)" if use_structural_stop else "flat stop_loss_pct",
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
                "mae_pct": t["mae_pct"],
                "mfe_pct": t["mfe_pct"],
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
    parser.add_argument(
        "--structural-stop",
        action="store_true",
        help=(
            "Opt-in structural stop (config.USE_STRUCTURAL_STOP): the last "
            "confirmed swing low (context_engine.structure), ATR-buffered, "
            "instead of the flat STOP_LOSS_PCT. Off by default."
        ),
    )
    parser.add_argument(
        "--take-profit",
        action="store_true",
        help=(
            "Opt-in flat take-profit at risk_manager.take_profit_price() "
            "(TAKE_PROFIT_PCT), checked against the candle's high. Off by "
            "default -- a trend-following strategy's edge usually comes from "
            "letting a winner run to its own signal exit, so this is meant as "
            "a quick experiment, not an assumed improvement."
        ),
    )
    parser.add_argument(
        "--trend-strength-filter",
        action="store_true",
        help=(
            "Opt-in trend-strength confirmation filter (config."
            "USE_TREND_STRENGTH_FILTER): blocks a new EMA-crossover entry "
            "unless the EMAs are at least --min-trend-strength ATRs apart. "
            "Off by default."
        ),
    )
    parser.add_argument(
        "--min-trend-strength",
        type=float,
        default=None,
        metavar="ATR_MULTIPLE",
        help=(
            "Threshold for --trend-strength-filter, in ATRs of EMA "
            "separation. Defaults to config.MIN_TREND_STRENGTH_ATR_MULTIPLE."
        ),
    )
    parser.add_argument(
        "--walk-forward",
        type=int,
        metavar="N",
        default=None,
        help=(
            "Instead of one backtest over the whole --days window, split it "
            "into N contiguous, non-overlapping segments and run the same "
            "config (unchanged) on each independently. Checks whether a "
            "result holds up out-of-sample instead of only ever being read "
            "against the one window it was tuned on. Prints a per-segment "
            "comparison instead of a single report; ignores --export."
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
            segment_metrics = run_backtest(
                segment,
                use_pattern_filter=args.pattern_filter,
                use_structural_stop=args.structural_stop,
                use_take_profit=args.take_profit,
                use_trend_strength_filter=args.trend_strength_filter,
                min_trend_strength=args.min_trend_strength,
            )
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
            f"total_return_pct range [{min(segment_returns):.2f}, {max(segment_returns):.2f}]. "
            "A result that only shows up in one segment is more likely that "
            "segment's noise than a real edge."
        )
    else:
        if args.export:
            report = export_report(
                history,
                args.export,
                symbol=SYMBOL,
                timeframe=TIMEFRAME,
                use_pattern_filter=args.pattern_filter,
                use_structural_stop=args.structural_stop,
                use_take_profit=args.take_profit,
                use_trend_strength_filter=args.trend_strength_filter,
                min_trend_strength=args.min_trend_strength,
            )
            metrics = report["metrics"]
            print(f"Report written to {args.export}")
        else:
            metrics = run_backtest(
                history,
                use_pattern_filter=args.pattern_filter,
                use_structural_stop=args.structural_stop,
                use_take_profit=args.take_profit,
                use_trend_strength_filter=args.trend_strength_filter,
                min_trend_strength=args.min_trend_strength,
            )

        for key, value in metrics.items():
            print(f"{key}: {value:.2f}" if isinstance(value, float) else f"{key}: {value}")
