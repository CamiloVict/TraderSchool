"""Offline tests for main.run_trading_cycle's stop-loss handling.

No network access: a FakeExchange stands in for ccxt.binance, so these
exercise the actual order-placement/cancellation logic in main.py and
executor.py without touching Testnet. Run with:

    python -m unittest test_trading_cycle -v
"""
import unittest
from unittest.mock import patch

import pandas as pd

import main
import risk_manager
from config import SYMBOL


def make_candles(n: int, start_price: float, step: float):
    """`n` hourly candles, close price moving by `step` each candle —
    enough of a trend that the EMA 20/50 crossover signal is
    unambiguous on the last row (up for step > 0, down for step < 0)."""
    start = pd.Timestamp("2024-01-01", tz="UTC")
    rows = []
    for i in range(n):
        ts_ms = int((start + pd.Timedelta(hours=i)).timestamp() * 1000)
        price = start_price + step * i
        rows.append([ts_ms, price, price, price, price, 1.0])
    return rows


class FakeExchange:
    """Minimal ccxt.binance stand-in covering only what main.py /
    executor.py call: fetch_ohlcv, fetch_balance, create_order,
    cancel_order, fetch_open_orders, fetch_my_trades."""

    def __init__(self, candles, free=None, locked=None, open_orders=None, trades=None):
        self._candles = candles
        self.free = dict(free or {})
        self.locked = dict(locked or {})
        self._open_orders = list(open_orders or [])
        self._trades = list(trades or [])
        self.created_orders = []
        self.cancelled_ids = []

    def fetch_ohlcv(self, symbol, timeframe=None, limit=None, since=None):
        return self._candles

    def fetch_balance(self):
        total = {
            asset: self.free.get(asset, 0.0) + self.locked.get(asset, 0.0)
            for asset in set(self.free) | set(self.locked)
        }
        return {"free": dict(self.free), "used": dict(self.locked), "total": total}

    def create_order(self, symbol, type=None, side=None, amount=None, price=None, params=None):
        params = params or {}
        # Market orders don't take a price; fill at the latest close,
        # like a real market order would.
        fill_price = price if price is not None else self._candles[-1][4]
        order = {
            "id": f"order-{len(self.created_orders) + 1}",
            "symbol": symbol,
            "type": type,
            "side": side,
            "amount": amount,
            "price": price,
            "filled": amount,
            "average": fill_price,
            "cost": fill_price * amount,
            "triggerPrice": params.get("stopPrice"),
        }
        self.created_orders.append(order)
        return order

    def cancel_order(self, order_id, symbol):
        self.cancelled_ids.append(order_id)
        for order in self._open_orders:
            if order["id"] == order_id:
                base = symbol.split("/")[0]
                self.locked[base] = self.locked.get(base, 0.0) - order["amount"]
                self.free[base] = self.free.get(base, 0.0) + order["amount"]
                self._open_orders.remove(order)
                return order
        raise KeyError(order_id)

    def fetch_open_orders(self, symbol):
        return list(self._open_orders)

    def fetch_my_trades(self, symbol, limit=None):
        return list(self._trades)


class RunTradingCycleTests(unittest.TestCase):
    def test_buy_places_a_protective_stop_loss(self):
        candles = make_candles(200, start_price=10000, step=10)  # uptrend -> signal 1
        exchange = FakeExchange(candles, free={"USDT": 1000.0})

        result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "buy")
        self.assertIsNotNone(result["stop_order_id"])
        buy_orders = [o for o in exchange.created_orders if o["side"] == "buy"]
        stop_orders = [o for o in exchange.created_orders if o["side"] == "sell"]
        self.assertEqual(len(buy_orders), 1)
        self.assertEqual(buy_orders[0]["type"], "market")
        self.assertEqual(len(stop_orders), 1)
        self.assertIsNotNone(stop_orders[0]["triggerPrice"])
        entry_price = buy_orders[0]["average"]
        expected_stop = risk_manager.stop_loss_price(entry_price)
        self.assertAlmostEqual(stop_orders[0]["triggerPrice"], expected_stop, places=6)

    def test_exit_signal_cancels_stale_stop_before_selling(self):
        candles = make_candles(200, start_price=10000, step=-10)  # downtrend -> signal 0
        last_price = candles[-1][4]
        base = SYMBOL.split("/")[0]
        stale_stop = {
            "id": "stop-1",
            "side": "sell",
            "amount": 1.0,
            "triggerPrice": 9000.0,
        }
        exchange = FakeExchange(
            candles,
            locked={base: 1.0},
            open_orders=[stale_stop],
        )
        self.assertGreater(1.0 * last_price, 10)  # sanity: counts as "in position"

        result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "sell")
        self.assertEqual(exchange.cancelled_ids, ["stop-1"])
        sell_orders = [o for o in exchange.created_orders if o["side"] == "sell"]
        self.assertEqual(len(sell_orders), 1)
        self.assertEqual(sell_orders[0]["type"], "market")
        self.assertEqual(sell_orders[0]["amount"], 1.0)  # unlocked by the cancel

    def test_self_heals_a_missing_stop_loss(self):
        candles = make_candles(200, start_price=10000, step=10)  # uptrend -> signal 1
        base = SYMBOL.split("/")[0]
        last_price = candles[-1][4]
        exchange = FakeExchange(
            candles,
            free={base: 1.0},  # already in position, no open stop order
            trades=[{"side": "buy", "price": 10500.0, "amount": 1.0}],
        )
        self.assertGreater(1.0 * last_price, 10)

        result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "stop_loss_replaced")
        self.assertIsNotNone(result["stop_order_id"])
        stop_orders = [o for o in exchange.created_orders if o["side"] == "sell"]
        self.assertEqual(len(stop_orders), 1)
        expected_stop = risk_manager.stop_loss_price(10500.0)
        self.assertAlmostEqual(stop_orders[0]["triggerPrice"], expected_stop, places=6)

    def test_pattern_filter_blocks_entry_when_enabled_and_bearish(self):
        candles = make_candles(200, start_price=10000, step=10)  # uptrend -> signal 1
        exchange = FakeExchange(candles, free={"USDT": 1000.0})

        always_bearish = lambda data, *a, **k: pd.Series(-1, index=data.index)
        with patch("main.USE_PATTERN_FILTER", True), patch(
            "main.detect_double_patterns", side_effect=always_bearish
        ):
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "entry_blocked_by_pattern")
        self.assertEqual(exchange.created_orders, [])

    def test_pattern_filter_off_ignores_pattern_detection(self):
        candles = make_candles(200, start_price=10000, step=10)  # uptrend -> signal 1
        exchange = FakeExchange(candles, free={"USDT": 1000.0})

        with patch("main.USE_PATTERN_FILTER", False), patch("main.detect_double_patterns") as mock_detect:
            result = main.run_trading_cycle(exchange)

        mock_detect.assert_not_called()
        self.assertEqual(result["action"], "buy")


if __name__ == "__main__":
    unittest.main()
