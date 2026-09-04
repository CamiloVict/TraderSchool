"""Tests for backtester._simulate's stop-loss modeling.

Verifies the backtest now closes a trade the same way main.py --trade
would: whichever comes first, the candle's low touching the stop-loss
price or the EMA crossing back. Run with:

    python -m unittest test_backtester -v
"""
import unittest

import pandas as pd

from backtester import _simulate
from risk_manager import stop_loss_price
from strategy import SLOW_PERIOD


def make_df(closes: list, low_overrides: dict = None) -> pd.DataFrame:
    """Hourly OHLCV DataFrame from a list of close prices. `close` is
    used for open/high/low too unless overridden per-index in
    `low_overrides` — lets a test punch a deep intra-candle wick
    without moving the EMA (which is computed off `close`)."""
    low_overrides = low_overrides or {}
    start = pd.Timestamp("2024-01-01", tz="UTC")
    rows = []
    index = []
    for i, close in enumerate(closes):
        low = low_overrides.get(i, close)
        rows.append({"open": close, "high": max(close, low), "low": low, "close": close, "volume": 1.0})
        index.append(start + pd.Timedelta(hours=i))
    return pd.DataFrame(rows, index=pd.DatetimeIndex(index, name="timestamp"))


class SimulateStopLossTests(unittest.TestCase):
    def test_stop_loss_closes_trade_even_if_signal_still_bullish(self):
        warmup = [100.0] * (SLOW_PERIOD + 5)
        rise = [100.0 + i for i in range(1, 31)]  # -> EMA fast crosses above EMA slow
        closes = warmup + rise
        crash_index = len(closes)
        closes.append(closes[-1])  # close stays high: signal alone wouldn't exit here
        closes += [closes[-1]] * 10

        # A deep wick on the crash candle, well below any plausible stop.
        low_overrides = {crash_index: closes[crash_index] * 0.5}
        df = make_df(closes, low_overrides)

        metrics, _, trades = _simulate(df, initial_capital=1000.0)

        stop_trades = [t for t in trades if t["exit_reason"] == "stop_loss"]
        self.assertEqual(len(stop_trades), 1)
        expected_stop = stop_loss_price(stop_trades[0]["entry_price"])
        self.assertAlmostEqual(stop_trades[0]["exit_price"], expected_stop, places=6)
        self.assertEqual(metrics["stop_loss_exits"], 1)

    def test_signal_exit_when_stop_never_touched(self):
        warmup = [100.0] * (SLOW_PERIOD + 5)
        rise = [100.0 + i for i in range(1, 31)]
        fall = [rise[-1] - i for i in range(1, 31)]  # -> EMA fast crosses back below
        df = make_df(warmup + rise + fall)

        metrics, _, trades = _simulate(df, initial_capital=1000.0)

        self.assertGreaterEqual(len(trades), 1)
        self.assertTrue(all(t["exit_reason"] == "signal" for t in trades))
        self.assertEqual(metrics["stop_loss_exits"], 0)


if __name__ == "__main__":
    unittest.main()
