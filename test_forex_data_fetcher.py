"""Tests for forex_data_fetcher.py -- mocks requests.get throughout, so
these run with no network access and never hit a real OANDA account
(there isn't one configured in this environment anyway). Covers the
request-shaping/parsing/retry logic; the actual shape of a real OANDA
response is documented but unverified end-to-end (see the module's own
docstring) until someone runs this against a real practice account.

Run with: python -m unittest test_forex_data_fetcher -v
"""
import unittest
from unittest.mock import Mock, patch

import requests

import forex_data_fetcher as fdf


def _fake_response(payload: dict, status_ok: bool = True) -> Mock:
    response = Mock()
    response.json.return_value = payload
    response.raise_for_status = Mock() if status_ok else Mock(side_effect=requests.exceptions.HTTPError("boom"))
    return response


def _candle(time: str, o: float, h: float, l: float, c: float, volume: int = 10, complete: bool = True) -> dict:
    return {"time": time, "volume": volume, "complete": complete, "mid": {"o": str(o), "h": str(h), "l": str(l), "c": str(c)}}


class FetchCandlesGuardsTests(unittest.TestCase):
    def test_raises_without_a_token_configured(self):
        with patch("forex_data_fetcher.OANDA_API_TOKEN", ""):
            with self.assertRaises(RuntimeError):
                fdf.fetch_candles("XAU_USD")

    def test_rejects_an_unknown_granularity(self):
        with patch("forex_data_fetcher.OANDA_API_TOKEN", "fake-token"):
            with self.assertRaises(ValueError):
                fdf.fetch_candles("XAU_USD", granularity="not-a-real-code")

    def test_rejects_a_count_over_oandas_own_cap(self):
        with patch("forex_data_fetcher.OANDA_API_TOKEN", "fake-token"):
            with self.assertRaises(ValueError):
                fdf.fetch_candles("XAU_USD", count=fdf.MAX_CANDLES_PER_REQUEST + 1)

    def test_rejects_an_unknown_environment(self):
        with patch("forex_data_fetcher.OANDA_API_TOKEN", "fake-token"), patch(
            "forex_data_fetcher.OANDA_ENVIRONMENT", "sandbox"
        ):
            with self.assertRaises(ValueError):
                fdf.fetch_candles("XAU_USD")


class FetchCandlesParsingTests(unittest.TestCase):
    def test_parses_a_successful_response_into_a_dataframe(self):
        payload = {
            "candles": [
                _candle("2024-01-01T00:00:00.000000000Z", 2000.0, 2005.0, 1998.0, 2002.0),
                _candle("2024-01-01T01:00:00.000000000Z", 2002.0, 2010.0, 2001.0, 2008.0),
            ]
        }
        with patch("forex_data_fetcher.OANDA_API_TOKEN", "fake-token"), patch(
            "forex_data_fetcher.requests.get", return_value=_fake_response(payload)
        ) as mock_get:
            df = fdf.fetch_candles("XAU_USD", granularity="H1", count=2)

        self.assertEqual(len(df), 2)
        self.assertEqual(list(df.columns), ["open", "high", "low", "close", "volume"])
        self.assertAlmostEqual(df.iloc[0]["close"], 2002.0)
        self.assertAlmostEqual(df.iloc[1]["high"], 2010.0)
        # Practice vs. live must resolve to different URLs -- a
        # practice token pointed at the wrong one is exactly the
        # mistake OANDA_ENVIRONMENT's own default exists to prevent.
        called_url = mock_get.call_args.args[0]
        self.assertIn("fxpractice", called_url)
        self.assertEqual(mock_get.call_args.kwargs["headers"]["Authorization"], "Bearer fake-token")

    def test_skips_the_still_forming_current_candle(self):
        payload = {
            "candles": [
                _candle("2024-01-01T00:00:00.000000000Z", 2000.0, 2005.0, 1998.0, 2002.0),
                _candle("2024-01-01T01:00:00.000000000Z", 2002.0, 2010.0, 2001.0, 2008.0, complete=False),
            ]
        }
        with patch("forex_data_fetcher.OANDA_API_TOKEN", "fake-token"), patch(
            "forex_data_fetcher.requests.get", return_value=_fake_response(payload)
        ):
            df = fdf.fetch_candles("XAU_USD")

        self.assertEqual(len(df), 1)

    def test_raises_on_an_http_error_response(self):
        payload = {"errorMessage": "Invalid token"}
        with patch("forex_data_fetcher.OANDA_API_TOKEN", "bad-token"), patch(
            "forex_data_fetcher.requests.get", return_value=_fake_response(payload, status_ok=False)
        ):
            with self.assertRaises(requests.exceptions.HTTPError):
                fdf.fetch_candles("XAU_USD")


class RetryTests(unittest.TestCase):
    def test_retries_a_transient_connection_error(self):
        payload = {"candles": [_candle("2024-01-01T00:00:00.000000000Z", 2000.0, 2005.0, 1998.0, 2002.0)]}
        with patch("forex_data_fetcher.OANDA_API_TOKEN", "fake-token"), patch(
            "forex_data_fetcher.requests.get",
            side_effect=[requests.exceptions.ConnectionError("blip"), _fake_response(payload)],
        ), patch("forex_data_fetcher.time.sleep"):
            df = fdf.fetch_candles("XAU_USD")

        self.assertEqual(len(df), 1)

    def test_gives_up_after_max_attempts(self):
        with patch("forex_data_fetcher.OANDA_API_TOKEN", "fake-token"), patch(
            "forex_data_fetcher.requests.get", side_effect=requests.exceptions.ConnectionError("down")
        ), patch("forex_data_fetcher.time.sleep"):
            with self.assertRaises(requests.exceptions.ConnectionError):
                fdf.fetch_candles("XAU_USD")

    def test_does_not_retry_an_http_error(self):
        # An HTTP error means OANDA answered -- retrying it would just
        # delay reporting a real problem (e.g. a bad token).
        payload = {"errorMessage": "Invalid token"}
        with patch("forex_data_fetcher.OANDA_API_TOKEN", "bad-token"), patch(
            "forex_data_fetcher.requests.get", return_value=_fake_response(payload, status_ok=False)
        ) as mock_get:
            with self.assertRaises(requests.exceptions.HTTPError):
                fdf.fetch_candles("XAU_USD")

        self.assertEqual(mock_get.call_count, 1)


class FetchCandlesHistoryTests(unittest.TestCase):
    def test_pages_forward_until_caught_up_to_now(self):
        first_page = {
            "candles": [
                _candle("2024-01-01T00:00:00.000000000Z", 2000.0, 2005.0, 1998.0, 2002.0),
                _candle("2024-01-01T01:00:00.000000000Z", 2002.0, 2010.0, 2001.0, 2008.0),
            ]
        }
        second_page = {"candles": [_candle("2024-01-01T02:00:00.000000000Z", 2008.0, 2012.0, 2005.0, 2010.0)]}
        # First page exactly fills a (patched-down) request cap -- the
        # pagination loop's own signal to keep going instead of
        # stopping, since a full page means there may be more. Second
        # page comes back short, which is the real stop signal.
        with patch("forex_data_fetcher.OANDA_API_TOKEN", "fake-token"), patch(
            "forex_data_fetcher.MAX_CANDLES_PER_REQUEST", 2
        ), patch(
            "forex_data_fetcher.requests.get",
            side_effect=[_fake_response(first_page), _fake_response(second_page)],
        ) as mock_get:
            df = fdf.fetch_candles_history("XAU_USD", since="2024-01-01T00:00:00Z")

        self.assertEqual(len(df), 3)
        self.assertEqual(mock_get.call_count, 2)

    def test_empty_history_returns_an_empty_dataframe_with_the_right_columns(self):
        with patch("forex_data_fetcher.OANDA_API_TOKEN", "fake-token"), patch(
            "forex_data_fetcher.requests.get", return_value=_fake_response({"candles": []})
        ):
            df = fdf.fetch_candles_history("XAU_USD", since="2024-01-01T00:00:00Z")

        self.assertTrue(df.empty)
        self.assertEqual(list(df.columns), ["open", "high", "low", "close", "volume"])


if __name__ == "__main__":
    unittest.main()
