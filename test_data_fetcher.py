"""Tests for data_fetcher.py's retry wiring specifically -- fetch_ohlcv
and fetch_ohlcv_history should recover from a transient ccxt.NetworkError
via retry.call_with_retries rather than failing the whole cycle on one
blip. The rest of this module is thin ccxt wrapping without its own
tests; this file exists because the retry behavior is new and worth
locking in.

Run with: python -m unittest test_data_fetcher -v
"""
import unittest
from unittest.mock import patch

import ccxt

from data_fetcher import fetch_ohlcv, fetch_ohlcv_history


class FakeExchange:
    """Raises a transient NetworkError the first `fail_times` calls to
    fetch_ohlcv, then returns `candles`."""

    def __init__(self, candles, fail_times=0):
        self._candles = candles
        self._fail_times = fail_times
        self.calls = 0

    def fetch_ohlcv(self, symbol, timeframe=None, limit=None, since=None):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise ccxt.RequestTimeout("timed out")
        return self._candles


class FetchOhlcvRetryTests(unittest.TestCase):
    def test_recovers_from_a_transient_network_error(self):
        candles = [[1704067200000, 100.0, 101.0, 99.0, 100.5, 10.0]]
        exchange = FakeExchange(candles, fail_times=2)

        with patch("retry.time.sleep"):
            df = fetch_ohlcv(exchange, symbol="BTC/USDT", timeframe="1h", limit=1)

        self.assertEqual(len(df), 1)
        self.assertEqual(exchange.calls, 3)

    def test_gives_up_after_repeated_network_errors(self):
        exchange = FakeExchange([], fail_times=999)

        with patch("retry.time.sleep"):
            with self.assertRaises(ccxt.RequestTimeout):
                fetch_ohlcv(exchange, symbol="BTC/USDT", timeframe="1h", limit=1)


class FetchOhlcvHistoryRetryTests(unittest.TestCase):
    def test_recovers_from_a_transient_network_error_mid_pagination(self):
        candles = [[1704067200000, 100.0, 101.0, 99.0, 100.5, 10.0]]
        exchange = FakeExchange(candles, fail_times=2)

        with patch("retry.time.sleep"):
            df = fetch_ohlcv_history(exchange, symbol="BTC/USDT", timeframe="1h")

        self.assertEqual(len(df), 1)


if __name__ == "__main__":
    unittest.main()
