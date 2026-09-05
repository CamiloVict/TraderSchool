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
from config import MAX_CONSECUTIVE_LOSSES, SYMBOL
from strategy import SLOW_PERIOD


def _stub_disk_touching_side_effects(test_case):
    """run_trading_cycle() has side effects that persist to real files
    under data/ (daily_loss_state.json, weekly_loss_state.json,
    trade_journal.json, balance_history.json, heartbeat.json) so they
    survive across main.py --trade's separate cron-invoked processes --
    exactly what a test run must NOT do, since one test's values would
    otherwise leak into the next as a stale baseline or a lingering
    record. Each has its own dedicated tests (test_daily_loss_state.py,
    test_trade_journal.py, test_balance_snapshot.py,
    test_heartbeat.py); every test here just needs them out of the way.
    """
    loss_patcher = patch("main.load_or_init_starting_capital", side_effect=lambda equity, **_: equity)
    loss_patcher.start()
    test_case.addCleanup(loss_patcher.stop)

    weekly_loss_patcher = patch(
        "main.weekly_loss_state.load_or_init_starting_capital", side_effect=lambda equity, **_: equity
    )
    weekly_loss_patcher.start()
    test_case.addCleanup(weekly_loss_patcher.stop)

    journal_patcher = patch("main.record_trades")
    journal_patcher.start()
    test_case.addCleanup(journal_patcher.stop)

    # Empty by default -- no test here is about a pre-existing losing
    # streak unless it patches this itself (see
    # test_blocks_a_new_entry_when_max_consecutive_losses_is_hit).
    read_journal_patcher = patch("main.read_journal", return_value=[])
    read_journal_patcher.start()
    test_case.addCleanup(read_journal_patcher.stop)

    balance_patcher = patch("main.record_balance")
    balance_patcher.start()
    test_case.addCleanup(balance_patcher.stop)

    heartbeat_patcher = patch("main.record_heartbeat")
    heartbeat_patcher.start()
    test_case.addCleanup(heartbeat_patcher.stop)


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


def make_losing_streak(n: int):
    """`n` closed round trips (buy then sell, each sell below its own
    buy), trade_journal.py-shaped -- for
    risk_manager.consecutive_losses()."""
    trades = []
    start = pd.Timestamp("2024-01-01", tz="UTC")
    for i in range(n):
        buy_time = start + pd.Timedelta(hours=2 * i)
        sell_time = start + pd.Timedelta(hours=2 * i + 1)
        trades.append(
            {
                "id": f"buy-{i}",
                "timestamp": int(buy_time.timestamp() * 1000),
                "datetime": buy_time.isoformat(),
                "symbol": SYMBOL,
                "side": "buy",
                "price": 100.0,
                "amount": 1.0,
            }
        )
        trades.append(
            {
                "id": f"sell-{i}",
                "timestamp": int(sell_time.timestamp() * 1000),
                "datetime": sell_time.isoformat(),
                "symbol": SYMBOL,
                "side": "sell",
                "price": 95.0,  # below the buy -> a loss
                "amount": 1.0,
            }
        )
    return trades


def make_candles_from_closes(closes: list):
    """Hourly candles from a list of close prices, open/high/low set
    a small fixed offset from close -- unlike make_candles' flat OHLC,
    this actually forms real swing highs/lows for structural-stop tests."""
    start = pd.Timestamp("2024-01-01", tz="UTC")
    rows = []
    for i, close in enumerate(closes):
        ts_ms = int((start + pd.Timedelta(hours=i)).timestamp() * 1000)
        rows.append([ts_ms, close, close + 0.5, close - 0.5, close, 1.0])
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
    def setUp(self):
        _stub_disk_touching_side_effects(self)

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

    def test_uses_the_structural_stop_when_enabled(self):
        # A real dip (forms a confirmed swing low around 9950) then a
        # rise past the old high -> EMA crossover entry. With
        # USE_STRUCTURAL_STOP on, the placed stop should come from that
        # swing low (risk_manager.structural_stop_price), not the flat %.
        warmup = [10000.0] * (SLOW_PERIOD + 5)
        dip = [10000.0 - i * 10 for i in range(1, 6)]  # down to 9950
        rise = [dip[-1] + i * 10 for i in range(1, 31)]  # back up and beyond
        candles = make_candles_from_closes(warmup + dip + rise)
        exchange = FakeExchange(candles, free={"USDT": 1000.0})

        with patch("main.USE_STRUCTURAL_STOP", True):
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "buy")
        stop_orders = [o for o in exchange.created_orders if o["side"] == "sell"]
        self.assertEqual(len(stop_orders), 1)
        entry_price = [o for o in exchange.created_orders if o["side"] == "buy"][0]["average"]
        flat_stop = risk_manager.stop_loss_price(entry_price)
        self.assertNotAlmostEqual(stop_orders[0]["triggerPrice"], flat_stop, places=2)

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
            "main.detect_reversal_patterns", side_effect=always_bearish
        ):
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "entry_blocked_by_pattern")
        self.assertEqual(exchange.created_orders, [])

    def test_pattern_filter_off_ignores_pattern_detection(self):
        candles = make_candles(200, start_price=10000, step=10)  # uptrend -> signal 1
        exchange = FakeExchange(candles, free={"USDT": 1000.0})

        with patch("main.USE_PATTERN_FILTER", False), patch("main.detect_reversal_patterns") as mock_detect:
            result = main.run_trading_cycle(exchange)

        mock_detect.assert_not_called()
        self.assertEqual(result["action"], "buy")

    def test_blocks_a_new_entry_when_the_daily_loss_limit_is_hit(self):
        candles = make_candles(200, start_price=10000, step=10)  # uptrend -> signal 1
        exchange = FakeExchange(candles, free={"USDT": 1000.0})

        # A "starting capital" far above today's actual equity means
        # today's realized loss already blows past MAX_DAILY_LOSS_PCT.
        with patch("main.load_or_init_starting_capital", return_value=1_000_000.0):
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "entry_blocked_by_daily_loss_limit")
        self.assertEqual(exchange.created_orders, [])

    def test_blocks_a_new_entry_when_the_weekly_loss_limit_is_hit(self):
        candles = make_candles(200, start_price=10000, step=10)  # uptrend -> signal 1
        exchange = FakeExchange(candles, free={"USDT": 1000.0})

        with patch("main.weekly_loss_state.load_or_init_starting_capital", return_value=1_000_000.0):
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "entry_blocked_by_weekly_loss_limit")
        self.assertEqual(exchange.created_orders, [])

    def test_blocks_a_new_entry_when_max_consecutive_losses_is_hit(self):
        candles = make_candles(200, start_price=10000, step=10)  # uptrend -> signal 1
        exchange = FakeExchange(candles, free={"USDT": 1000.0})
        losing_streak = make_losing_streak(MAX_CONSECUTIVE_LOSSES)

        with patch("main.read_journal", return_value=losing_streak):
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "entry_blocked_by_consecutive_losses")
        self.assertEqual(exchange.created_orders, [])

    def test_does_not_block_when_the_losing_streak_is_shorter_than_the_max(self):
        candles = make_candles(200, start_price=10000, step=10)  # uptrend -> signal 1
        exchange = FakeExchange(candles, free={"USDT": 1000.0})
        short_streak = make_losing_streak(MAX_CONSECUTIVE_LOSSES - 1)

        with patch("main.read_journal", return_value=short_streak):
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "buy")


class EmaAuditFieldsTests(unittest.TestCase):
    """Every action the EMA cycle can take must come with a `reason`
    that explains it, plus the concrete numbers (stop/size/risk_pct or
    the loss-limit/streak value that tripped) an audit needs -- not
    just the bare action string. See main._ema_action_reason()."""

    def test_buy_reason_reports_the_stop_and_risk_used(self):
        candles = make_candles(200, start_price=10000, step=10)  # uptrend -> signal 1
        exchange = FakeExchange(candles, free={"USDT": 1000.0})

        result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "buy")
        self.assertIn("flat_stop_loss_pct", result["reason"])
        self.assertEqual(result["stop_source"], "flat_stop_loss_pct")
        self.assertAlmostEqual(result["stop_price"], risk_manager.stop_loss_price(result["price"]), places=0)
        self.assertGreater(result["size"], 0)
        self.assertEqual(result["risk_pct"], 1.0)

    def test_daily_loss_block_reason_reports_the_actual_loss_pct(self):
        candles = make_candles(200, start_price=10000, step=10)
        exchange = FakeExchange(candles, free={"USDT": 1000.0})

        with patch("main.load_or_init_starting_capital", return_value=1_000_000.0):
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "entry_blocked_by_daily_loss_limit")
        self.assertIsNotNone(result["daily_loss_pct"])
        self.assertIn(f"{result['daily_loss_pct']:.2f}%", result["reason"])
        self.assertIsNone(result["stop_price"])
        self.assertIsNone(result["size"])

    def test_consecutive_losses_block_reason_reports_the_streak_length(self):
        candles = make_candles(200, start_price=10000, step=10)
        exchange = FakeExchange(candles, free={"USDT": 1000.0})
        losing_streak = make_losing_streak(MAX_CONSECUTIVE_LOSSES)

        with patch("main.read_journal", return_value=losing_streak):
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "entry_blocked_by_consecutive_losses")
        self.assertEqual(result["consecutive_losses_count"], MAX_CONSECUTIVE_LOSSES)
        self.assertIn(str(MAX_CONSECUTIVE_LOSSES), result["reason"])

    def test_hold_reason_differs_between_flat_and_in_position(self):
        flat_candles = make_candles(200, start_price=10000, step=-10)  # downtrend -> signal 0, flat
        result = main.run_trading_cycle(FakeExchange(flat_candles, free={"USDT": 1000.0}))
        self.assertEqual(result["action"], "hold")
        self.assertIn("no EMA entry signal", result["reason"])


class NotifyWiringTests(unittest.TestCase):
    """run_trading_cycle() -- not each individual cycle function -- is
    where notify() gets called, so this covers both engines' actions
    from one place: "hold" (the expected common case, every cycle)
    must stay silent, anything else should notify."""

    def setUp(self):
        _stub_disk_touching_side_effects(self)

    def test_a_hold_does_not_notify(self):
        candles = make_candles(200, start_price=10000, step=0)  # flat -> no crossover either way
        exchange = FakeExchange(candles, free={"USDT": 1000.0})

        with patch("main.notify") as mock_notify:
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "hold")
        mock_notify.assert_not_called()

    def test_a_buy_notifies(self):
        candles = make_candles(200, start_price=10000, step=10)  # uptrend -> signal 1
        exchange = FakeExchange(candles, free={"USDT": 1000.0})

        with patch("main.notify") as mock_notify:
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "buy")
        mock_notify.assert_called_once()
        self.assertIn("buy", mock_notify.call_args[0][0])

    def test_syncs_the_trade_journal_every_cycle_including_a_hold(self):
        # Binance's own trade history should stay current regardless of
        # what this cycle itself decided -- a manual trade or a fill
        # from a previous cycle's order still belongs in the journal.
        candles = make_candles(200, start_price=10000, step=0)  # flat -> hold
        exchange = FakeExchange(candles, free={"USDT": 1000.0})

        with patch("main.record_trades") as mock_record:
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "hold")
        mock_record.assert_called_once_with(exchange, SYMBOL)

    def test_a_broken_trade_journal_does_not_fail_the_cycle(self):
        candles = make_candles(200, start_price=10000, step=10)  # uptrend -> signal 1
        exchange = FakeExchange(candles, free={"USDT": 1000.0})

        with patch("main.record_trades", side_effect=RuntimeError("disk full")):
            result = main.run_trading_cycle(exchange)  # must not raise

        self.assertEqual(result["action"], "buy")

    def test_snapshots_the_balance_every_cycle_including_a_hold(self):
        candles = make_candles(200, start_price=10000, step=0)  # flat -> hold
        exchange = FakeExchange(candles, free={"USDT": 1000.0})

        with patch("main.record_balance") as mock_record:
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "hold")
        mock_record.assert_called_once_with(exchange)

    def test_a_broken_balance_snapshot_does_not_fail_the_cycle(self):
        candles = make_candles(200, start_price=10000, step=10)  # uptrend -> signal 1
        exchange = FakeExchange(candles, free={"USDT": 1000.0})

        with patch("main.record_balance", side_effect=RuntimeError("disk full")):
            result = main.run_trading_cycle(exchange)  # must not raise

        self.assertEqual(result["action"], "buy")

    def test_records_a_heartbeat_every_cycle_including_a_hold(self):
        candles = make_candles(200, start_price=10000, step=0)  # flat -> hold
        exchange = FakeExchange(candles, free={"USDT": 1000.0})

        with patch("main.record_heartbeat") as mock_heartbeat:
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "hold")
        mock_heartbeat.assert_called_once_with()

    def test_a_broken_heartbeat_does_not_fail_the_cycle(self):
        candles = make_candles(200, start_price=10000, step=10)  # uptrend -> signal 1
        exchange = FakeExchange(candles, free={"USDT": 1000.0})

        with patch("main.record_heartbeat", side_effect=RuntimeError("disk full")):
            result = main.run_trading_cycle(exchange)  # must not raise

        self.assertEqual(result["action"], "buy")


def make_history_df(n: int, start_price: float = 10000.0, step: float = 10.0) -> pd.DataFrame:
    """A plausible-looking OHLCV history DataFrame, standing in for
    what fetch_ohlcv_history would return. build_context() itself is
    mocked in the Setup Engine tests below, so most of them don't care
    about this data's actual price shape — it just has to be non-empty
    with a real DatetimeIndex so build_timeframe_set() (not mocked)
    doesn't choke. The one exception: the bearish-pattern exit check in
    _run_setup_engine_cycle runs patterns.detect_reversal_patterns() on
    this same history directly, unmocked — a monotonic trend (the
    default `step`) has no interior swings and so never confirms a
    pattern, which is why every other test here can ignore this and
    only the bearish-pattern test below supplies its own shaped history."""
    rows = make_candles(n, start_price, step)
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df.set_index("timestamp")


class DummyContextExchange:
    """Stands in for get_public_data_exchange()'s return value. Only
    parse8601() is actually called on it in _run_setup_engine_cycle —
    fetch_ohlcv_history itself is mocked in these tests, so nothing
    else on this object is ever touched."""

    def parse8601(self, iso_string):
        return 0


def make_snapshot(
    *,
    bias_direction=None,
    no_trade=False,
    market_state=None,
    setups=None,
    invalidation_level=9000.0,
):
    """A minimal but fully-populated ContextSnapshot for testing how
    main.py reacts to it — real setup-detection correctness lives in
    test_context_setups.py, real end-to-end context building in
    test_context_engine.py. Defaults describe a clean bullish context."""
    from context_engine.schema import (
        Alignment,
        Bias,
        BiasHypothesis,
        ContextScore,
        ContextSnapshot,
        DataQuality,
        Direction,
        Invalidation,
        LiquidityState,
        MarketState,
        Phase,
        Regime,
        RegimeKind,
        RangeState,
        SessionState,
        VolatilityRegime,
        VolatilityState,
        Zone,
    )

    bias_direction = bias_direction or Bias.BULLISH
    market_state = market_state or MarketState.TREND_UP
    setups = setups or []

    return ContextSnapshot(
        timestamp="2024-01-09T07:00:00+00:00",
        asset=SYMBOL,
        version="test",
        data_quality=DataQuality(valid=True, degraded=False, issues=[]),
        regime=Regime(primary=RegimeKind.TRENDING_UP, volatility=VolatilityRegime.NORMAL, phase=Phase.PULLBACK),
        multi_timeframe={},
        alignment=Alignment.STRONG_ALIGNMENT,
        structure={},
        liquidity=LiquidityState(),
        volatility=VolatilityState(
            atr=100.0, atr_percent=1.0, regime=VolatilityRegime.NORMAL, expansion=False, contraction=False, percentile=50.0
        ),
        range=RangeState(name="daily", high=12000.0, low=9000.0, position_percent=50.0, zone=Zone.EQUILIBRIUM),
        sessions=SessionState(
            current="LONDON", high=None, low=None, range=None, previous=None, previous_high=None, previous_low=None
        ),
        events=[],
        bias=BiasHypothesis(direction=bias_direction, confidence=0.8, reasons=["test"], invalidations=["test"]),
        context_score=ContextScore(total=5.0, label="BULLISH", weights_version="test", components=[]),
        market_state=market_state,
        previous_market_state=None,
        preferred_direction=Direction.LONG if bias_direction in (Bias.BULLISH, Bias.STRONG_BULLISH) else Direction.NONE,
        setups=setups,
        preferred_setups=[s.name.value for s in setups],
        avoid=[],
        no_trade=no_trade,
        invalidation=Invalidation(type="CLOSE_BELOW", level=invalidation_level, detail="test"),
        risk={},
    )


class SetupEngineTradingCycleTests(unittest.TestCase):
    """main.py's Setup Engine path (config.USE_SETUP_ENGINE=True).
    Patches context_engine.engine.build_context directly rather than
    feeding it real market data through the full pipeline — the
    detector's own correctness is test_context_setups.py's job; this
    is only about whether main.py reacts to a snapshot correctly."""

    def setUp(self):
        _stub_disk_touching_side_effects(self)

    def _patches(self, snapshot):
        return (
            patch("main.USE_SETUP_ENGINE", True),
            patch("data_fetcher.get_public_data_exchange", return_value=DummyContextExchange()),
            patch("data_fetcher.fetch_ohlcv_history", return_value=make_history_df(200)),
            patch("context_engine.engine.build_context", return_value=snapshot),
        )

    def test_buys_on_a_confirmed_long_setup_using_its_invalidation_as_stop(self):
        from context_engine.schema import Direction, Invalidation, Setup, SetupName

        setup = Setup(
            name=SetupName.LIQUIDITY_SWEEP_RECLAIM,
            direction=Direction.LONG,
            reasons=["fake"],
            invalidation=Invalidation(type="CLOSE_BELOW", level=9000.0, detail="fake"),
        )
        snapshot = make_snapshot(setups=[setup], invalidation_level=9000.0)
        exchange = FakeExchange(make_candles(5, 10000, 0), free={"USDT": 1000.0})

        p1, p2, p3, p4 = self._patches(snapshot)
        with p1, p2, p3, p4:
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "buy")
        stop_orders = [o for o in exchange.created_orders if o["side"] == "sell"]
        self.assertEqual(len(stop_orders), 1)
        self.assertEqual(stop_orders[0]["triggerPrice"], 9000.0)

    def test_blocks_a_new_entry_when_the_daily_loss_limit_is_hit(self):
        from context_engine.schema import Direction, Invalidation, Setup, SetupName

        setup = Setup(
            name=SetupName.LIQUIDITY_SWEEP_RECLAIM,
            direction=Direction.LONG,
            reasons=["fake"],
            invalidation=Invalidation(type="CLOSE_BELOW", level=9000.0, detail="fake"),
        )
        snapshot = make_snapshot(setups=[setup], invalidation_level=9000.0)
        exchange = FakeExchange(make_candles(5, 10000, 0), free={"USDT": 1000.0})

        p1, p2, p3, p4 = self._patches(snapshot)
        with p1, p2, p3, p4, patch("main.load_or_init_starting_capital", return_value=1_000_000.0):
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "entry_blocked_by_daily_loss_limit")
        self.assertEqual(exchange.created_orders, [])

    def test_blocks_a_new_entry_when_the_weekly_loss_limit_is_hit(self):
        from context_engine.schema import Direction, Invalidation, Setup, SetupName

        setup = Setup(
            name=SetupName.LIQUIDITY_SWEEP_RECLAIM,
            direction=Direction.LONG,
            reasons=["fake"],
            invalidation=Invalidation(type="CLOSE_BELOW", level=9000.0, detail="fake"),
        )
        snapshot = make_snapshot(setups=[setup], invalidation_level=9000.0)
        exchange = FakeExchange(make_candles(5, 10000, 0), free={"USDT": 1000.0})

        p1, p2, p3, p4 = self._patches(snapshot)
        with p1, p2, p3, p4, patch("main.weekly_loss_state.load_or_init_starting_capital", return_value=1_000_000.0):
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "entry_blocked_by_weekly_loss_limit")
        self.assertEqual(exchange.created_orders, [])

    def test_blocks_a_new_entry_when_max_consecutive_losses_is_hit(self):
        from context_engine.schema import Direction, Invalidation, Setup, SetupName

        setup = Setup(
            name=SetupName.LIQUIDITY_SWEEP_RECLAIM,
            direction=Direction.LONG,
            reasons=["fake"],
            invalidation=Invalidation(type="CLOSE_BELOW", level=9000.0, detail="fake"),
        )
        snapshot = make_snapshot(setups=[setup], invalidation_level=9000.0)
        exchange = FakeExchange(make_candles(5, 10000, 0), free={"USDT": 1000.0})
        losing_streak = make_losing_streak(MAX_CONSECUTIVE_LOSSES)

        p1, p2, p3, p4 = self._patches(snapshot)
        with p1, p2, p3, p4, patch("main.read_journal", return_value=losing_streak):
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "entry_blocked_by_consecutive_losses")
        self.assertEqual(exchange.created_orders, [])

    def test_ignores_a_short_setup_long_only_bot(self):
        from context_engine.schema import Direction, Invalidation, Setup, SetupName

        short_setup = Setup(
            name=SetupName.LIQUIDITY_SWEEP_RECLAIM,
            direction=Direction.SHORT,
            reasons=["fake"],
            invalidation=Invalidation(type="CLOSE_ABOVE", level=11000.0, detail="fake"),
        )
        snapshot = make_snapshot(setups=[short_setup])
        exchange = FakeExchange(make_candles(5, 10000, 0), free={"USDT": 1000.0})

        p1, p2, p3, p4 = self._patches(snapshot)
        with p1, p2, p3, p4:
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "hold")
        self.assertEqual(exchange.created_orders, [])

    def test_no_trade_context_closes_an_open_position(self):
        from context_engine.schema import Bias

        base = SYMBOL.split("/")[0]
        stale_stop = {"id": "stop-1", "side": "sell", "amount": 1.0, "triggerPrice": 9000.0}
        exchange = FakeExchange(make_candles(5, 10000, 0), locked={base: 1.0}, open_orders=[stale_stop])
        snapshot = make_snapshot(bias_direction=Bias.NEUTRAL, no_trade=True)

        p1, p2, p3, p4 = self._patches(snapshot)
        with p1, p2, p3, p4:
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "sell")
        self.assertEqual(exchange.cancelled_ids, ["stop-1"])

    def test_bias_flip_closes_an_open_position_even_without_no_trade(self):
        from context_engine.schema import Bias

        base = SYMBOL.split("/")[0]
        stale_stop = {"id": "stop-1", "side": "sell", "amount": 1.0, "triggerPrice": 9000.0}
        exchange = FakeExchange(make_candles(5, 10000, 0), locked={base: 1.0}, open_orders=[stale_stop])
        snapshot = make_snapshot(bias_direction=Bias.BEARISH, no_trade=False)

        p1, p2, p3, p4 = self._patches(snapshot)
        with p1, p2, p3, p4:
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "sell")

    def test_bearish_pattern_closes_an_open_position_even_with_bullish_bias(self):
        # Unlike test_bias_flip above, bias stays BULLISH and no_trade
        # stays False on the mocked snapshot here -- the only reason
        # this should sell is patterns.detect_reversal_patterns()
        # running for real on the (unmocked) context history.
        from test_patterns import double_top_closes, make_ohlc

        base = SYMBOL.split("/")[0]
        stale_stop = {"id": "stop-1", "side": "sell", "amount": 1.0, "triggerPrice": 9000.0}
        exchange = FakeExchange(make_candles(5, 10000, 0), locked={base: 1.0}, open_orders=[stale_stop])
        history = make_ohlc(double_top_closes())  # confirms its bearish break near its own end
        snapshot = make_snapshot()  # default: bullish bias, no_trade=False, no setups

        with patch("main.USE_SETUP_ENGINE", True), patch(
            "data_fetcher.get_public_data_exchange", return_value=DummyContextExchange()
        ), patch("data_fetcher.fetch_ohlcv_history", return_value=history), patch(
            "context_engine.engine.build_context", return_value=snapshot
        ):
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "sell")
        self.assertEqual(exchange.cancelled_ids, ["stop-1"])

    def test_self_heals_a_missing_stop_using_the_contexts_invalidation(self):
        base = SYMBOL.split("/")[0]
        exchange = FakeExchange(make_candles(5, 10000, 0), free={base: 1.0})  # in position, no open stop
        snapshot = make_snapshot(invalidation_level=9200.0)

        p1, p2, p3, p4 = self._patches(snapshot)
        with p1, p2, p3, p4:
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "stop_loss_replaced")
        stop_orders = [o for o in exchange.created_orders if o["side"] == "sell"]
        self.assertEqual(len(stop_orders), 1)
        self.assertEqual(stop_orders[0]["triggerPrice"], 9200.0)

    def test_buy_reason_reports_the_invalidation_stop_and_risk_used(self):
        from context_engine.schema import Direction, Invalidation, Setup, SetupName

        setup = Setup(
            name=SetupName.LIQUIDITY_SWEEP_RECLAIM,
            direction=Direction.LONG,
            reasons=["fake"],
            invalidation=Invalidation(type="CLOSE_BELOW", level=9000.0, detail="fake"),
        )
        snapshot = make_snapshot(setups=[setup], invalidation_level=9000.0)
        exchange = FakeExchange(make_candles(5, 10000, 0), free={"USDT": 1000.0})

        p1, p2, p3, p4 = self._patches(snapshot)
        with p1, p2, p3, p4:
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "buy")
        self.assertEqual(result["stop_source"], "setup_invalidation_level")
        self.assertEqual(result["stop_price"], 9000.0)
        self.assertIn("setup_invalidation_level", result["reason"])
        self.assertIn("9000.0", result["reason"])
        self.assertEqual(result["risk_pct"], 1.0)

    def test_exit_reason_distinguishes_no_trade_bias_flip_and_bearish_pattern(self):
        from context_engine.schema import Bias

        base = SYMBOL.split("/")[0]

        def sell_with(snapshot):
            stale_stop = {"id": "stop-1", "side": "sell", "amount": 1.0, "triggerPrice": 9000.0}
            exchange = FakeExchange(make_candles(5, 10000, 0), locked={base: 1.0}, open_orders=[stale_stop])
            p1, p2, p3, p4 = self._patches(snapshot)
            with p1, p2, p3, p4:
                return main.run_trading_cycle(exchange)

        no_trade_result = sell_with(make_snapshot(bias_direction=Bias.NEUTRAL, no_trade=True))
        self.assertEqual(no_trade_result["action"], "sell")
        self.assertIn("no_trade", no_trade_result["reason"])

        bias_flip_result = sell_with(make_snapshot(bias_direction=Bias.BEARISH, no_trade=False))
        self.assertEqual(bias_flip_result["action"], "sell")
        self.assertIn("bias", bias_flip_result["reason"])

    def test_stop_loss_replaced_reason_reports_the_source_and_price(self):
        base = SYMBOL.split("/")[0]
        exchange = FakeExchange(make_candles(5, 10000, 0), free={base: 1.0})
        snapshot = make_snapshot(invalidation_level=9200.0)

        p1, p2, p3, p4 = self._patches(snapshot)
        with p1, p2, p3, p4:
            result = main.run_trading_cycle(exchange)

        self.assertEqual(result["action"], "stop_loss_replaced")
        self.assertEqual(result["stop_source"], "setup_invalidation_level")
        self.assertIn("9200.0", result["reason"])


class CheckConnectionTests(unittest.TestCase):
    """`python main.py`'s connectivity check — specifically the
    listed-market guard: Testnet typically carries far fewer pairs than
    the real exchange this repo's backtests actually ran against, so
    whether config.SYMBOL is even tradeable here is a real open
    question this check exists to answer unambiguously instead of
    leaving it to whatever exception the OHLCV fetch happens to raise."""

    def test_exits_with_a_clear_message_when_symbol_is_not_listed(self):
        class FakeMarketsExchange:
            def load_markets(self):
                return {"BTC/USDT": {}, "ETH/USDT": {}}

        with patch("main.get_exchange", return_value=FakeMarketsExchange()), patch("main.BINANCE_API_KEY", ""):
            with self.assertRaises(SystemExit) as ctx:
                main.check_connection()

        self.assertEqual(ctx.exception.code, 1)

    def test_proceeds_past_the_guard_when_symbol_is_listed(self):
        candles = make_candles(10, start_price=4000, step=0)

        class FakeMarketsExchange:
            def load_markets(self):
                return {SYMBOL: {}}

            def fetch_ohlcv(self, symbol, timeframe=None, limit=None, since=None):
                return candles

        with patch("main.get_exchange", return_value=FakeMarketsExchange()), patch("main.BINANCE_API_KEY", ""):
            main.check_connection()  # must not raise/exit


class MainEntrypointTests(unittest.TestCase):
    """`main()`'s --trade dispatch: the refuse-if-not-testnet guard and
    the try/except around run_trading_cycle() that turns an unhandled
    exception into a logged failure + nonzero exit instead of a bare
    traceback -- the only thing that lets a cron/systemd schedule
    actually notice a failed run. _configure_logging() is mocked out in
    every test here so running the suite never creates a real logs/
    directory as a side effect."""

    def test_refuses_to_trade_when_testnet_is_off(self):
        with patch("main.USE_TESTNET", False), patch("main._configure_logging"), patch(
            "main.run_trading_cycle"
        ) as mock_cycle, patch("sys.argv", ["main.py", "--trade"]):
            with self.assertRaises(SystemExit) as ctx:
                main.main()

        self.assertEqual(ctx.exception.code, 1)
        mock_cycle.assert_not_called()

    def test_exits_nonzero_instead_of_propagating_when_the_cycle_raises(self):
        with patch("main.USE_TESTNET", True), patch("main._configure_logging"), patch(
            "main.run_trading_cycle", side_effect=RuntimeError("boom")
        ), patch("sys.argv", ["main.py", "--trade"]):
            with self.assertRaises(SystemExit) as ctx:
                main.main()

        self.assertEqual(ctx.exception.code, 1)

    def test_notifies_when_the_cycle_raises(self):
        with patch("main.USE_TESTNET", True), patch("main._configure_logging"), patch(
            "main.run_trading_cycle", side_effect=RuntimeError("boom")
        ), patch("sys.argv", ["main.py", "--trade"]), patch("main.notify") as mock_notify:
            with self.assertRaises(SystemExit):
                main.main()

        mock_notify.assert_called_once()
        self.assertIn("FAILED", mock_notify.call_args[0][0])

    def test_runs_the_cycle_normally_when_testnet_is_on(self):
        with patch("main.USE_TESTNET", True), patch("main._configure_logging"), patch(
            "main.run_trading_cycle"
        ) as mock_cycle, patch("sys.argv", ["main.py", "--trade"]):
            main.main()

        mock_cycle.assert_called_once()


if __name__ == "__main__":
    unittest.main()
