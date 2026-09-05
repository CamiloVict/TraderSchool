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
from risk_manager import stop_loss_price, structural_stop_price, take_profit_price
from strategy import add_signals, SLOW_PERIOD


def make_df(closes: list, low_overrides: dict = None, high_overrides: dict = None) -> pd.DataFrame:
    """Hourly OHLCV DataFrame from a list of close prices. `close` is
    used for open/high/low too unless overridden per-index in
    `low_overrides`/`high_overrides` — lets a test punch a deep
    intra-candle wick (down or up) without moving the EMA (which is
    computed off `close`)."""
    low_overrides = low_overrides or {}
    high_overrides = high_overrides or {}
    start = pd.Timestamp("2024-01-01", tz="UTC")
    rows = []
    index = []
    for i, close in enumerate(closes):
        low = low_overrides.get(i, close)
        high = high_overrides.get(i, close)
        rows.append(
            {
                "open": close,
                "high": max(close, low, high),
                "low": min(close, low, high),
                "close": close,
                "volume": 1.0,
            }
        )
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


class StructuralStopWiringTests(unittest.TestCase):
    def _dip_rise_deep_dip_scenario(self):
        """Warmup, a moderate dip (forms a swing low around 95), a rise
        that triggers an EMA-crossover entry, THEN (after entry) a much
        deeper dip down to ~45 -- a swing low that doesn't exist yet at
        entry time. If structural_stop_price ever saw the full series
        instead of history-up-to-entry, the entry's stop would come out
        near 44 instead of near 94."""
        warmup = [100.0] * (SLOW_PERIOD + 5)
        dip = [100 - i for i in range(1, 6)]
        rise = [95 + i for i in range(1, 31)]
        deep_dip_after = [125 - i * 8 for i in range(1, 11)]
        recover = [45 + i * 3 for i in range(1, 21)]
        fall = [recover[-1] - i for i in range(1, 31)]
        closes = warmup + dip + rise + deep_dip_after + recover + fall

        start = pd.Timestamp("2024-01-01", tz="UTC")
        rows = [{"open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 1.0} for c in closes]
        index = [start + pd.Timedelta(hours=i) for i in range(len(closes))]
        return pd.DataFrame(rows, index=pd.DatetimeIndex(index, name="timestamp"))

    def test_structural_stop_differs_from_the_flat_percentage(self):
        df = self._dip_rise_deep_dip_scenario()

        metrics, _, trades = _simulate(df, initial_capital=1000.0, use_structural_stop=True)

        self.assertGreaterEqual(len(trades), 1)
        first_entry_price = trades[0]["entry_price"]
        self.assertNotAlmostEqual(trades[0]["stop_loss_price"], stop_loss_price(first_entry_price), places=2)

    def test_structural_stop_never_leaks_a_future_swing_low(self):
        df = self._dip_rise_deep_dip_scenario()
        data = add_signals(df)
        entry_ts = data.index[data["signal"].diff() == 1][0]
        entry_price = float(data.loc[entry_ts, "close"])

        # The swing low genuinely differs depending on how much of the
        # series is visible -- proves this scenario actually exercises
        # the look-ahead guard, not just that some number matches.
        correct_stop = structural_stop_price(data.loc[:entry_ts], entry_price)
        leaked_stop = structural_stop_price(data, entry_price)
        self.assertLess(leaked_stop, correct_stop - 10, "the future deep dip should look like a much lower stop")

        _, _, trades = _simulate(df, initial_capital=1000.0, use_structural_stop=True)

        self.assertAlmostEqual(trades[0]["stop_loss_price"], correct_stop, places=6)


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


class TakeProfitWiringTests(unittest.TestCase):
    def _gentle_rise_then_plateau_then_fall(self):
        """A rise gentle enough (1.5% total) to trigger the EMA
        crossover entry without ever naturally reaching the 4%
        take-profit target on its own -- unlike the steep 30% rise
        used elsewhere in this file, which would blow through the
        target itself and confound what's actually being tested here.
        Plateaus (signal stays bullish, no exit) before eventually
        falling far enough to trip the stop-loss as a fallback exit."""
        warmup = [100.0] * (SLOW_PERIOD + 5)
        rise = [100.0 + i * 0.05 for i in range(1, 31)]
        closes = warmup + rise
        spike_index = len(closes)
        plateau_val = closes[-1]
        closes.append(plateau_val)
        closes += [plateau_val] * 10
        fall = [plateau_val - i for i in range(1, 31)]
        closes += fall
        return closes, spike_index

    def test_take_profit_closes_trade_when_the_high_touches_the_target(self):
        closes, spike_index = self._gentle_rise_then_plateau_then_fall()
        entry_price = closes[SLOW_PERIOD + 5]  # first rise candle -> where entry fires
        target = take_profit_price(entry_price)
        # A high wick on the plateau, well above the take-profit target,
        # while the close itself stays flat (the EMA crossover alone
        # would not have exited here).
        high_overrides = {spike_index: target * 1.1}
        df = make_df(closes, high_overrides=high_overrides)

        metrics, _, trades = _simulate(df, initial_capital=1000.0, use_take_profit=True)

        self.assertGreaterEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "take_profit")
        self.assertAlmostEqual(trades[0]["exit_price"], take_profit_price(trades[0]["entry_price"]), places=6)

    def test_flag_off_never_triggers_a_take_profit_exit_even_if_the_high_touches_it(self):
        closes, spike_index = self._gentle_rise_then_plateau_then_fall()
        entry_price = closes[SLOW_PERIOD + 5]
        target = take_profit_price(entry_price)
        high_overrides = {spike_index: target * 1.1}
        df = make_df(closes, high_overrides=high_overrides)

        metrics, _, trades = _simulate(df, initial_capital=1000.0, use_take_profit=False)

        self.assertTrue(all(t["exit_reason"] != "take_profit" for t in trades))


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
