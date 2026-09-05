"""Tests for risk_manager.position_size's stop_price override — the
piece the Setup Engine path in main.py needs to size against a
structural invalidation level instead of the flat STOP_LOSS_PCT — for
structural_stop_price's own swing-based stop derivation, and for
consecutive_losses' trade_journal.py-shaped streak counting.

Run with: python -m unittest test_risk_manager -v
"""
import unittest

import pandas as pd

from config import (
    MAX_DAILY_LOSS_PCT,
    MAX_STOP_DISTANCE_ATR_MULTIPLE,
    MAX_WEEKLY_LOSS_PCT,
    MIN_STOP_DISTANCE_ATR_MULTIPLE,
    TAKER_FEE_PCT,
)
from risk_manager import (
    DailyLossTracker,
    WeeklyLossTracker,
    consecutive_losses,
    position_size,
    stop_loss_price,
    structural_stop_price,
    validate_stop_distance,
)


def make_df(closes: list) -> pd.DataFrame:
    start = pd.Timestamp("2024-01-01", tz="UTC")
    rows = []
    index = []
    for i, close in enumerate(closes):
        rows.append({"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1.0})
        index.append(start + pd.Timedelta(hours=i))
    return pd.DataFrame(rows, index=pd.DatetimeIndex(index, name="timestamp"))


def _expected_size(capital: float, entry_price: float, stop_price: float) -> float:
    """Reference implementation of position_size's own formula, fees
    included, for tests to check against independently of the
    production code path."""
    risk_amount = capital * 0.01
    price_risk_pct = abs(entry_price - stop_price) / entry_price
    round_trip_fee_pct = 2 * TAKER_FEE_PCT / 100
    position_value = risk_amount / (price_risk_pct + round_trip_fee_pct)
    return position_value / entry_price


class PositionSizeStopPriceTests(unittest.TestCase):
    def test_defaults_to_the_flat_stop_loss_pct_when_no_stop_price_given(self):
        size = position_size(1000.0, 100.0)
        implied_stop = stop_loss_price(100.0)
        expected = _expected_size(1000.0, 100.0, implied_stop)

        self.assertAlmostEqual(size, expected, places=6)

    def test_explicit_stop_price_overrides_the_flat_percentage(self):
        # A stop twice as far away as the default STOP_LOSS_PCT sizes a
        # smaller position for the same risk budget -- not exactly
        # half anymore now that a fixed fee term is folded in
        # alongside the (now doubled) price-distance term, so check
        # against the formula directly rather than assuming a ratio.
        far_stop = 100.0 - (100.0 - stop_loss_price(100.0)) * 2
        wide_size = position_size(1000.0, 100.0, stop_price=far_stop)
        expected = _expected_size(1000.0, 100.0, far_stop)

        self.assertAlmostEqual(wide_size, expected, places=6)
        self.assertLess(wide_size, position_size(1000.0, 100.0), "a farther stop must still size smaller")

    def test_a_closer_stop_sizes_a_larger_position_for_the_same_risk(self):
        close_stop = 100.0 - (100.0 - stop_loss_price(100.0)) / 2
        default_size = position_size(1000.0, 100.0)
        tight_size = position_size(1000.0, 100.0, stop_price=close_stop)

        self.assertGreater(tight_size, default_size)

    def test_zero_distance_stop_returns_zero_instead_of_dividing_by_zero(self):
        self.assertEqual(position_size(1000.0, 100.0, stop_price=100.0), 0.0)

    def test_short_side_still_works_with_an_explicit_stop_above_entry(self):
        size = position_size(1000.0, 100.0, side="short", stop_price=105.0)
        self.assertGreater(size, 0.0)


class PositionSizeFeeAwarenessTests(unittest.TestCase):
    """The actual bug this class exists to pin down: before folding
    fees into position_size(), a stopped-out trade's real loss was
    price_risk_pct + round-trip fees, quietly exceeding
    RISK_PER_TRADE_PCT -- the exact number every other risk limit in
    this repo (daily/weekly loss, consecutive losses, portfolio risk)
    assumes is the true worst case per trade."""

    def test_a_stop_out_loses_no_more_than_risk_per_trade_pct_fees_included(self):
        capital = 1000.0
        entry_price = 100.0
        stop_price = stop_loss_price(entry_price)
        size = position_size(capital, entry_price, stop_price=stop_price)

        entry_fee = size * entry_price * (TAKER_FEE_PCT / 100)
        exit_fee = size * stop_price * (TAKER_FEE_PCT / 100)
        price_loss = size * (entry_price - stop_price)
        total_loss_pct_of_capital = (price_loss + entry_fee + exit_fee) / capital * 100

        # Not exact to the last decimal: position_size() approximates
        # both fee legs off entry_price (the only price known ahead of
        # time), while the exit fee here is computed off the slightly
        # lower stop_price -- a deliberate, second-order-tiny
        # simplification (a few hundredths of a basis point on a 1%
        # budget), not the bug this test exists to catch.
        self.assertAlmostEqual(total_loss_pct_of_capital, 1.0, delta=0.01)

    def test_sizes_smaller_than_a_fee_blind_calculation_would(self):
        # The bug this test would have caught: a formula that ignores
        # fees sizes a position whose PRICE loss alone already equals
        # the full risk budget, before a single cent of fee is paid --
        # meaning the real loss on a stop-out silently exceeds
        # RISK_PER_TRADE_PCT by the cost of the round trip.
        capital = 1000.0
        entry_price = 100.0
        stop_price = stop_loss_price(entry_price)
        size = position_size(capital, entry_price, stop_price=stop_price)

        fee_blind_price_risk_pct = abs(entry_price - stop_price) / entry_price
        fee_blind_size = min(capital * 0.01 / fee_blind_price_risk_pct, capital) / entry_price

        self.assertLess(size, fee_blind_size)


class ValidateStopDistanceTests(unittest.TestCase):
    def test_ok_when_the_distance_is_within_the_configured_band(self):
        atr = 1.0
        mid_multiple = (MIN_STOP_DISTANCE_ATR_MULTIPLE + MAX_STOP_DISTANCE_ATR_MULTIPLE) / 2
        stop_price = 100.0 - mid_multiple * atr

        ok, reason = validate_stop_distance(100.0, stop_price, atr)

        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_rejects_a_stop_closer_than_the_minimum_atr_multiple(self):
        atr = 1.0
        too_close = 100.0 - (MIN_STOP_DISTANCE_ATR_MULTIPLE * atr) / 2

        ok, reason = validate_stop_distance(100.0, too_close, atr)

        self.assertFalse(ok)
        self.assertIn("minimum", reason)

    def test_rejects_a_stop_farther_than_the_maximum_atr_multiple(self):
        atr = 1.0
        too_far = 100.0 - (MAX_STOP_DISTANCE_ATR_MULTIPLE * atr) * 2

        ok, reason = validate_stop_distance(100.0, too_far, atr)

        self.assertFalse(ok)
        self.assertIn("maximum", reason)

    def test_works_the_same_regardless_of_which_side_the_stop_sits_on(self):
        # Short-side stops sit above entry -- abs() means the check
        # doesn't care about sign, only magnitude.
        atr = 1.0
        too_close = 100.0 + (MIN_STOP_DISTANCE_ATR_MULTIPLE * atr) / 2

        ok, reason = validate_stop_distance(100.0, too_close, atr)

        self.assertFalse(ok)

    def test_skips_validation_when_atr_is_unavailable(self):
        # Not enough history to compute an ATR yet -- fail open rather
        # than block a trade over missing diagnostic data.
        ok, reason = validate_stop_distance(100.0, 50.0, atr=None)

        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_skips_validation_when_atr_is_zero_or_negative(self):
        ok, reason = validate_stop_distance(100.0, 50.0, atr=0.0)

        self.assertTrue(ok)


class StructuralStopPriceTests(unittest.TestCase):
    def test_long_stop_sits_below_the_last_confirmed_swing_low(self):
        # A clear dip-then-recover: the low at index 3 (close=100, so
        # low=99) is a confirmed swing low (2 higher lows on each side,
        # the default SWING_LEFT/SWING_RIGHT=2).
        df = make_df([110, 108, 105, 100, 103, 106, 109, 112, 115, 118, 120])

        stop = structural_stop_price(df, entry_price=120.0)

        self.assertLess(stop, 99.0, "stop should sit below the swing low, not on top of it")
        self.assertLess(stop, stop_loss_price(120.0), "the swing low here is much further than the flat %")

    def test_falls_back_to_flat_pct_when_there_is_not_enough_history_for_a_swing(self):
        df = make_df([110, 108, 105])  # fewer candles than SWING_LEFT+SWING_RIGHT+1 needs

        stop = structural_stop_price(df, entry_price=105.0)

        self.assertAlmostEqual(stop, stop_loss_price(105.0), places=6)

    def test_falls_back_to_flat_pct_when_the_swing_is_degenerate(self):
        # A swing low sitting above the entry price (e.g. price already
        # broke below it) would put the stop on the wrong side of the
        # trade -- must fall back rather than produce a nonsensical stop.
        df = make_df([90, 92, 95, 100, 97, 94, 91, 88, 85, 82, 80])
        entry_price = 80.0  # below every swing low in this series

        stop = structural_stop_price(df, entry_price=entry_price)

        self.assertAlmostEqual(stop, stop_loss_price(entry_price), places=6)

    def test_short_side_uses_the_last_confirmed_swing_high(self):
        df = make_df([90, 92, 95, 100, 97, 94, 100, 105, 108, 105, 102])

        stop = structural_stop_price(df, entry_price=90.0, side="short")

        self.assertGreater(stop, 100.0, "stop should sit above the swing high, not on top of it")


class DailyLossTrackerTests(unittest.TestCase):
    def test_current_loss_pct_is_zero_before_any_trade(self):
        tracker = DailyLossTracker(starting_capital=1000.0)
        self.assertEqual(tracker.current_loss_pct(), 0.0)
        self.assertTrue(tracker.trading_allowed())

    def test_current_loss_pct_reflects_realized_losses_so_far(self):
        tracker = DailyLossTracker(starting_capital=1000.0)
        tracker.record_trade_pnl(-30.0)
        self.assertAlmostEqual(tracker.current_loss_pct(), 3.0, places=6)
        self.assertTrue(tracker.trading_allowed())

    def test_a_win_reduces_the_loss_pct_a_prior_loss_had_built_up(self):
        tracker = DailyLossTracker(starting_capital=1000.0)
        tracker.record_trade_pnl(-30.0)
        tracker.record_trade_pnl(10.0)
        self.assertAlmostEqual(tracker.current_loss_pct(), 2.0, places=6)

    def test_trading_blocked_once_loss_pct_reaches_the_configured_max(self):
        tracker = DailyLossTracker(starting_capital=1000.0)
        tracker.record_trade_pnl(-MAX_DAILY_LOSS_PCT * 10)  # well past the limit

        self.assertGreaterEqual(tracker.current_loss_pct(), MAX_DAILY_LOSS_PCT)
        self.assertFalse(tracker.trading_allowed())

    def test_zero_starting_capital_blocks_trading_without_dividing_by_zero(self):
        tracker = DailyLossTracker(starting_capital=0.0)
        self.assertEqual(tracker.current_loss_pct(), 0.0)
        self.assertFalse(tracker.trading_allowed())


class WeeklyLossTrackerTests(unittest.TestCase):
    def test_current_loss_pct_reflects_realized_losses_so_far(self):
        tracker = WeeklyLossTracker(starting_capital=1000.0)
        tracker.record_trade_pnl(-150.0)
        self.assertAlmostEqual(tracker.current_loss_pct(), 15.0, places=6)

    def test_trading_blocked_once_loss_pct_reaches_the_configured_max(self):
        tracker = WeeklyLossTracker(starting_capital=1000.0)
        tracker.record_trade_pnl(-MAX_WEEKLY_LOSS_PCT * 10)

        self.assertGreaterEqual(tracker.current_loss_pct(), MAX_WEEKLY_LOSS_PCT)
        self.assertFalse(tracker.trading_allowed())

    def test_zero_starting_capital_blocks_trading_without_dividing_by_zero(self):
        tracker = WeeklyLossTracker(starting_capital=0.0)
        self.assertEqual(tracker.current_loss_pct(), 0.0)
        self.assertFalse(tracker.trading_allowed())


def make_trade(id, timestamp, side, price, amount=1.0):
    return {
        "id": id,
        "timestamp": timestamp,
        "datetime": f"2024-01-01T{timestamp:02d}:00:00Z",
        "symbol": "PAXG/USDT",
        "side": side,
        "price": price,
        "amount": amount,
    }


class ConsecutiveLossesTests(unittest.TestCase):
    def test_counts_losses_back_to_back_at_the_end_of_the_journal(self):
        trades = [
            make_trade(1, 1, "buy", 100.0),
            make_trade(2, 2, "sell", 105.0),  # win, doesn't count
            make_trade(3, 3, "buy", 100.0),
            make_trade(4, 4, "sell", 95.0),  # loss
            make_trade(5, 5, "buy", 100.0),
            make_trade(6, 6, "sell", 90.0),  # loss
        ]

        self.assertEqual(consecutive_losses(trades), 2)

    def test_a_win_anywhere_in_the_streak_resets_the_count_from_there(self):
        trades = [
            make_trade(1, 1, "buy", 100.0),
            make_trade(2, 2, "sell", 90.0),  # loss
            make_trade(3, 3, "buy", 100.0),
            make_trade(4, 4, "sell", 110.0),  # win -- breaks the streak
            make_trade(5, 5, "buy", 100.0),
            make_trade(6, 6, "sell", 95.0),  # loss
        ]

        self.assertEqual(consecutive_losses(trades), 1)

    def test_still_open_position_at_the_end_does_not_count_as_anything(self):
        trades = [
            make_trade(1, 1, "buy", 100.0),
            make_trade(2, 2, "sell", 90.0),  # loss
            make_trade(3, 3, "buy", 100.0),  # still open, not a closed trade
        ]

        self.assertEqual(consecutive_losses(trades), 1)

    def test_a_sell_with_no_preceding_buy_is_skipped_not_counted_either_way(self):
        # The real case that already happened once: the account held
        # the asset before the journal started tracking it. Unknown
        # result -- must neither extend nor break the streak.
        trades = [
            make_trade(1, 1, "sell", 100.0),  # no matching buy -- unknown
            make_trade(2, 2, "buy", 100.0),
            make_trade(3, 3, "sell", 90.0),  # loss
        ]

        self.assertEqual(consecutive_losses(trades), 1)

    def test_empty_journal_has_no_streak(self):
        self.assertEqual(consecutive_losses([]), 0)

    def test_out_of_order_input_is_sorted_by_timestamp_first(self):
        trades = [
            make_trade(2, 2, "sell", 90.0),  # loss, but listed first
            make_trade(1, 1, "buy", 100.0),
        ]

        self.assertEqual(consecutive_losses(trades), 1)


if __name__ == "__main__":
    unittest.main()
