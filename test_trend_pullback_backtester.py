"""Tests for trend_pullback_backtester._simulate -- VERSION 1 of the
"Strategy Engine V2" proposal. Verifies the simulation loop actually
wires trend_pullback_strategy.add_signals's `signal` column into real
entries/exits and prices the stop off risk_manager.structural_stop_price()
-- the strategy's own entry/exit logic is covered directly by
test_trend_pullback_strategy.py.

Run with: python -m unittest test_trend_pullback_backtester -v
"""
import unittest

from test_trend_pullback_strategy import _pullback_then_bos_then_reversal_closes, make_df
from trend_pullback_backtester import _simulate


class SimulateTrendPullbackTests(unittest.TestCase):
    def test_a_full_trade_closes_via_the_structural_stop(self):
        df = make_df(_pullback_then_bos_then_reversal_closes())

        metrics, data, trades = _simulate(df, initial_capital=1000.0)

        self.assertEqual(len(trades), 1)
        trade = trades[0]
        self.assertAlmostEqual(trade["entry_price"], 121.0, places=6)
        self.assertEqual(str(trade["entry_time"]), "2024-01-05 03:00:00+00:00")
        self.assertEqual(trade["exit_reason"], "stop_loss")
        # The stop must actually be *below* entry for a long, and above
        # the last swing low it's supposed to be anchored to (113) minus
        # some ATR buffer, not some unrelated value.
        self.assertLess(trade["stop_loss_price"], trade["entry_price"])
        self.assertEqual(metrics["num_trades"], 1)
        self.assertEqual(metrics["stop_loss_exits"], 1)

    def test_a_flat_market_never_trades(self):
        df = make_df([100.0] * 200)

        metrics, data, trades = _simulate(df, initial_capital=1000.0)

        self.assertEqual(trades, [])
        self.assertTrue((data["equity"] == 1000.0).all())
        self.assertEqual(metrics["total_return_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
