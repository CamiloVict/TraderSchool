"""Tests for context_engine.liquidity.

The distinction under test is sweep versus breakout: both trade through
a level, and only the close afterwards tells them apart.

    python -m unittest test_context_liquidity -v
"""
import unittest

import pandas as pd

from context_engine.liquidity import (
    analyze_liquidity,
    detect_sweeps,
    find_equal_levels,
    monthly_levels,
    previous_period_levels,
)
from context_engine.schema import LiquidityEventKind, LiquidityLevel, SwingKind
from context_engine.timeframes import build_timeframe_set


def make_frame(rows, start="2024-01-01", freq="1h"):
    """Frame from explicit (open, high, low, close) tuples."""
    index = pd.date_range(start=start, periods=len(rows), freq=freq, tz="UTC")
    return pd.DataFrame(
        [{"open": o, "high": h, "low": low, "close": c, "volume": 10.0} for o, h, low, c in rows],
        index=pd.DatetimeIndex(index, name="timestamp"),
    )


def flat_rows(n, price, spread=1.0):
    return [(price, price + spread, price - spread, price)] * n


class TemporalLevelTests(unittest.TestCase):
    def test_previous_day_and_week_levels_come_from_completed_bars(self):
        hourly = make_frame(flat_rows(24 * 21, 100.0))
        # Give the final (in-progress) day an extreme high; it must not
        # become the PDH, because that day has not finished yet.
        hourly.iloc[-1, hourly.columns.get_loc("high")] = 999.0
        frames = build_timeframe_set(hourly)

        levels = {level.name: level.price for level in previous_period_levels(frames)}

        self.assertIn("PDH", levels)
        self.assertIn("PWL", levels)
        self.assertNotEqual(levels["PDH"], 999.0)

    def test_monthly_levels_need_two_months_of_history(self):
        short = make_frame(flat_rows(10, 100.0), freq="1D")

        self.assertEqual(monthly_levels(short), [])

    def test_monthly_levels_use_the_previous_month(self):
        daily = make_frame(flat_rows(70, 100.0), start="2024-01-01", freq="1D")
        daily.iloc[40, daily.columns.get_loc("high")] = 150.0  # inside february

        levels = {level.name: level.price for level in monthly_levels(daily)}

        self.assertIn("PMH", levels)
        self.assertIn("PML", levels)


class EqualLevelTests(unittest.TestCase):
    def test_repeated_highs_are_reported_as_a_pool(self):
        rows = flat_rows(30, 100.0)
        rows[10] = (100.0, 110.0, 99.0, 100.0)
        rows[20] = (100.0, 110.02, 99.0, 100.0)  # within ATR tolerance
        df = make_frame(rows)

        equal_highs, _ = find_equal_levels(df)

        self.assertTrue(any(abs(price - 110.0) < 0.1 for price in equal_highs))

    def test_short_history_yields_no_pools(self):
        self.assertEqual(find_equal_levels(make_frame(flat_rows(2, 100.0))), ([], []))


class SweepTests(unittest.TestCase):
    def test_pierce_then_close_back_inside_is_a_sweep(self):
        level = LiquidityLevel(name="PDL", price=95.0, kind=SwingKind.LOW)
        rows = flat_rows(20, 100.0)
        rows[10] = (100.0, 100.5, 90.0, 99.0)  # wick under 95, close above it
        df = make_frame(rows)

        events = detect_sweeps(df, [level])

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, LiquidityEventKind.SWEEP_LOW)
        self.assertTrue(events[0].reclaimed)

    def test_pierce_and_hold_beyond_is_an_expansion_not_a_sweep(self):
        level = LiquidityLevel(name="PDL", price=95.0, kind=SwingKind.LOW)
        rows = flat_rows(12, 100.0) + flat_rows(8, 90.0)  # breaks down and stays
        df = make_frame(rows)

        events = detect_sweeps(df, [level])

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, LiquidityEventKind.LIQUIDITY_EXPANSION)
        self.assertFalse(events[0].reclaimed)

    def test_sweep_high_is_detected_above_the_level(self):
        level = LiquidityLevel(name="PDH", price=105.0, kind=SwingKind.HIGH)
        rows = flat_rows(20, 100.0)
        rows[10] = (100.0, 112.0, 99.5, 101.0)
        df = make_frame(rows)

        events = detect_sweeps(df, [level])

        self.assertEqual(events[0].kind, LiquidityEventKind.SWEEP_HIGH)
        self.assertTrue(events[0].reclaimed)

    def test_displacement_is_flagged_on_a_large_body(self):
        level = LiquidityLevel(name="PDL", price=95.0, kind=SwingKind.LOW)
        rows = flat_rows(20, 100.0)
        rows[10] = (91.0, 100.5, 90.0, 100.0)  # big bullish body off the low
        df = make_frame(rows)

        events = detect_sweeps(df, [level])

        self.assertTrue(events[0].displacement)

    def test_untouched_level_produces_no_event(self):
        level = LiquidityLevel(name="PDL", price=50.0, kind=SwingKind.LOW)
        df = make_frame(flat_rows(20, 100.0))

        self.assertEqual(detect_sweeps(df, [level]), [])

    def test_no_verdict_is_emitted_on_the_final_candle(self):
        # The pierce happens on the very last bar, so whether it gets
        # reclaimed is still unknown and nothing may be reported.
        level = LiquidityLevel(name="PDL", price=95.0, kind=SwingKind.LOW)
        rows = flat_rows(19, 100.0) + [(100.0, 100.5, 90.0, 94.0)]
        df = make_frame(rows)

        self.assertEqual(detect_sweeps(df, [level]), [])


class AnalyzeLiquidityTests(unittest.TestCase):
    def test_empty_input_returns_an_empty_state(self):
        state = analyze_liquidity({})

        self.assertEqual(state.levels, [])
        self.assertIsNone(state.recent_event)

    def test_full_analysis_reports_levels_and_marks_swept_ones(self):
        rows = flat_rows(24 * 20, 100.0)
        rows[-5] = (100.0, 100.5, 80.0, 99.0)  # deep wick sweeping lows
        hourly = make_frame(rows)
        frames = build_timeframe_set(hourly)

        state = analyze_liquidity(frames)
        names = {level.name for level in state.levels}

        self.assertIn("PDH", names)
        self.assertIn("PDL", names)
        self.assertTrue(any(level.swept for level in state.levels))
        self.assertIsNotNone(state.recent_event)


if __name__ == "__main__":
    unittest.main()
