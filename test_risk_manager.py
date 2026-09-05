"""Tests for risk_manager.position_size's stop_price override — the
piece the Setup Engine path in main.py needs to size against a
structural invalidation level instead of the flat STOP_LOSS_PCT — for
structural_stop_price's own swing-based stop derivation, and for
consecutive_losses' trade_journal.py-shaped streak counting.

Run with: python -m unittest test_risk_manager -v
"""
import unittest

import pandas as pd

from risk_manager import consecutive_losses, position_size, stop_loss_price, structural_stop_price


def make_df(closes: list) -> pd.DataFrame:
    start = pd.Timestamp("2024-01-01", tz="UTC")
    rows = []
    index = []
    for i, close in enumerate(closes):
        rows.append({"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1.0})
        index.append(start + pd.Timedelta(hours=i))
    return pd.DataFrame(rows, index=pd.DatetimeIndex(index, name="timestamp"))


class PositionSizeStopPriceTests(unittest.TestCase):
    def test_defaults_to_the_flat_stop_loss_pct_when_no_stop_price_given(self):
        size = position_size(1000.0, 100.0)
        implied_stop = stop_loss_price(100.0)
        expected = (1000.0 * 0.01) / (abs(100.0 - implied_stop) / 100.0) / 100.0

        self.assertAlmostEqual(size, expected, places=6)

    def test_explicit_stop_price_overrides_the_flat_percentage(self):
        # A stop twice as far away as the default STOP_LOSS_PCT should
        # size to roughly half the position for the same risk budget.
        default_size = position_size(1000.0, 100.0)
        far_stop = 100.0 - (100.0 - stop_loss_price(100.0)) * 2
        wide_size = position_size(1000.0, 100.0, stop_price=far_stop)

        self.assertAlmostEqual(wide_size, default_size / 2, places=6)

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
