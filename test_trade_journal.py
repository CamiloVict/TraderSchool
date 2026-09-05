"""Tests for trade_journal.py.

Run with: python -m unittest test_trade_journal -v
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import trade_journal as tj


class FakeExchange:
    def __init__(self, trades):
        self._trades = trades

    def fetch_my_trades(self, symbol, limit=None):
        return self._trades


def make_trade(id, timestamp, side="buy", price=4000.0, amount=1.0, extra_field="ignored"):
    return {
        "id": id,
        "timestamp": timestamp,
        "datetime": f"2026-01-01T{timestamp:02d}:00:00Z",
        "symbol": "PAXG/USDT",
        "side": side,
        "price": price,
        "amount": amount,
        "cost": price * amount,
        "fee": {"cost": 0.1, "currency": "USDT"},
        "some_unrelated_ccxt_field": extra_field,
    }


class RecordTradesTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.journal_path = os.path.join(self.tmpdir.name, "trade_journal.json")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_first_call_persists_every_fetched_trade(self):
        exchange = FakeExchange([make_trade(1, 1), make_trade(2, 2)])

        with patch("retry.time.sleep"):
            result = tj.record_trades(exchange, "PAXG/USDT", journal_path=self.journal_path)

        self.assertEqual(len(result), 2)
        with open(self.journal_path) as f:
            saved = json.load(f)
        self.assertEqual(len(saved), 2)

    def test_a_later_call_only_adds_trades_not_already_recorded(self):
        exchange = FakeExchange([make_trade(1, 1), make_trade(2, 2)])
        with patch("retry.time.sleep"):
            tj.record_trades(exchange, "PAXG/USDT", journal_path=self.journal_path)

        # Binance's fetch_my_trades naturally returns overlapping
        # history on every call (it's "the last N trades", not "trades
        # since last time") -- id 1 and 2 reappear here alongside the
        # genuinely new id 3.
        exchange._trades = [make_trade(1, 1), make_trade(2, 2), make_trade(3, 3)]
        with patch("retry.time.sleep"):
            result = tj.record_trades(exchange, "PAXG/USDT", journal_path=self.journal_path)

        self.assertEqual(len(result), 3)
        self.assertEqual([t["id"] for t in result], [1, 2, 3])

    def test_does_not_duplicate_when_the_same_trades_are_fetched_again(self):
        exchange = FakeExchange([make_trade(1, 1)])
        with patch("retry.time.sleep"):
            tj.record_trades(exchange, "PAXG/USDT", journal_path=self.journal_path)
            result = tj.record_trades(exchange, "PAXG/USDT", journal_path=self.journal_path)

        self.assertEqual(len(result), 1)

    def test_only_keeps_the_documented_fields(self):
        exchange = FakeExchange([make_trade(1, 1)])

        with patch("retry.time.sleep"):
            result = tj.record_trades(exchange, "PAXG/USDT", journal_path=self.journal_path)

        self.assertNotIn("some_unrelated_ccxt_field", result[0])
        self.assertEqual(set(result[0]), set(tj._KEPT_FIELDS))

    def test_result_is_sorted_by_timestamp_even_if_fetched_out_of_order(self):
        exchange = FakeExchange([make_trade(2, 20), make_trade(1, 10)])

        with patch("retry.time.sleep"):
            result = tj.record_trades(exchange, "PAXG/USDT", journal_path=self.journal_path)

        self.assertEqual([t["id"] for t in result], [1, 2])

    def test_starts_clean_when_no_journal_file_exists_yet(self):
        self.assertFalse(os.path.exists(self.journal_path))
        exchange = FakeExchange([make_trade(1, 1)])

        with patch("retry.time.sleep"):
            result = tj.record_trades(exchange, "PAXG/USDT", journal_path=self.journal_path)

        self.assertEqual(len(result), 1)

    def test_creates_parent_directories_that_do_not_exist_yet(self):
        nested_path = os.path.join(self.tmpdir.name, "nested", "dir", "trade_journal.json")
        exchange = FakeExchange([make_trade(1, 1)])

        with patch("retry.time.sleep"):
            tj.record_trades(exchange, "PAXG/USDT", journal_path=nested_path)

        self.assertTrue(os.path.exists(nested_path))


if __name__ == "__main__":
    unittest.main()
