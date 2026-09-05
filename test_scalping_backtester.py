"""Tests for scalping_backtester._simulate's entry/exit/stop-loss modeling.

Run with: python -m unittest test_scalping_backtester -v
"""
import unittest

import pandas as pd

from scalping_backtester import _simulate
from scalping_strategy import PREMIUM_MIN, add_signals
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


class TakeProfitWiringTests(unittest.TestCase):
    """Unlike backtester.py's flat-%% --take-profit (tested and dropped
    for a trend-following strategy), this one exits at the premium-zone
    target already computed for the reward:risk entry gate -- a natural
    fit for a mean-reversion strategy's own "buy the discount, sell the
    premium" premise."""

    def _dip_then_sustained_rise(self):
        closes = _sharp_dip_then_bounce()
        for _ in range(25):  # sustained rise back up through the range
            closes.append(closes[-1] + 4)
        return closes

    def test_take_profit_exits_at_the_premium_zone_target(self):
        closes = self._dip_then_sustained_rise()
        df = make_df(closes)
        data = add_signals(df)
        entry_ts = data.index[data["signal"].diff() == 1][0]
        entry_row = data.loc[entry_ts]
        expected_target = entry_row["range_low"] + (PREMIUM_MIN / 100) * (
            entry_row["range_high"] - entry_row["range_low"]
        )

        metrics, _, trades = _simulate(df, initial_capital=1000.0, use_take_profit=True)

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "take_profit")
        self.assertAlmostEqual(trades[0]["exit_price"], expected_target, places=6)

    def test_flag_off_still_exits_on_the_range_signal_as_before(self):
        closes = self._dip_then_sustained_rise()
        df = make_df(closes)

        metrics, _, trades = _simulate(df, initial_capital=1000.0, use_take_profit=False)

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "signal")


if __name__ == "__main__":
    unittest.main()
