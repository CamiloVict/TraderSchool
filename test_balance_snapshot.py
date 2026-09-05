"""Tests for balance_snapshot.py.

Run with: python -m unittest test_balance_snapshot -v
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import balance_snapshot as bs


class FakeExchange:
    def __init__(self, totals):
        self._totals = totals

    def fetch_balance(self):
        return {"total": self._totals}


class RecordBalanceTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.snapshot_path = os.path.join(self.tmpdir.name, "balance_history.json")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_first_call_creates_a_snapshot_with_only_non_zero_balances(self):
        exchange = FakeExchange({"USDT": 500.0, "PAXG": 0.05, "BTC": 0.0})

        with patch("retry.time.sleep"):
            snapshot = bs.record_balance(exchange, snapshot_path=self.snapshot_path)

        self.assertEqual(snapshot["balances"], {"USDT": 500.0, "PAXG": 0.05})
        with open(self.snapshot_path) as f:
            saved = json.load(f)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["balances"], {"USDT": 500.0, "PAXG": 0.05})

    def test_a_later_call_appends_rather_than_overwriting(self):
        exchange = FakeExchange({"USDT": 500.0})
        with patch("retry.time.sleep"):
            bs.record_balance(exchange, snapshot_path=self.snapshot_path)

        exchange._totals = {"USDT": 480.0, "PAXG": 0.05}
        with patch("retry.time.sleep"):
            bs.record_balance(exchange, snapshot_path=self.snapshot_path)

        with open(self.snapshot_path) as f:
            saved = json.load(f)
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[0]["balances"], {"USDT": 500.0})
        self.assertEqual(saved[1]["balances"], {"USDT": 480.0, "PAXG": 0.05})

    def test_starts_clean_when_no_snapshot_file_exists_yet(self):
        self.assertFalse(os.path.exists(self.snapshot_path))
        exchange = FakeExchange({"USDT": 1000.0})

        with patch("retry.time.sleep"):
            bs.record_balance(exchange, snapshot_path=self.snapshot_path)

        with open(self.snapshot_path) as f:
            saved = json.load(f)
        self.assertEqual(len(saved), 1)

    def test_creates_parent_directories_that_do_not_exist_yet(self):
        nested_path = os.path.join(self.tmpdir.name, "nested", "dir", "balance_history.json")
        exchange = FakeExchange({"USDT": 1000.0})

        with patch("retry.time.sleep"):
            bs.record_balance(exchange, snapshot_path=nested_path)

        self.assertTrue(os.path.exists(nested_path))


if __name__ == "__main__":
    unittest.main()
