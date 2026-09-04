"""Tests for risk_manager.position_size's stop_price override — the
piece the Setup Engine path in main.py needs to size against a
structural invalidation level instead of the flat STOP_LOSS_PCT.

Run with: python -m unittest test_risk_manager -v
"""
import unittest

from risk_manager import position_size, stop_loss_price


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


if __name__ == "__main__":
    unittest.main()
