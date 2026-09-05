"""Tests for weekly_loss_state.py.

Run with: python -m unittest test_weekly_loss_state -v
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import weekly_loss_state as wls


class LoadOrInitStartingCapitalTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_path = os.path.join(self.tmpdir.name, "weekly_loss_state.json")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_first_call_of_the_week_adopts_current_equity_and_persists_it(self):
        result = wls.load_or_init_starting_capital(1000.0, state_path=self.state_path)

        self.assertEqual(result, 1000.0)
        with open(self.state_path) as f:
            saved = json.load(f)
        self.assertEqual(saved["starting_capital"], 1000.0)

    def test_later_calls_the_same_iso_week_return_the_persisted_value_unchanged(self):
        wls.load_or_init_starting_capital(1000.0, state_path=self.state_path)

        # Equity moved (a trade happened), but the week's reference
        # point must not follow it -- that's the whole point of a
        # fixed starting-of-week baseline.
        result = wls.load_or_init_starting_capital(950.0, state_path=self.state_path)

        self.assertEqual(result, 1000.0)

    def test_a_new_iso_week_resets_the_reference_to_current_equity(self):
        with patch("weekly_loss_state._current_week_utc", return_value="2026-W01"):
            wls.load_or_init_starting_capital(1000.0, state_path=self.state_path)

        with patch("weekly_loss_state._current_week_utc", return_value="2026-W02"):
            result = wls.load_or_init_starting_capital(950.0, state_path=self.state_path)

        self.assertEqual(result, 950.0)
        with open(self.state_path) as f:
            saved = json.load(f)
        self.assertEqual(saved["week"], "2026-W02")

    def test_missing_state_file_behaves_like_the_first_call_of_the_week(self):
        self.assertFalse(os.path.exists(self.state_path))

        result = wls.load_or_init_starting_capital(500.0, state_path=self.state_path)

        self.assertEqual(result, 500.0)

    def test_creates_parent_directories_that_do_not_exist_yet(self):
        nested_path = os.path.join(self.tmpdir.name, "nested", "dir", "weekly_loss_state.json")

        wls.load_or_init_starting_capital(1000.0, state_path=nested_path)

        self.assertTrue(os.path.exists(nested_path))


if __name__ == "__main__":
    unittest.main()
