"""Backtesting engine for the long-only EMA-crossover strategy in strategy.py.

Simulates the strategy against historical OHLCV data and reports the
metrics needed to judge it before ever touching order execution, even
on Testnet: win rate, max drawdown, total return, number of trades.

Assumptions (deliberate simplifications, worth knowing about):
  - Long-only, a single position at a time (no pyramiding, no shorting).
  - Enters/exits at the *close* of the candle where the EMA crossover
    happens, not the next candle's open. Real fills will differ
    slightly — one reason testnet paper-trading still matters even
    after a good backtest.
  - A flat per-trade fee approximates Binance's spot taker fee so the
    backtest isn't unrealistically optimistic.
"""
import numpy as np
import pandas as pd

from strategy import SLOW_PERIOD, add_signals

TAKER_FEE_PCT = 0.1  # Binance default spot taker fee, %


def run_backtest(
    df: pd.DataFrame,
    initial_capital: float = 1000.0,
    fast: int = None,
    slow: int = None,
) -> dict:
    """Simulate the EMA-crossover strategy over `df` and return metrics.

    `df` must have a 'close' column indexed by time (as returned by
    data_fetcher.fetch_ohlcv / fetch_ohlcv_history).
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
    position = 0  # 0 = flat, 1 = long
    entry_price = 0.0
    trade_returns = []
    equity_curve = []

    for _, row in data.iterrows():
        price = row["close"]
        signal = row["signal"]

        if position == 0 and signal == 1:
            position = 1
            entry_price = price
            capital *= 1 - TAKER_FEE_PCT / 100
        elif position == 1 and signal == 0:
            trade_return = price / entry_price - 1
            capital *= price / entry_price
            capital *= 1 - TAKER_FEE_PCT / 100
            trade_returns.append(trade_return)
            position = 0

        equity_curve.append(capital * (price / entry_price) if position == 1 else capital)

    if not equity_curve:
        return {
            "initial_capital": initial_capital,
            "final_capital": initial_capital,
            "total_return_pct": 0.0,
            "num_trades": 0,
            "win_rate_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_trade_return_pct": 0.0,
        }

    equity_series = pd.Series(equity_curve, index=data.index)
    running_max = equity_series.cummax()
    drawdown_pct = (equity_series - running_max) / running_max * 100

    wins = [r for r in trade_returns if r > 0]

    return {
        "initial_capital": initial_capital,
        "final_capital": float(equity_series.iloc[-1]),
        "total_return_pct": float(equity_series.iloc[-1] / initial_capital - 1) * 100,
        "num_trades": len(trade_returns),
        "win_rate_pct": (len(wins) / len(trade_returns) * 100) if trade_returns else 0.0,
        "max_drawdown_pct": float(drawdown_pct.min()),
        "avg_trade_return_pct": float(np.mean(trade_returns) * 100) if trade_returns else 0.0,
    }


if __name__ == "__main__":
    from config import SYMBOL, TIMEFRAME
    from data_fetcher import fetch_ohlcv_history, get_exchange

    exchange = get_exchange()
    since_ms = exchange.parse8601(
        (pd.Timestamp.utcnow() - pd.Timedelta(days=180)).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    print(f"Fetching {SYMBOL} {TIMEFRAME} history (last ~180 days) ...")
    history = fetch_ohlcv_history(exchange, symbol=SYMBOL, timeframe=TIMEFRAME, since_ms=since_ms)
    print(f"Got {len(history)} candles: {history.index.min()} -> {history.index.max()}\n")

    metrics = run_backtest(history)
    for key, value in metrics.items():
        print(f"{key}: {value:.2f}" if isinstance(value, float) else f"{key}: {value}")
