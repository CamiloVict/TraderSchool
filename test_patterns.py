"""Tests for patterns.py's reversal-pattern detection: double-top/bottom
and head-and-shoulders/inverse.

Run with: python -m unittest test_patterns -v
"""
import unittest

import numpy as np
import pandas as pd

from patterns import PATTERN_VETO_LOOKBACK, bearish_veto_mask, detect_reversal_patterns


def make_ohlc(closes: list) -> pd.DataFrame:
    """Hourly OHLC DataFrame from close prices, with a tiny fixed wick
    so high/low swings track the close-price shape almost exactly."""
    start = pd.Timestamp("2024-01-01", tz="UTC")
    closes = np.array(closes, dtype=float)
    index = pd.DatetimeIndex([start + pd.Timedelta(hours=i) for i in range(len(closes))], name="timestamp")
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes + 0.1,
            "low": closes - 0.1,
            "close": closes,
            "volume": 1.0,
        },
        index=index,
    )


def double_top_closes():
    up1 = np.linspace(100, 110, 10)
    down1 = np.linspace(110, 95, 10)
    up2 = np.linspace(95, 110, 10)
    down2 = np.linspace(110, 90, 10)  # breaks back below the 95 valley
    return np.concatenate([up1, down1, up2, down2]).tolist()


def double_bottom_closes():
    down1 = np.linspace(100, 90, 10)
    up1 = np.linspace(90, 105, 10)
    down2 = np.linspace(105, 90, 10)
    up2 = np.linspace(90, 110, 10)  # breaks back above the 105 peak
    return np.concatenate([down1, up1, down2, up2]).tolist()


def head_and_shoulders_closes():
    left_shoulder = np.linspace(100, 108, 8)
    trough1 = np.linspace(108, 98, 8)
    head = np.linspace(98, 122, 10)
    trough2 = np.linspace(122, 99, 10)  # ~= trough1
    right_shoulder = np.linspace(99, 109, 8)  # ~= left shoulder
    breakdown = np.linspace(109, 85, 10)  # breaks below the ~98.5 neckline
    return np.concatenate([left_shoulder, trough1, head, trough2, right_shoulder, breakdown]).tolist()


def inverse_head_and_shoulders_closes():
    left_shoulder = np.linspace(100, 92, 8)
    peak1 = np.linspace(92, 102, 8)
    head = np.linspace(102, 78, 10)
    peak2 = np.linspace(78, 101, 10)  # ~= peak1
    right_shoulder = np.linspace(101, 91, 8)  # ~= left shoulder
    breakout = np.linspace(91, 115, 10)  # breaks above the ~101.5 neckline
    return np.concatenate([left_shoulder, peak1, head, peak2, right_shoulder, breakout]).tolist()


class DoubleTopBottomTests(unittest.TestCase):
    def test_double_top_confirms_bearish_signal(self):
        df = make_ohlc(double_top_closes())
        signal = detect_reversal_patterns(df, pivot_window=3)

        self.assertIn(-1, signal.values)
        self.assertNotIn(1, signal.values)
        confirm_pos = int(np.argmax(signal.values == -1))
        # Confirms only after both peaks have formed (~position 30) and
        # price has fallen back through the valley, not before.
        self.assertGreater(confirm_pos, 30)

    def test_double_bottom_confirms_bullish_signal(self):
        df = make_ohlc(double_bottom_closes())
        signal = detect_reversal_patterns(df, pivot_window=3)

        self.assertIn(1, signal.values)
        self.assertNotIn(-1, signal.values)
        confirm_pos = int(np.argmax(signal.values == 1))
        self.assertGreater(confirm_pos, 30)

    def test_flat_noisy_series_has_no_pattern(self):
        rng = np.random.default_rng(0)
        closes = 100 + rng.normal(0, 0.2, 60)
        df = make_ohlc(closes.tolist())
        signal = detect_reversal_patterns(df, pivot_window=3)
        self.assertTrue((signal == 0).all())


class HeadAndShouldersTests(unittest.TestCase):
    def test_head_and_shoulders_confirms_bearish_signal(self):
        df = make_ohlc(head_and_shoulders_closes())
        signal = detect_reversal_patterns(df, pivot_window=3)

        self.assertIn(-1, signal.values)
        confirm_pos = int(np.argmax(signal.values == -1))
        # The right shoulder finishes forming around position 44; the
        # breakdown can't confirm before that.
        self.assertGreater(confirm_pos, 44)

    def test_inverse_head_and_shoulders_confirms_bullish_signal(self):
        df = make_ohlc(inverse_head_and_shoulders_closes())
        signal = detect_reversal_patterns(df, pivot_window=3)

        self.assertIn(1, signal.values)
        confirm_pos = int(np.argmax(signal.values == 1))
        self.assertGreater(confirm_pos, 44)

    def test_confirmation_never_precedes_the_right_shoulders_confirmation_bar(self):
        """The look-ahead check this module exists to enforce: a
        pattern can't confirm using a swing (the right shoulder) before
        that swing was itself knowable, even if price had already
        crossed the neckline by then."""
        df = make_ohlc(head_and_shoulders_closes())
        pivot_window = 3
        signal = detect_reversal_patterns(df, pivot_window=pivot_window)

        confirm_pos = int(np.argmax(signal.values == -1))
        # Right shoulder peaks at position 43 (8+8+10+10+8-1); it can't
        # be confirmed as a swing before pivot_window candles later.
        right_shoulder_pos = 43
        self.assertGreaterEqual(confirm_pos, right_shoulder_pos + pivot_window)


class BearishVetoMaskTests(unittest.TestCase):
    def test_veto_active_within_lookback_and_expires_after(self):
        n = 30
        confirm_at = 10
        pattern_signal = pd.Series(0, index=range(n))
        pattern_signal.iloc[confirm_at] = -1

        veto = bearish_veto_mask(pattern_signal, lookback=PATTERN_VETO_LOOKBACK)

        self.assertFalse(veto.iloc[confirm_at - 1])
        self.assertTrue(veto.iloc[confirm_at])
        self.assertTrue(veto.iloc[confirm_at + PATTERN_VETO_LOOKBACK - 1])
        self.assertFalse(veto.iloc[confirm_at + PATTERN_VETO_LOOKBACK])


if __name__ == "__main__":
    unittest.main()
