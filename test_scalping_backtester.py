"""Tests for scalping_backtester._simulate's entry/exit/stop-loss modeling.

Run with: python -m unittest test_scalping_backtester -v
"""
import unittest

import pandas as pd

from scalping_backtester import _simulate
from test_scalping_strategy import _sharp_dip_then_bounce, make_df


class SimulateRoundTripTests(unittest.TestCase):
    def test_signal_exit_when_stop_never_touched(self):
        closes = _sharp_dip_then_bounce()
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
        closes = _sharp_dip_then_bounce()
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


class RewardRiskFilterTests(unittest.TestCase):
    def test_skips_entry_when_reward_risk_ratio_is_not_met(self):
        # Same round-trip scenario that trades fine at the default
        # ratio (see SimulateRoundTripTests) -- demanding a ratio far
        # above what this setup actually offers must veto it entirely.
        closes = _sharp_dip_then_bounce()
        for _ in range(25):
            closes.append(closes[-1] + 4)
        df = make_df(closes)

        metrics, _, trades = _simulate(df, initial_capital=1000.0, min_reward_risk_ratio=10.0)

        self.assertEqual(len(trades), 0)
        self.assertEqual(metrics["num_trades"], 0)

    def test_trades_once_the_ratio_requirement_is_lowered_back(self):
        # Same scenario, proving the veto above is really about the
        # ratio and not some other side effect of passing the kwarg.
        closes = _sharp_dip_then_bounce()
        for _ in range(25):
            closes.append(closes[-1] + 4)
        df = make_df(closes)

        metrics, _, trades = _simulate(df, initial_capital=1000.0, min_reward_risk_ratio=1.5)

        self.assertEqual(len(trades), 1)


if __name__ == "__main__":
    unittest.main()
