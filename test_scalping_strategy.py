"""Tests for scalping_strategy.add_signals's range mean-reversion logic.

Run with: python -m unittest test_scalping_strategy -v
"""
import unittest

import pandas as pd

from scalping_strategy import DISCOUNT_MAX, PREMIUM_MIN, RSI_OVERSOLD, add_signals


def make_df(closes: list) -> pd.DataFrame:
    """5-minute OHLCV DataFrame from a list of close prices. High/low
    are a small fixed offset from close -- wide enough to be a valid
    candle, narrow enough to not itself dominate the ATR/range math."""
    start = pd.Timestamp("2024-01-01", tz="UTC")
    rows = []
    index = []
    for i, close in enumerate(closes):
        rows.append({"open": close, "high": close + 0.5, "low": close - 0.5, "close": close, "volume": 1.0})
        index.append(start + pd.Timedelta(minutes=5 * i))
    return pd.DataFrame(rows, index=pd.DatetimeIndex(index, name="timestamp"))


def _oscillation(base: float, bars: int) -> list:
    """A mild up/down chop, just to give ATR a real (non-zero) value
    before the scenario-specific move starts."""
    return [base + (2 if i % 2 == 0 else -2) for i in range(bars)]


class RoundTripTests(unittest.TestCase):
    def test_enters_at_range_bottom_when_oversold_and_holds_until_premium_zone(self):
        closes = _oscillation(150.0, 25)
        for _ in range(15):  # sustained decline -> wide range + oversold RSI
            closes.append(closes[-1] - 4)
        for _ in range(25):  # sustained rise back up through the range
            closes.append(closes[-1] + 5)
        df = make_df(closes)

        out = add_signals(df)

        entries = out.index[out["signal"].diff() == 1]
        exits = out.index[out["signal"].diff() == -1]
        self.assertEqual(len(entries), 1, "expected exactly one buy in this single dip-and-recover scenario")
        self.assertEqual(len(exits), 1, "expected exactly one sell once price reaches the premium zone")

        entry_row = out.loc[entries[0]]
        self.assertLessEqual(entry_row["range_position_pct"], DISCOUNT_MAX)
        self.assertLessEqual(entry_row["rsi"], RSI_OVERSOLD)

        exit_row = out.loc[exits[0]]
        self.assertGreaterEqual(exit_row["range_position_pct"], PREMIUM_MIN)

        # Held long for the entire stretch between entry and exit, not
        # just at the two endpoints -- a wobble in RSI mid-hold must not
        # itself cause a premature exit.
        held = out.loc[entries[0] : exits[0]].iloc[:-1]
        self.assertTrue((held["signal"] == 1).all())

    def test_no_signal_before_enough_history_for_the_rolling_range(self):
        # Fewer bars than the lookback/RSI warm-up: nothing to compute
        # the range or RSI from yet, so it must never fire, whatever
        # the raw prices look like.
        df = make_df(_oscillation(150.0, 10))
        out = add_signals(df)
        self.assertEqual(out["signal"].sum(), 0)


class FilterTests(unittest.TestCase):
    def test_no_entry_when_range_too_tight_relative_to_atr(self):
        # A real dip in price-position terms (down to single-digit %
        # of the local range), but the range itself barely widened
        # relative to ATR -- this is chop, not a range worth scalping.
        closes = _oscillation(150.0, 25)
        for _ in range(15):
            closes.append(closes[-1] - 0.3)
        for _ in range(10):
            closes.append(closes[-1] + 0.3)
        df = make_df(closes)

        out = add_signals(df)

        self.assertEqual(out["signal"].sum(), 0)

    def test_no_entry_when_in_discount_zone_but_rsi_not_oversold(self):
        # A single, moderate gap down lands well inside the discount
        # zone immediately (no sustained decline), so RSI hasn't caught
        # up to "oversold" yet -- the zone alone must not be enough.
        closes = _oscillation(150.0, 30)
        closes.append(closes[-1] - 8)
        closes += [closes[-1]] * 10
        df = make_df(closes)

        out = add_signals(df)

        in_discount = out["range_position_pct"] <= DISCOUNT_MAX
        self.assertTrue(in_discount.any(), "scenario should actually reach the discount zone")
        self.assertTrue((out.loc[in_discount, "rsi"] > RSI_OVERSOLD).all())
        self.assertEqual(out["signal"].sum(), 0)


if __name__ == "__main__":
    unittest.main()
