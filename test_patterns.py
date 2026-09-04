"""Tests for patterns.py's double-top/double-bottom detection.

Run with: python -m unittest test_patterns -v
"""
import unittest

import numpy as np
import pandas as pd

from patterns import PATTERN_VETO_LOOKBACK, bearish_veto_mask, detect_double_patterns


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


class DoubleTopBottomTests(unittest.TestCase):
    def test_double_top_confirms_bearish_signal(self):
        df = make_ohlc(double_top_closes())
        signal = detect_double_patterns(df, pivot_window=3)

        self.assertIn(-1, signal.values)
        self.assertNotIn(1, signal.values)
        confirm_pos = int(np.argmax(signal.values == -1))
        # Confirms only after both peaks have formed (~position 30) and
        # price has fallen back through the valley, not before.
        self.assertGreater(confirm_pos, 30)

    def test_double_bottom_confirms_bullish_signal(self):
        df = make_ohlc(double_bottom_closes())
        signal = detect_double_patterns(df, pivot_window=3)

        self.assertIn(1, signal.values)
        self.assertNotIn(-1, signal.values)
        confirm_pos = int(np.argmax(signal.values == 1))
        self.assertGreater(confirm_pos, 30)

    def test_flat_noisy_series_has_no_pattern(self):
        rng = np.random.default_rng(0)
        closes = 100 + rng.normal(0, 0.2, 60)
        df = make_ohlc(closes.tolist())
        signal = detect_double_patterns(df, pivot_window=3)
        self.assertTrue((signal == 0).all())


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
