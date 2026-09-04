"""Tests for context_engine.validation and .timeframes.

Covers the corrupt-data cases from master prompt section 34: no data,
missing candle, duplicate timestamp, inconsistent OHLC and gaps — plus
the UTC normalization and resampling the rest of the engine assumes.

    python -m unittest test_context_validation -v
"""
import unittest

import pandas as pd

from context_engine.schema import Severity
from context_engine.timeframes import (
    build_timeframe_set,
    ensure_utc,
    resample_ohlcv,
    slice_until,
)
from context_engine.validation import (
    DataValidationError,
    assert_valid,
    validate_frame,
    validate_frames,
)


def make_frame(closes, start="2024-01-01", freq="1h", tz="UTC"):
    """Hourly OHLCV frame from close prices, with a small symmetric
    range around each close so the candles are internally consistent."""
    index = pd.date_range(start=start, periods=len(closes), freq=freq, tz=tz)
    rows = [
        {
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 10.0,
        }
        for close in closes
    ]
    return pd.DataFrame(rows, index=pd.DatetimeIndex(index, name="timestamp"))


def codes(issues):
    return {issue.code for issue in issues}


class ValidateFrameTests(unittest.TestCase):
    def test_clean_frame_has_no_issues(self):
        df = make_frame([100.0 + i for i in range(80)])

        issues = validate_frame(df, "1h")

        self.assertEqual(issues, [])

    def test_empty_frame_is_fatal(self):
        issues = validate_frame(pd.DataFrame(), "1h")

        self.assertEqual(codes(issues), {"NO_DATA"})
        self.assertEqual(issues[0].severity, Severity.FATAL)

    def test_duplicate_timestamp_is_fatal(self):
        df = make_frame([100.0 + i for i in range(80)])
        df = pd.concat([df, df.iloc[[10]]]).sort_index()

        issues = validate_frame(df, "1h")

        self.assertIn("DUPLICATE_TIMESTAMPS", codes(issues))
        duplicate = next(i for i in issues if i.code == "DUPLICATE_TIMESTAMPS")
        self.assertEqual(duplicate.severity, Severity.FATAL)

    def test_high_below_low_is_fatal(self):
        df = make_frame([100.0 + i for i in range(80)])
        df.iloc[20, df.columns.get_loc("high")] = df.iloc[20]["low"] - 5

        issues = validate_frame(df, "1h")

        self.assertIn("HIGH_BELOW_LOW", codes(issues))

    def test_high_not_containing_close_is_fatal(self):
        df = make_frame([100.0 + i for i in range(80)])
        # High sits below the close: impossible, and the kind of thing a
        # bad merge of two data sources produces.
        df.iloc[30, df.columns.get_loc("high")] = df.iloc[30]["close"] - 0.5

        issues = validate_frame(df, "1h")

        self.assertIn("OHLC_INCONSISTENT", codes(issues))

    def test_a_single_missing_candle_only_warns(self):
        df = make_frame([100.0 + i for i in range(200)])
        df = df.drop(df.index[50])

        issues = validate_frame(df, "1h")
        missing = next(i for i in issues if i.code == "MISSING_CANDLES")

        self.assertEqual(missing.severity, Severity.WARNING)

    def test_many_missing_candles_are_fatal(self):
        df = make_frame([100.0 + i for i in range(200)])
        df = df.drop(df.index[50:100])

        issues = validate_frame(df, "1h")
        missing = next(i for i in issues if i.code == "MISSING_CANDLES")

        self.assertEqual(missing.severity, Severity.FATAL)

    def test_anomalous_gap_is_flagged(self):
        closes = [100.0] * 80
        closes[40:] = [5000.0] * 40  # a bad print / wrong symbol splice
        df = make_frame(closes)

        issues = validate_frame(df, "1h")

        self.assertIn("ANOMALOUS_GAP", codes(issues))

    def test_negative_volume_is_fatal(self):
        df = make_frame([100.0 + i for i in range(80)])
        df.iloc[5, df.columns.get_loc("volume")] = -1.0

        issues = validate_frame(df, "1h")

        self.assertIn("NEGATIVE_VOLUME", codes(issues))

    def test_short_history_warns_but_stays_usable(self):
        df = make_frame([100.0 + i for i in range(10)])

        issues = validate_frame(df, "1h")

        self.assertIn("SHORT_HISTORY", codes(issues))
        self.assertTrue(all(i.severity is Severity.WARNING for i in issues))

    def test_naive_timestamps_warn(self):
        df = make_frame([100.0 + i for i in range(80)], tz=None)

        issues = validate_frame(df, "1h")

        self.assertIn("NAIVE_TIMESTAMPS", codes(issues))


class ValidateFramesTests(unittest.TestCase):
    def test_valid_when_every_timeframe_is_clean(self):
        frames = {"1h": make_frame([100.0 + i for i in range(80)])}

        quality = validate_frames(frames)

        self.assertTrue(quality.valid)
        self.assertFalse(quality.degraded)

    def test_warning_marks_degraded_but_stays_valid(self):
        frames = {"1h": make_frame([100.0 + i for i in range(10)])}

        quality = validate_frames(frames)

        self.assertTrue(quality.valid)
        self.assertTrue(quality.degraded)
        assert_valid(quality)  # must not raise

    def test_assert_valid_raises_on_fatal_issue(self):
        df = make_frame([100.0 + i for i in range(80)])
        df.iloc[3, df.columns.get_loc("high")] = df.iloc[3]["low"] - 1

        quality = validate_frames({"1h": df})

        self.assertFalse(quality.valid)
        with self.assertRaises(DataValidationError):
            assert_valid(quality)

    def test_no_timeframes_is_invalid(self):
        quality = validate_frames({})

        self.assertFalse(quality.valid)


class TimeframeTests(unittest.TestCase):
    def test_ensure_utc_localizes_naive_timestamps(self):
        df = make_frame([100.0, 101.0, 102.0], tz=None)

        out = ensure_utc(df)

        self.assertIsNotNone(out.index.tz)
        self.assertEqual(str(out.index.tz), "UTC")
        # Localizing must not move the wall-clock time.
        self.assertEqual(out.index[0].hour, df.index[0].hour)

    def test_ensure_utc_sorts_and_deduplicates(self):
        df = make_frame([100.0, 101.0, 102.0])
        shuffled = pd.concat([df.iloc[[2]], df.iloc[[0]], df.iloc[[0]], df.iloc[[1]]])

        out = ensure_utc(shuffled)

        self.assertEqual(len(out), 3)
        self.assertTrue(out.index.is_monotonic_increasing)

    def test_resample_aggregates_hourly_into_four_hour_bars(self):
        df = make_frame([100.0 + i for i in range(8)])

        out = resample_ohlcv(df, "4h")

        self.assertEqual(len(out), 2)
        first = out.iloc[0]
        self.assertEqual(first["open"], 100.0)
        self.assertEqual(first["close"], 103.0)
        self.assertEqual(first["high"], 104.0)  # close 103 + 1
        self.assertEqual(first["low"], 99.0)  # close 100 - 1
        self.assertEqual(first["volume"], 40.0)

    def test_resample_anchors_daily_bars_to_utc_midnight(self):
        df = make_frame([100.0 + i for i in range(48)], start="2024-03-05")

        out = resample_ohlcv(df, "1d")

        self.assertEqual(len(out), 2)
        self.assertTrue(all(ts.hour == 0 for ts in out.index))

    def test_resample_rejects_downsampling(self):
        df = make_frame([100.0 + i for i in range(8)])

        # 15m bars cannot be recovered from hourly candles; the engine
        # must not invent the price path inside the hour.
        frames = build_timeframe_set(df, base_timeframe="1h")

        self.assertNotIn("15m", frames)

    def test_build_timeframe_set_covers_hourly_and_above(self):
        df = make_frame([100.0 + i for i in range(24 * 40)])

        frames = build_timeframe_set(df)

        self.assertEqual(set(frames), {"1h", "4h", "1d", "1w"})
        self.assertGreater(len(frames["1h"]), len(frames["4h"]))
        self.assertGreater(len(frames["4h"]), len(frames["1d"]))

    def test_slice_until_is_inclusive_and_drops_the_future(self):
        df = make_frame([100.0 + i for i in range(10)])
        cutoff = df.index[4]

        out = slice_until(df, cutoff)

        self.assertEqual(len(out), 5)
        self.assertEqual(out.index[-1], cutoff)


if __name__ == "__main__":
    unittest.main()
