"""Tests for backtester._simulate's stop-loss modeling.

Verifies the backtest now closes a trade the same way main.py --trade
would: whichever comes first, the candle's low touching the stop-loss
price or the EMA crossing back. Run with:

    python -m unittest test_backtester -v
"""
import unittest
from unittest.mock import patch

import pandas as pd

import backtester
from backtester import _simulate
from config import RISK_PER_TRADE_PCT, STOP_LOSS_PCT
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


class PositionSizingTests(unittest.TestCase):
    def test_a_stopped_out_trade_only_risks_risk_per_trade_pct_of_capital(self):
        # Same crash scenario as the stop-loss test above, but this one
        # checks the *size* of the loss on the account, not just that
        # the stop fired. main.py --trade never risks more than
        # RISK_PER_TRADE_PCT of capital on one trade (risk_manager.
        # position_size) -- a backtest that instead puts 100% of
        # capital into every trade would show a loss close to the full
        # STOP_LOSS_PCT, wildly overstating both the return and the
        # risk the live bot actually takes.
        warmup = [100.0] * (SLOW_PERIOD + 5)
        rise = [100.0 + i for i in range(1, 31)]
        closes = warmup + rise
        crash_index = len(closes)
        closes.append(closes[-1])
        closes += [closes[-1]] * 10
        low_overrides = {crash_index: closes[crash_index] * 0.5}
        df = make_df(closes, low_overrides)

        metrics, _, trades = _simulate(df, initial_capital=1000.0)

        self.assertEqual(len(trades), 1)
        loss_pct_of_capital = (1000.0 - metrics["final_capital"]) / 1000.0 * 100
        self.assertLess(loss_pct_of_capital, STOP_LOSS_PCT)
        self.assertAlmostEqual(loss_pct_of_capital, RISK_PER_TRADE_PCT, delta=0.5)


class PatternFilterWiringTests(unittest.TestCase):
    """Verifies _simulate() actually wires the pattern veto into the
    entry check. patterns.py's own detection logic is covered by
    test_patterns.py — here detect_reversal_patterns is stubbed so the
    test is only about whether _simulate() honors it."""

    def test_confirmed_double_top_blocks_every_entry(self):
        warmup = [100.0] * (SLOW_PERIOD + 5)
        rise = [100.0 + i for i in range(1, 31)]  # would trigger an EMA entry
        df = make_df(warmup + rise)

        always_bearish = lambda data, *a, **k: pd.Series(-1, index=data.index)
        with patch("backtester.detect_reversal_patterns", side_effect=always_bearish):
            _, data, trades = backtester._simulate(df, initial_capital=1000.0, use_pattern_filter=True)

        self.assertEqual(trades, [])
        self.assertTrue((data["equity"] == 1000.0).all())

    def test_filter_off_ignores_pattern_detection_entirely(self):
        warmup = [100.0] * (SLOW_PERIOD + 5)
        rise = [100.0 + i for i in range(1, 31)]
        df = make_df(warmup + rise)

        with patch("backtester.detect_reversal_patterns") as mock_detect:
            _, data, _ = backtester._simulate(df, initial_capital=1000.0, use_pattern_filter=False)

        mock_detect.assert_not_called()
        self.assertTrue((data["equity"] != 1000.0).any())  # entry happened normally


if __name__ == "__main__":
    unittest.main()
