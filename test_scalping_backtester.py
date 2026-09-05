"""Tests for scalping_backtester._simulate's entry/exit/stop-loss modeling.

Run with: python -m unittest test_scalping_backtester -v
"""
import unittest

import pandas as pd

from scalping_backtester import _simulate
from test_scalping_strategy import _oscillation, make_df


class SimulateRoundTripTests(unittest.TestCase):
    def _sharp_dip_df(self):
        # A single sharp gap down (not a multi-bar grind) so there is
        # exactly one bar in the discount+oversold zone before recovery
        # starts -- a sustained multi-bar decline re-fires a fresh entry
        # on every bar still inside that zone, each one immediately
        # stopped out as the decline continues, which is real strategy
        # risk but not what this test isolates.
        closes = _oscillation(150.0, 30)
        closes.append(closes[-1] - 15)
        return closes

    def test_signal_exit_when_stop_never_touched(self):
        closes = self._sharp_dip_df()
        for _ in range(25):
            closes.append(closes[-1] + 4)
        df = make_df(closes)

        metrics, _, trades = _simulate(df, initial_capital=1000.0)

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "signal")
        self.assertGreater(trades[0]["return_pct"], 0, "the dip-and-recover scenario should be a winning trade")
        self.assertEqual(metrics["stop_loss_exits"], 0)
        self.assertEqual(metrics["signal_exits"], 1)

    def test_stop_loss_closes_trade_when_range_low_is_broken(self):
        # Same entry as the round-trip scenario (one sharp dip), held
        # for one bar, then a deep wick on a later candle breaks well
        # below the (buffered) structural stop without moving that
        # candle's close -- so the entry signal itself stays satisfied
        # and only the stop should react.
        closes = self._sharp_dip_df()
        closes.append(closes[-1])  # hold one bar, still in position
        crash_index = len(closes)
        closes.append(closes[-1])  # close unchanged; low overridden below
        closes += [closes[-1]] * 10
        df = make_df(closes)
        df.iloc[crash_index, df.columns.get_loc("low")] = df["close"].iloc[crash_index] * 0.5

        metrics, _, trades = _simulate(df, initial_capital=1000.0)

        stop_trades = [t for t in trades if t["exit_reason"] == "stop_loss"]
        self.assertEqual(len(stop_trades), 1)
        self.assertLess(stop_trades[0]["return_pct"], 0)
        self.assertEqual(metrics["stop_loss_exits"], 1)


if __name__ == "__main__":
    unittest.main()
