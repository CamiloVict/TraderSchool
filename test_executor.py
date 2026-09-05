"""Tests for executor.py's idempotency (newClientOrderId) and retry
wiring — the two additions this session made to protect against a
create_order() call that timed out client-side but may have actually
gone through, and against transient network blips on read calls.
Order-placement/cancellation happy paths already have broader coverage
via test_trading_cycle.py's FakeExchange; this file is about the two
specific new behaviors.

Run with: python -m unittest test_executor -v
"""
import unittest
from unittest.mock import patch

import ccxt

from executor import (
    _client_order_id,
    get_base_asset_balance,
    get_last_fill_price,
    get_open_stop_loss_orders,
    meets_exchange_minimums,
    place_market_order,
    place_stop_loss_order,
)


class MarketLookupExchange:
    """Stand-in exposing only .market(symbol), for
    meets_exchange_minimums()'s own tests."""

    def __init__(self, limits: dict):
        self._limits = limits

    def market(self, symbol):
        return {"limits": self._limits}


class RecordingExchange:
    """Captures create_order() calls; fetch_* methods raise a
    transient NetworkError `fail_times` times before returning
    `response`, to exercise retry.call_with_retries."""

    def __init__(self, response=None, fail_times=0):
        self.response = response
        self._fail_times = fail_times
        self.calls = 0
        self.created_orders = []

    def create_order(self, symbol, type=None, side=None, amount=None, price=None, params=None):
        order = {"symbol": symbol, "type": type, "side": side, "amount": amount, "price": price, "params": params or {}}
        self.created_orders.append(order)
        return order

    def _maybe_fail(self, response):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise ccxt.RequestTimeout("timed out")
        return response

    def fetch_open_orders(self, symbol):
        return self._maybe_fail(self.response or [])

    def fetch_my_trades(self, symbol, limit=None):
        return self._maybe_fail(self.response or [])

    def fetch_balance(self):
        return self._maybe_fail(self.response or {"free": {}, "used": {}, "total": {}})


class ClientOrderIdTests(unittest.TestCase):
    def test_same_symbol_side_and_hour_produce_the_same_id(self):
        with patch("executor.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "2026090504"
            first = _client_order_id("PAXG/USDT", "buy")
            second = _client_order_id("PAXG/USDT", "buy")

        self.assertEqual(first, second)

    def test_different_side_produces_a_different_id(self):
        with patch("executor.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "2026090504"
            buy_id = _client_order_id("PAXG/USDT", "buy")
            sell_id = _client_order_id("PAXG/USDT", "sell")

        self.assertNotEqual(buy_id, sell_id)

    def test_different_hour_produces_a_different_id(self):
        with patch("executor.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "2026090504"
            first = _client_order_id("PAXG/USDT", "buy")
            mock_dt.now.return_value.strftime.return_value = "2026090505"
            second = _client_order_id("PAXG/USDT", "buy")

        self.assertNotEqual(first, second)

    def test_market_order_carries_a_client_order_id(self):
        exchange = RecordingExchange()

        with patch("executor.USE_TESTNET", True):
            place_market_order(exchange, "PAXG/USDT", "buy", 1.0)

        self.assertIn("newClientOrderId", exchange.created_orders[0]["params"])

    def test_stop_loss_order_carries_a_client_order_id_alongside_stop_price(self):
        exchange = RecordingExchange()

        with patch("executor.USE_TESTNET", True):
            place_stop_loss_order(exchange, "PAXG/USDT", 1.0, 100.0)

        params = exchange.created_orders[0]["params"]
        self.assertIn("newClientOrderId", params)
        self.assertEqual(params["stopPrice"], 100.0)


class MeetsExchangeMinimumsTests(unittest.TestCase):
    def test_ok_when_amount_and_notional_both_clear_the_markets_limits(self):
        exchange = MarketLookupExchange({"amount": {"min": 0.0001}, "cost": {"min": 10.0}})

        ok, reason = meets_exchange_minimums(exchange, "BTC/USDT", amount=0.01, price=50_000.0)

        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_rejects_an_amount_below_the_markets_minimum_quantity(self):
        exchange = MarketLookupExchange({"amount": {"min": 0.001}, "cost": {"min": 10.0}})

        ok, reason = meets_exchange_minimums(exchange, "BTC/USDT", amount=0.0001, price=50_000.0)

        self.assertFalse(ok)
        self.assertIn("minimum order quantity", reason)

    def test_rejects_a_notional_below_the_markets_minimum_notional(self):
        # Amount alone clears the LOT_SIZE minimum, but amount*price is
        # still below MIN_NOTIONAL -- a small stop distance can produce
        # exactly this on a small account.
        exchange = MarketLookupExchange({"amount": {"min": 0.0001}, "cost": {"min": 10.0}})

        ok, reason = meets_exchange_minimums(exchange, "BTC/USDT", amount=0.0001, price=50_000.0)
        # 0.0001 * 50000 = 5.0, below the 10.0 minimum notional.

        self.assertFalse(ok)
        self.assertIn("minimum notional", reason)

    def test_ok_when_the_market_reports_no_limits_at_all(self):
        exchange = MarketLookupExchange({"amount": {"min": None}, "cost": {"min": None}})

        ok, reason = meets_exchange_minimums(exchange, "BTC/USDT", amount=0.0000001, price=1.0)

        self.assertTrue(ok)
        self.assertIsNone(reason)


class ReadRetryTests(unittest.TestCase):
    def test_get_open_stop_loss_orders_recovers_from_a_transient_network_error(self):
        exchange = RecordingExchange(response=[{"side": "sell", "triggerPrice": 100.0}], fail_times=2)

        with patch("retry.time.sleep"):
            result = get_open_stop_loss_orders(exchange, "PAXG/USDT")

        self.assertEqual(len(result), 1)

    def test_get_last_fill_price_recovers_from_a_transient_network_error(self):
        exchange = RecordingExchange(response=[{"side": "buy", "price": 4000.0}], fail_times=2)

        with patch("retry.time.sleep"):
            price = get_last_fill_price(exchange, "PAXG/USDT", "buy")

        self.assertEqual(price, 4000.0)

    def test_get_base_asset_balance_recovers_from_a_transient_network_error(self):
        exchange = RecordingExchange(response={"free": {"PAXG": 2.0}, "used": {}, "total": {}}, fail_times=2)

        with patch("retry.time.sleep"):
            balance = get_base_asset_balance(exchange, "PAXG/USDT")

        self.assertEqual(balance, 2.0)


if __name__ == "__main__":
    unittest.main()
