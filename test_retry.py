"""Tests for retry.py.

Run with: python -m unittest test_retry -v
"""
import unittest
from unittest.mock import patch

import ccxt

from retry import call_with_retries


class CallWithRetriesTests(unittest.TestCase):
    def test_returns_the_result_on_a_clean_first_call(self):
        calls = []

        def func():
            calls.append(1)
            return "ok"

        with patch("retry.time.sleep") as mock_sleep:
            result = call_with_retries(func)

        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 1)
        mock_sleep.assert_not_called()

    def test_retries_on_network_error_and_eventually_succeeds(self):
        calls = {"n": 0}

        def func():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ccxt.RequestTimeout("timed out")
            return "ok"

        with patch("retry.time.sleep") as mock_sleep:
            result = call_with_retries(func, max_attempts=5)

        self.assertEqual(result, "ok")
        self.assertEqual(calls["n"], 3)
        self.assertEqual(mock_sleep.call_count, 2)  # slept after attempts 1 and 2, not after the successful 3rd

    def test_raises_the_network_error_once_max_attempts_is_exhausted(self):
        def func():
            raise ccxt.ExchangeNotAvailable("down")

        with patch("retry.time.sleep"):
            with self.assertRaises(ccxt.ExchangeNotAvailable):
                call_with_retries(func, max_attempts=3)

    def test_a_non_network_error_propagates_immediately_without_retrying(self):
        calls = {"n": 0}

        def func():
            calls["n"] += 1
            raise ccxt.BadSymbol("not listed")

        with patch("retry.time.sleep") as mock_sleep:
            with self.assertRaises(ccxt.BadSymbol):
                call_with_retries(func, max_attempts=5)

        self.assertEqual(calls["n"], 1)
        mock_sleep.assert_not_called()

    def test_backoff_delay_doubles_each_attempt(self):
        def func():
            raise ccxt.RequestTimeout("timed out")

        with patch("retry.time.sleep") as mock_sleep:
            with self.assertRaises(ccxt.RequestTimeout):
                call_with_retries(func, max_attempts=4, base_delay=1.0)

        mock_sleep.assert_any_call(1.0)
        mock_sleep.assert_any_call(2.0)
        mock_sleep.assert_any_call(4.0)

    def test_passes_through_args_and_kwargs(self):
        def func(a, b, c=None):
            return (a, b, c)

        with patch("retry.time.sleep"):
            result = call_with_retries(func, 1, 2, c=3)

        self.assertEqual(result, (1, 2, 3))


if __name__ == "__main__":
    unittest.main()
