"""Tests for daily_loss_state.py.

Run with: python -m unittest test_daily_loss_state -v
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import daily_loss_state as dls


class LoadOrInitStartingCapitalTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_path = os.path.join(self.tmpdir.name, "daily_loss_state.json")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_first_call_of_the_day_adopts_current_equity_and_persists_it(self):
        result = dls.load_or_init_starting_capital(1000.0, state_path=self.state_path)

        self.assertEqual(result, 1000.0)
        with open(self.state_path) as f:
            saved = json.load(f)
        self.assertEqual(saved["starting_capital"], 1000.0)

    def test_later_calls_the_same_utc_day_return_the_persisted_value_unchanged(self):
        dls.load_or_init_starting_capital(1000.0, state_path=self.state_path)

        # Equity moved (a trade happened), but the day's reference point
        # must not follow it -- that's the whole point of a fixed
        # starting-of-day baseline.
        result = dls.load_or_init_starting_capital(950.0, state_path=self.state_path)

        self.assertEqual(result, 1000.0)

    def test_a_new_utc_day_resets_the_reference_to_current_equity(self):
        with patch("daily_loss_state._today_utc", return_value="2026-01-01"):
            dls.load_or_init_starting_capital(1000.0, state_path=self.state_path)

        with patch("daily_loss_state._today_utc", return_value="2026-01-02"):
            result = dls.load_or_init_starting_capital(950.0, state_path=self.state_path)

        self.assertEqual(result, 950.0)
        with open(self.state_path) as f:
            saved = json.load(f)
        self.assertEqual(saved["day"], "2026-01-02")

    def test_missing_state_file_behaves_like_the_first_call_of_the_day(self):
        self.assertFalse(os.path.exists(self.state_path))

        result = dls.load_or_init_starting_capital(500.0, state_path=self.state_path)

        self.assertEqual(result, 500.0)

    def test_creates_parent_directories_that_do_not_exist_yet(self):
        nested_path = os.path.join(self.tmpdir.name, "nested", "dir", "daily_loss_state.json")

        dls.load_or_init_starting_capital(1000.0, state_path=nested_path)

        self.assertTrue(os.path.exists(nested_path))


if __name__ == "__main__":
    unittest.main()
