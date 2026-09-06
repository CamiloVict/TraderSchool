"""Tests for trend_pullback_strategy.add_signals -- VERSION 1 of the
"Strategy Engine V2" proposal (see the module's own docstring).

Run with: python -m unittest test_trend_pullback_strategy -v
"""
import unittest

import pandas as pd

from trend_pullback_strategy import add_signals


def make_df(closes: list, wick_pct: float = 0.001) -> pd.DataFrame:
    """Hourly OHLCV with a small price-proportional wick (not a flat
    one -- see test_trading_cycle.py's own make_candles() docstring for
    why a flat wick would make ATR collapse to a constant decoupled
    from price, which this strategy's stop distance depends on)."""
    start = pd.Timestamp("2024-01-01", tz="UTC")
    rows = []
    index = []
    for i, close in enumerate(closes):
        wick = close * wick_pct
        rows.append({"open": close, "high": close + wick, "low": close - wick, "close": close, "volume": 1.0})
        index.append(start + pd.Timedelta(hours=i))
    return pd.DataFrame(rows, index=pd.DatetimeIndex(index, name="timestamp"))


def _pullback_then_bos_then_reversal_closes() -> list:
    """A deliberate zigzag (not a straight line): two up-legs each
    followed by a pullback, so context_engine.structure has two swing
    highs and two swing lows to label a real HH/HL sequence (a
    monotonic rise never produces enough swings for classify_trend to
    leave Trend.UNDEFINED -- confirmed against real output before this
    fixture was written). Then a long reversal down, long enough to
    confirm a BEARISH_CHOCH once structure catches up.

    Ground truth (verified directly against context_engine.structure.
    find_breaks() on this exact series before writing assertions
    against it):
      - BULLISH_BOS at hour 55+29 (breaking the first swing high, 110)
        -- too early for a signal: structural_trend is still UNDEFINED
        (only one swing high confirmed so far, classify_trend needs
        two of a kind to say anything but UNDEFINED).
      - BULLISH_BOS at hour 55+54 (breaking the second swing high, 120)
        -- this is the one that should fire a signal: by now
        structural_trend is UP (two HH/HL swings labeled) and the
        candle right before it was Phase.PULLBACK.
      - BEARISH_CHOCH at hour 55+75 -- the first real evidence of
        reversal; should drop the signal back to 0.
    """
    warmup = [100.0] * 55
    leg1_up = [100.0 + i for i in range(1, 11)]  # -> 110 (swing high #1)
    leg1_dn = [110.0 - i for i in range(1, 6)]  # -> 105 (swing low #1)
    leg2_up = [105.0 + i for i in range(1, 16)]  # -> 120 (swing high #2, breaks 110)
    leg2_dn = [120.0 - i for i in range(1, 8)]  # -> 113 (swing low #2, higher than 105)
    leg3_up = [113.0 + i for i in range(1, 21)]  # -> 133 (breaks 120 -> the real entry BOS)
    reversal = [133.0 - i * 2 for i in range(1, 41)]  # -> 53 (eventually confirms a BEARISH_CHOCH)
    return warmup + leg1_up + leg1_dn + leg2_up + leg2_dn + leg3_up + reversal


class AddSignalsTests(unittest.TestCase):
    def test_first_bullish_bos_before_trend_confirms_does_not_enter(self):
        df = make_df(_pullback_then_bos_then_reversal_closes())
        data = add_signals(df)

        self.assertEqual(int(data.loc["2024-01-04 03:00:00+00:00", "signal"]), 0)

    def test_second_bullish_bos_after_a_confirmed_pullback_enters(self):
        df = make_df(_pullback_then_bos_then_reversal_closes())
        data = add_signals(df)

        entry_row = data.loc["2024-01-05 03:00:00+00:00"]
        self.assertEqual(entry_row["structural_trend"], "UP")
        self.assertEqual(int(entry_row["signal"]), 1)
        # The candle immediately before must have been the pullback
        # this entry is supposed to be resolving, not a coincidence.
        self.assertEqual(data.loc["2024-01-05 02:00:00+00:00", "phase"], "PULLBACK")

    def test_stays_long_through_a_further_pullback_that_never_becomes_a_choch(self):
        df = make_df(_pullback_then_bos_then_reversal_closes())
        data = add_signals(df)

        # A few hours after entry, still mid-reversal-not-yet-CHOCH --
        # ordinary retracement must not itself close the trade (that
        # would defeat trading pullbacks in the first place).
        self.assertEqual(int(data.loc["2024-01-05 20:00:00+00:00", "signal"]), 1)

    def test_bearish_choch_drops_the_signal(self):
        df = make_df(_pullback_then_bos_then_reversal_closes())
        data = add_signals(df)

        self.assertEqual(int(data.loc["2024-01-06 01:00:00+00:00", "signal"]), 1)
        self.assertEqual(int(data.loc["2024-01-06 02:00:00+00:00", "signal"]), 0)

    def test_a_flat_market_never_enters(self):
        df = make_df([100.0] * 200)

        data = add_signals(df)

        self.assertTrue((data["signal"] == 0).all())


if __name__ == "__main__":
    unittest.main()
