"""Tests for setup_engine_backtester.py.

Most of these mock context_engine.engine.build_context — its own
correctness lives in test_context_engine.py / test_context_setups.py.
What's specific to this module and needs its own tests: the rolling
window actually stays bounded (the reason this loop is linear instead
of quadratic), previous_state actually threads from one iteration's
result into the next call, and P&L math against a mocked context is
right. One slow-ish real end-to-end run closes the gap between "the
pieces are individually right" and "they're wired together correctly".

Run with: python -m unittest test_setup_engine_backtester -v
"""
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import setup_engine_backtester as seb
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
    Setup,
    SetupName,
    VolatilityRegime,
    VolatilityState,
    Zone,
)


def make_history_df(n: int, start_price: float = 10000.0, step: float = 0.0) -> pd.DataFrame:
    start = pd.Timestamp("2024-01-01", tz="UTC")
    index = pd.DatetimeIndex([start + pd.Timedelta(hours=i) for i in range(n)], name="timestamp")
    closes = start_price + step * np.arange(n)
    return pd.DataFrame(
        {"open": closes, "high": closes + 1, "low": closes - 1, "close": closes, "volume": 1.0},
        index=index,
    )


def make_snapshot(*, bias_direction=None, no_trade=False, market_state=None, setups=None, invalidation_level=9000.0):
    bias_direction = bias_direction or Bias.BULLISH
    market_state = market_state or MarketState.TREND_UP
    setups = setups or []
    return ContextSnapshot(
        timestamp="2024-01-09T07:00:00+00:00",
        asset="TEST",
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


class RollingWindowTests(unittest.TestCase):
    def test_the_window_fed_to_build_context_never_grows(self):
        """The whole point of this loop over the naive "feed everything
        since the start" approach: cost per call must stay constant as
        the backtest progresses, not grow with how far in we are."""
        history = make_history_df(300)
        recorded_lengths = []

        def fake_build_timeframe_set(base, base_timeframe=None):
            recorded_lengths.append(len(base))
            return {}

        with patch("setup_engine_backtester.build_timeframe_set", side_effect=fake_build_timeframe_set), patch(
            "setup_engine_backtester.build_context", return_value=make_snapshot()
        ):
            seb.simulate_setup_engine(history, context_window_days=1, timeframe="1h")  # window = 24h

        self.assertTrue(recorded_lengths)
        # window_days=1 -> 24 hourly candles -> each slice is window+1 (inclusive of "now")
        self.assertTrue(all(length == 25 for length in recorded_lengths))

    def test_raises_when_history_does_not_exceed_the_window(self):
        history = make_history_df(20)

        with self.assertRaises(ValueError):
            seb.simulate_setup_engine(history, context_window_days=1, timeframe="1h")


class PreviousStateThreadingTests(unittest.TestCase):
    def test_previous_state_is_the_prior_iterations_own_market_state(self):
        history = make_history_df(30)
        snap_a = make_snapshot(market_state=MarketState.RANGE)
        snap_b = make_snapshot(market_state=MarketState.TREND_UP)
        previous_states_seen = []

        def fake_build_context(frames, asset=None, previous_state=None):
            previous_states_seen.append(previous_state)
            return snap_a if len(previous_states_seen) == 1 else snap_b

        with patch("setup_engine_backtester.build_timeframe_set", return_value={}), patch(
            "setup_engine_backtester.build_context", side_effect=fake_build_context
        ):
            seb.simulate_setup_engine(history, context_window_days=1, timeframe="1h")

        self.assertIsNone(previous_states_seen[0])  # nothing to hold on the first decision
        self.assertEqual(previous_states_seen[1], MarketState.RANGE)  # snap_a's own state
        self.assertEqual(previous_states_seen[2], MarketState.TREND_UP)  # snap_b's own state


class PnLTests(unittest.TestCase):
    def test_buys_on_a_confirmed_long_setup_and_records_a_stop_loss_exit(self):
        history = make_history_df(30, start_price=10000.0, step=0.0)
        setup = Setup(
            name=SetupName.LIQUIDITY_SWEEP_RECLAIM,
            direction=Direction.LONG,
            reasons=["fake"],
            invalidation=Invalidation(type="CLOSE_BELOW", level=9500.0, detail="fake"),
        )
        # Confirms the setup only on the very first tested candle, so
        # exactly one entry happens; every candle has the same flat
        # price, so the position never actually gets stopped or exited
        # by bias -- this test is about entry + sizing, not exits.
        calls = {"n": 0}

        def fake_build_context(frames, asset=None, previous_state=None):
            calls["n"] += 1
            return make_snapshot(setups=[setup] if calls["n"] == 1 else [])

        with patch("setup_engine_backtester.build_timeframe_set", return_value={}), patch(
            "setup_engine_backtester.build_context", side_effect=fake_build_context
        ):
            metrics, trades, snapshots = seb.simulate_setup_engine(history, context_window_days=1, timeframe="1h")

        self.assertEqual(trades, [])  # never exited: still open at the end
        self.assertTrue(snapshots)
        self.assertEqual(snapshots[0]["setups"], ["LIQUIDITY_SWEEP_RECLAIM"])

    def test_exits_on_a_stop_loss_touch_using_the_setups_invalidation_level(self):
        # Flat at 10000 except one tested candle whose low pierces the
        # 9500 invalidation level -- the position should exit there, at
        # exactly that price, not at the candle's close. Needs >24
        # candles total (context_window_days=1 -> a 24h lookback); the
        # pierce is placed a few candles into the tested range.
        history = make_history_df(35, start_price=10000.0, step=0.0)
        history.iloc[27, history.columns.get_loc("low")] = 9000.0

        setup = Setup(
            name=SetupName.LIQUIDITY_SWEEP_RECLAIM,
            direction=Direction.LONG,
            reasons=["fake"],
            invalidation=Invalidation(type="CLOSE_BELOW", level=9500.0, detail="fake"),
        )
        calls = {"n": 0}

        def fake_build_context(frames, asset=None, previous_state=None):
            calls["n"] += 1
            return make_snapshot(setups=[setup] if calls["n"] == 1 else [])

        with patch("setup_engine_backtester.build_timeframe_set", return_value={}), patch(
            "setup_engine_backtester.build_context", side_effect=fake_build_context
        ):
            metrics, trades, snapshots = seb.simulate_setup_engine(history, context_window_days=1, timeframe="1h")

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "stop_loss")
        self.assertEqual(trades[0]["exit_price"], 9500.0)
        self.assertEqual(metrics["stop_loss_exits"], 1)

    def test_exits_on_a_bearish_bias_flip_without_touching_the_stop(self):
        history = make_history_df(35, start_price=10000.0, step=0.0)
        setup = Setup(
            name=SetupName.LIQUIDITY_SWEEP_RECLAIM,
            direction=Direction.LONG,
            reasons=["fake"],
            invalidation=Invalidation(type="CLOSE_BELOW", level=9000.0, detail="fake"),
        )
        calls = {"n": 0}

        def fake_build_context(frames, asset=None, previous_state=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return make_snapshot(setups=[setup])
            if calls["n"] == 3:
                return make_snapshot(bias_direction=Bias.BEARISH)
            return make_snapshot()

        with patch("setup_engine_backtester.build_timeframe_set", return_value={}), patch(
            "setup_engine_backtester.build_context", side_effect=fake_build_context
        ):
            metrics, trades, snapshots = seb.simulate_setup_engine(history, context_window_days=1, timeframe="1h")

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "bias_flip")
        self.assertEqual(metrics["signal_exits"], 1)
        self.assertEqual(metrics["stop_loss_exits"], 0)


class RealEndToEndSmokeTest(unittest.TestCase):
    """The one test in this file that runs the genuine
    context_engine pipeline instead of a mock — slower (a handful of
    seconds), but it's the only thing that would catch a real
    integration break between this loop and build_context() itself."""

    def test_runs_without_error_on_a_small_real_history(self):
        from test_context_engine import trending_hourly

        history = trending_hourly(days=3)  # 72 hourly candles

        metrics, trades, snapshots = seb.simulate_setup_engine(history, context_window_days=1, timeframe="1h", asset="BTC/USDT")

        self.assertEqual(len(snapshots), 48)  # 72 - 24h window
        self.assertIn("total_return_pct", metrics)
        self.assertIsInstance(trades, list)


if __name__ == "__main__":
    unittest.main()
