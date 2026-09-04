"""Tests for context_engine.structure.

The important ones are about *when* structure becomes knowable: a
swing must not be reported before its confirmation bar, and a wick
through a level must not be mistaken for a break of structure.

    python -m unittest test_context_structure -v
"""
import unittest

import pandas as pd

from context_engine.schema import BreakKind, StructurePoint, SwingKind, Trend
from context_engine.structure import (
    analyze_structure,
    classify_trend,
    find_breaks,
    find_swings,
    label_sequence,
    swings_known_at,
)


def make_frame(highs, lows=None, closes=None, start="2024-01-01"):
    """Frame built directly from highs/lows so a test can place a pivot
    exactly where it wants one. Close defaults to the midpoint."""
    lows = lows if lows is not None else [h - 2 for h in highs]
    closes = closes if closes is not None else [(h + low) / 2 for h, low in zip(highs, lows)]
    index = pd.date_range(start=start, periods=len(highs), freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [10.0] * len(highs),
        },
        index=pd.DatetimeIndex(index, name="timestamp"),
    )


class FindSwingsTests(unittest.TestCase):
    def test_detects_a_swing_high_and_low(self):
        highs = [10, 11, 15, 11, 10, 9, 12, 13]
        lows = [8, 9, 13, 9, 8, 5, 10, 11]
        df = make_frame(highs, lows)

        swings = find_swings(df, left=2, right=2)
        kinds = {s.kind for s in swings}

        self.assertIn(SwingKind.HIGH, kinds)
        self.assertIn(SwingKind.LOW, kinds)
        high = next(s for s in swings if s.kind is SwingKind.HIGH)
        self.assertEqual(high.price, 15)

    def test_swing_is_confirmed_right_candles_after_it_printed(self):
        highs = [10, 11, 15, 11, 10, 9, 8, 7]
        df = make_frame(highs)

        swing = next(s for s in find_swings(df, left=2, right=2) if s.kind is SwingKind.HIGH)

        # Printed at index 2, only knowable at index 4.
        self.assertEqual(swing.timestamp, df.index[2].isoformat())
        self.assertEqual(swing.confirmed_at, df.index[4].isoformat())

    def test_swing_is_hidden_before_its_confirmation_bar(self):
        highs = [10, 11, 15, 11, 10, 9, 8, 7]
        df = make_frame(highs)
        swings = find_swings(df, left=2, right=2)

        # One bar before confirmation the pivot must be invisible, even
        # though the high itself already printed.
        too_early = swings_known_at(swings, df.index[3])
        just_in_time = swings_known_at(swings, df.index[4])

        self.assertEqual(too_early, [])
        self.assertEqual(len(just_in_time), 1)

    def test_last_candles_can_never_hold_a_confirmed_swing(self):
        highs = [10, 11, 12, 13, 14, 20]  # the 20 is the final bar
        df = make_frame(highs)

        swings = find_swings(df, left=2, right=2)

        self.assertTrue(all(s.price != 20 for s in swings))

    def test_returns_nothing_when_history_is_too_short(self):
        self.assertEqual(find_swings(make_frame([10, 11]), left=2, right=2), [])
        self.assertEqual(find_swings(None), [])


class SequenceTests(unittest.TestCase):
    def test_labels_higher_highs_and_higher_lows(self):
        # Two rising peaks separated by two rising troughs.
        highs = [10, 11, 15, 11, 10, 9, 12, 18, 13, 12, 11, 14]
        lows = [8, 9, 13, 9, 6, 7, 10, 16, 11, 9, 9, 12]
        df = make_frame(highs, lows)

        sequence = label_sequence(find_swings(df, left=2, right=2))

        self.assertIn(StructurePoint.HH, sequence)
        self.assertEqual(classify_trend([StructurePoint.HH, StructurePoint.HL]), Trend.UP)

    def test_classify_trend_covers_each_case(self):
        self.assertEqual(classify_trend([StructurePoint.HH, StructurePoint.HL]), Trend.UP)
        self.assertEqual(classify_trend([StructurePoint.LL, StructurePoint.LH]), Trend.DOWN)
        self.assertEqual(classify_trend([StructurePoint.HH, StructurePoint.LL]), Trend.RANGING)
        self.assertEqual(classify_trend([StructurePoint.HH]), Trend.UNDEFINED)


class FindBreaksTests(unittest.TestCase):
    def test_close_beyond_a_swing_high_is_a_bullish_bos(self):
        highs = [10, 11, 15, 11, 10, 10, 10, 20, 21]
        lows = [8, 9, 13, 9, 8, 8, 8, 18, 19]
        closes = [9, 10, 14, 10, 9, 9, 9, 19, 20]
        df = make_frame(highs, lows, closes)

        swings = find_swings(df, left=2, right=2)
        breaks = find_breaks(df, swings)

        self.assertTrue(breaks)
        self.assertEqual(breaks[0].kind, BreakKind.BULLISH_BOS)
        self.assertEqual(breaks[0].level, 15)

    def test_a_wick_through_the_level_is_not_a_break(self):
        # High pierces the prior swing high but the candle closes back
        # underneath it: liquidity taken, structure intact.
        highs = [10, 11, 15, 11, 10, 10, 10, 16, 12]
        lows = [8, 9, 13, 9, 8, 8, 8, 9, 9]
        closes = [9, 10, 14, 10, 9, 9, 9, 10, 10]
        df = make_frame(highs, lows, closes)

        swings = find_swings(df, left=2, right=2)
        breaks = find_breaks(df, swings)

        self.assertEqual(breaks, [])

    def test_break_against_the_previous_break_is_a_choch(self):
        highs = [10, 11, 15, 11, 10, 10, 10, 20, 21, 20, 19, 12, 8, 7]
        lows = [8, 9, 13, 9, 6, 8, 8, 18, 19, 18, 12, 8, 4, 3]
        closes = [9, 10, 14, 10, 7, 9, 9, 19, 20, 19, 13, 9, 5, 4]
        df = make_frame(highs, lows, closes)

        swings = find_swings(df, left=2, right=2)
        kinds = [b.kind for b in find_breaks(df, swings)]

        self.assertIn(BreakKind.BULLISH_BOS, kinds)
        self.assertIn(BreakKind.BEARISH_CHOCH, kinds)
        # The bullish break has to come first for the bearish one to
        # qualify as a change of character rather than a plain BOS.
        self.assertLess(kinds.index(BreakKind.BULLISH_BOS), kinds.index(BreakKind.BEARISH_CHOCH))

    def test_break_respects_the_as_of_cutoff(self):
        highs = [10, 11, 15, 11, 10, 10, 10, 20, 21]
        lows = [8, 9, 13, 9, 8, 8, 8, 18, 19]
        closes = [9, 10, 14, 10, 9, 9, 9, 19, 20]
        df = make_frame(highs, lows, closes)
        swings = find_swings(df, left=2, right=2)

        before = find_breaks(df, swings, as_of=df.index[6])
        after = find_breaks(df, swings, as_of=df.index[8])

        self.assertEqual(before, [])
        self.assertTrue(after)


class AnalyzeStructureTests(unittest.TestCase):
    def test_empty_frame_yields_an_undefined_state(self):
        state = analyze_structure(pd.DataFrame())

        self.assertEqual(state.trend, Trend.UNDEFINED)
        self.assertEqual(state.sequence, [])
        self.assertIsNone(state.last_bos)
        self.assertIsNone(state.last_swing_high)

    def test_uptrend_is_reported_with_its_swing_levels(self):
        highs = []
        lows = []
        base = 100
        # Five rising impulse/pullback legs.
        for leg in range(5):
            highs += [base + leg * 10 + d for d in (0, 4, 8, 4, 0)]
            lows += [base + leg * 10 + d - 3 for d in (0, 4, 8, 4, 0)]
        df = make_frame(highs, lows)

        state = analyze_structure(df)

        self.assertEqual(state.trend, Trend.UP)
        self.assertIsNotNone(state.last_swing_high)
        self.assertIsNotNone(state.last_swing_low)

    def test_state_only_reflects_data_up_to_as_of(self):
        highs = [10, 11, 15, 11, 10, 9, 8, 7, 6, 5]
        df = make_frame(highs)

        early = analyze_structure(df, as_of=df.index[3])
        later = analyze_structure(df, as_of=df.index[9])

        self.assertEqual(early.swings, [])
        self.assertTrue(later.swings)


if __name__ == "__main__":
    unittest.main()
