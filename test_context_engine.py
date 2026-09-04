"""Tests for the context engine orchestrator.

The centrepiece is `LookAheadTests`: building the context at time T
must give the identical answer whether or not the candles after T are
present in the input. If that ever fails, every backtest built on this
engine is measuring knowledge the strategy would not have had.

The rest cover the contract shape, the no-trade conditions, and the
LLM boundary.

    python -m unittest test_context_engine -v
"""
import json
import unittest

import numpy as np
import pandas as pd

from context_engine.engine import build_context
from context_engine.llm_interface import (
    LLMOutputError,
    build_llm_input,
    parse_llm_output,
)
from context_engine.params import CONTEXT_ENGINE_VERSION, WEIGHTS_VERSION, ScoreWeights
from context_engine.schema import (
    Alignment,
    Bias,
    Direction,
    MarketEvent,
    MarketState,
    Trend,
    VolatilityRegime,
)
from context_engine.scoring import label_for, score_context
from context_engine.timeframes import build_timeframe_set
from context_engine.validation import DataValidationError

CONTRACT_KEYS = {
    "timestamp",
    "asset",
    "version",
    "data_quality",
    "regime",
    "multi_timeframe",
    "alignment",
    "structure",
    "liquidity",
    "volatility",
    "range",
    "sessions",
    "events",
    "bias",
    "context_score",
    "market_state",
    "preferred_direction",
    "preferred_setups",
    "avoid",
    "no_trade",
    "invalidation",
    "risk",
}


def synthetic_hourly(periods=24 * 60, drift=0.0, noise=0.4, seed=11, start="2024-01-01"):
    """Deterministic random-walk candles. A fixed seed keeps every
    assertion reproducible."""
    index = pd.date_range(start=start, periods=periods, freq="1h", tz="UTC")
    rng = np.random.default_rng(seed)
    closes = 100 + np.cumsum(rng.normal(drift, noise, periods))
    opens = np.concatenate([[closes[0]], closes[:-1]])
    spread = np.abs(rng.normal(0, 0.3, periods)) + 0.2
    return pd.DataFrame(
        {
            "open": opens,
            "high": np.maximum(opens, closes) + spread,
            "low": np.minimum(opens, closes) - spread,
            "close": closes,
            "volume": rng.uniform(5, 15, periods),
        },
        index=pd.DatetimeIndex(index, name="timestamp"),
    )


def trending_hourly(days=139, up_days=3, down_days=1, seed=7, start="2024-01-01"):
    """An uptrend that pulls back on a *daily* scale.

    Two properties this fixture has to get right to be a fair test:

    - Pullbacks long enough to survive resampling. A trend that only
      retraces within the hour is perfectly monotonic on the daily
      chart, so it has no swing pivots and therefore no structure at
      all. Three days up, one day down leaves a visible daily low.

    - Bar sizes that do not step. The down leg moves faster than the
      up leg, so without noise the ATR jumps every fourth day and the
      volatility engine correctly reports EXTREME. Small seeded noise
      keeps the distribution continuous, and `days` is chosen so the
      series ends mid-impulse rather than inside a pullback.
    """
    periods = days * 24
    index = pd.date_range(start=start, periods=periods, freq="1h", tz="UTC")
    cycle = up_days + down_days
    rng = np.random.default_rng(seed)

    closes = []
    price = 100.0
    for i in range(periods):
        rising = ((i // 24) % cycle) < up_days
        price += (0.15 if rising else -0.30) + rng.normal(0, 0.05)
        closes.append(price)

    closes = np.array(closes)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    spread = np.abs(rng.normal(0, 0.05, periods)) + 0.05
    return pd.DataFrame(
        {
            "open": opens,
            "high": np.maximum(opens, closes) + spread,
            "low": np.minimum(opens, closes) - spread,
            "close": closes,
            "volume": np.full(periods, 10.0),
        },
        index=pd.DatetimeIndex(index, name="timestamp"),
    )


class LookAheadTests(unittest.TestCase):
    """Master prompt section 30: never use information that was not
    available at decision time."""

    def test_context_is_identical_when_future_candles_are_removed(self):
        df = synthetic_hourly()
        frames = build_timeframe_set(df)
        as_of = frames["1h"].index[-200]

        # Same moment, two very different inputs: one carrying 200 bars
        # of future data, one truncated exactly at the decision point.
        with_future = build_context(frames, asset="BTC/USDT", as_of=as_of)
        truncated = build_timeframe_set(df[df.index <= as_of])
        without_future = build_context(truncated, asset="BTC/USDT", as_of=as_of)

        self.assertEqual(with_future.to_dict(), without_future.to_dict())

    def test_look_ahead_holds_at_several_points_in_time(self):
        df = synthetic_hourly(seed=5)
        frames = build_timeframe_set(df)

        for offset in (400, 300, 150, 80):
            as_of = frames["1h"].index[-offset]
            with self.subTest(offset=offset):
                truncated = build_timeframe_set(df[df.index <= as_of])
                self.assertEqual(
                    build_context(frames, asset="BTC/USDT", as_of=as_of).to_dict(),
                    build_context(truncated, asset="BTC/USDT", as_of=as_of).to_dict(),
                )

    def test_snapshot_timestamp_never_exceeds_as_of(self):
        frames = build_timeframe_set(synthetic_hourly())
        as_of = frames["1h"].index[-150]

        snapshot = build_context(frames, as_of=as_of)

        self.assertLessEqual(pd.Timestamp(snapshot.timestamp), as_of)


class ContractTests(unittest.TestCase):
    def test_snapshot_exposes_the_full_contract(self):
        frames = build_timeframe_set(synthetic_hourly())

        data = build_context(frames, asset="BTC/USDT").to_dict()

        self.assertEqual(set(data), CONTRACT_KEYS)
        self.assertEqual(data["version"], CONTEXT_ENGINE_VERSION)
        self.assertEqual(data["context_score"]["weights_version"], WEIGHTS_VERSION)

    def test_snapshot_is_json_serializable_without_a_custom_encoder(self):
        frames = build_timeframe_set(synthetic_hourly())

        data = build_context(frames, asset="BTC/USDT").to_dict()

        self.assertEqual(json.loads(json.dumps(data)), data)

    def test_building_twice_gives_the_same_answer(self):
        frames = build_timeframe_set(synthetic_hourly())

        first = build_context(frames, asset="BTC/USDT")
        second = build_context(frames, asset="BTC/USDT")

        self.assertEqual(first.to_dict(), second.to_dict())

    def test_preferred_setups_stays_empty_until_the_setup_engine_exists(self):
        frames = build_timeframe_set(synthetic_hourly())

        self.assertEqual(build_context(frames).preferred_setups, [])

    def test_risk_block_mirrors_the_configured_limits(self):
        frames = build_timeframe_set(synthetic_hourly())
        limits = {"max_risk_per_trade_pct": 0.5, "max_daily_loss_pct": 2.0}

        risk = build_context(frames, risk=limits).risk

        self.assertEqual(risk["max_risk_per_trade_pct"], 0.5)
        self.assertEqual(risk["max_daily_loss_pct"], 2.0)
        self.assertIn("high_volatility", risk)


class TrendingMarketTests(unittest.TestCase):
    def test_a_clean_uptrend_reads_bullish_and_allows_longs(self):
        frames = build_timeframe_set(trending_hourly())

        snapshot = build_context(frames, asset="BTC/USDT")

        self.assertIn(snapshot.bias.direction, (Bias.BULLISH, Bias.STRONG_BULLISH))
        self.assertEqual(snapshot.multi_timeframe["1d"], Trend.UP)
        self.assertEqual(snapshot.preferred_direction, Direction.LONG)
        self.assertFalse(snapshot.no_trade)

    def test_bullish_bias_is_invalidated_below_a_swing_low(self):
        frames = build_timeframe_set(trending_hourly())

        snapshot = build_context(frames)

        self.assertEqual(snapshot.invalidation.type, "CLOSE_BELOW")
        self.assertIsNotNone(snapshot.invalidation.level)
        self.assertLess(snapshot.invalidation.level, frames["1h"]["close"].iloc[-1])

    def test_every_reason_is_a_concrete_statement(self):
        frames = build_timeframe_set(trending_hourly())

        reasons = build_context(frames).bias.reasons

        self.assertTrue(reasons)
        self.assertTrue(all(isinstance(r, str) and r.strip() for r in reasons))


class NoTradeTests(unittest.TestCase):
    """Master prompt section 39: "no trade" is a valid answer, and it
    has to say why."""

    def test_an_imminent_high_impact_event_blocks_trading(self):
        frames = build_timeframe_set(trending_hourly())
        event = MarketEvent(
            event="CPI",
            importance="HIGH",
            time="13:30",
            currency="USD",
            minutes_to_event=10,
        )

        snapshot = build_context(frames, events=[event])

        self.assertTrue(snapshot.no_trade)
        self.assertEqual(snapshot.preferred_direction, Direction.NONE)
        self.assertEqual(snapshot.market_state, MarketState.NO_TRADE)
        self.assertTrue(any("CPI" in reason for reason in snapshot.avoid))

    def test_a_distant_event_does_not_block_trading(self):
        frames = build_timeframe_set(trending_hourly())
        event = MarketEvent(
            event="CPI",
            importance="HIGH",
            time="13:30",
            currency="USD",
            minutes_to_event=600,
        )

        snapshot = build_context(frames, events=[event])

        self.assertFalse(any("CPI" in reason for reason in snapshot.avoid))

    def test_choppy_market_refuses_to_pick_a_side(self):
        # A directionless random walk should not produce conviction.
        frames = build_timeframe_set(synthetic_hourly(drift=0.0, noise=0.6, seed=99))

        snapshot = build_context(frames)

        self.assertTrue(snapshot.no_trade)
        self.assertTrue(snapshot.avoid)

    def test_avoid_reasons_are_always_explained(self):
        frames = build_timeframe_set(synthetic_hourly(seed=42))

        snapshot = build_context(frames)

        if snapshot.no_trade:
            self.assertTrue(snapshot.avoid, "no_trade must always come with a reason")


class DataQualityTests(unittest.TestCase):
    def test_corrupt_candles_raise_in_strict_mode(self):
        df = synthetic_hourly(periods=300)
        df.iloc[100, df.columns.get_loc("high")] = df.iloc[100]["low"] - 5
        frames = {"1h": df}

        with self.assertRaises(DataValidationError):
            build_context(frames, asset="BTC/USDT")

    def test_corrupt_candles_are_flagged_when_degradation_is_allowed(self):
        df = synthetic_hourly(periods=300)
        df.iloc[100, df.columns.get_loc("high")] = df.iloc[100]["low"] - 5

        snapshot = build_context({"1h": df}, asset="BTC/USDT", strict=False)

        self.assertFalse(snapshot.data_quality.valid)
        self.assertTrue(snapshot.no_trade)
        self.assertTrue(any("data quality" in reason for reason in snapshot.avoid))

    def test_no_data_yields_a_usable_empty_snapshot(self):
        snapshot = build_context({}, asset="BTC/USDT", strict=False)

        self.assertTrue(snapshot.no_trade)
        self.assertEqual(snapshot.preferred_direction, Direction.NONE)
        self.assertEqual(set(snapshot.to_dict()), CONTRACT_KEYS)

    def test_short_history_still_produces_a_snapshot(self):
        frames = build_timeframe_set(synthetic_hourly(periods=80))

        snapshot = build_context(frames, asset="BTC/USDT")

        self.assertTrue(snapshot.data_quality.valid)
        self.assertTrue(snapshot.data_quality.degraded)


class ScoringTests(unittest.TestCase):
    def test_score_labels_follow_their_thresholds(self):
        self.assertEqual(label_for(8), "STRONG_BULLISH")
        self.assertEqual(label_for(4), "BULLISH")
        self.assertEqual(label_for(0), "NEUTRAL")
        self.assertEqual(label_for(-4), "BEARISH")
        self.assertEqual(label_for(-8), "STRONG_BEARISH")

    def test_weights_are_configurable(self):
        frames = build_timeframe_set(trending_hourly())
        doubled = ScoreWeights(daily_trend=4.0, h4_trend=4.0, h1_trend=2.0, weekly_trend=4.0)

        default_score = build_context(frames).context_score.total
        weighted_score = build_context(frames, weights=doubled).context_score.total

        self.assertNotEqual(default_score, weighted_score)

    def test_components_add_up_to_the_total(self):
        frames = build_timeframe_set(trending_hourly())

        score = build_context(frames).context_score
        summed = round(sum(c.contribution for c in score.components), 2)

        self.assertEqual(summed, score.total)

    def test_extreme_volatility_reduces_conviction_rather_than_flipping_it(self):
        class FakeVolatility:
            regime = VolatilityRegime.EXTREME

        bullish = {"1d": Trend.UP, "4h": Trend.UP}
        calm = score_context(bullish, None, None, None)
        wild = score_context(bullish, None, None, FakeVolatility())

        self.assertLess(wild.total, calm.total)
        self.assertGreater(wild.total, 0)  # still bullish, just less so


class AlignmentTests(unittest.TestCase):
    def test_conflicting_higher_timeframes_are_reported_as_conflict(self):
        from context_engine.bias import assess_alignment

        conflicted = {"1w": Trend.UP, "1d": Trend.DOWN, "4h": Trend.UP}

        self.assertEqual(assess_alignment(conflicted), Alignment.CONFLICT)

    def test_a_lower_timeframe_pullback_is_not_a_conflict(self):
        from context_engine.bias import assess_alignment

        # Daily and 4h agree; only the 15m disagrees, which is what a
        # pullback looks like and must not be flagged as conflict.
        pullback = {"1w": Trend.UP, "1d": Trend.UP, "4h": Trend.UP, "15m": Trend.DOWN}

        self.assertEqual(assess_alignment(pullback), Alignment.STRONG_ALIGNMENT)


class LLMInterfaceTests(unittest.TestCase):
    def test_input_carries_context_but_no_raw_candles(self):
        frames = build_timeframe_set(trending_hourly())
        snapshot = build_context(frames, asset="BTC/USDT")

        payload = build_llm_input(snapshot)

        self.assertIn("market_context", payload)
        self.assertIn("expected_output_schema", payload)
        self.assertNotIn("candles", payload)
        json.dumps(payload)  # must be serializable as-is

    def test_a_valid_reply_is_parsed(self):
        reply = json.dumps(
            {
                "interpretation": "Bullish daily with a pullback in progress.",
                "market_state": "PULLBACK",
                "preferred_direction": "LONG",
                "confidence": 0.7,
                "preferred_setups": ["PULLBACK"],
                "avoid": ["COUNTERTREND_SHORT"],
                "invalidation": ["4h close below 108900"],
                "contradictions": [],
            }
        )

        parsed = parse_llm_output(reply)

        self.assertEqual(parsed["preferred_direction"], "LONG")
        self.assertEqual(parsed["confidence"], 0.7)

    def test_prose_instead_of_json_is_rejected(self):
        with self.assertRaises(LLMOutputError):
            parse_llm_output("The market looks kind of bullish today.")

    def test_a_missing_field_is_rejected(self):
        with self.assertRaises(LLMOutputError):
            parse_llm_output(json.dumps({"interpretation": "x"}))

    def test_an_unknown_direction_is_rejected(self):
        reply = {
            "interpretation": "",
            "market_state": "RANGE",
            "preferred_direction": "MAYBE_LONG",
            "confidence": 0.5,
            "preferred_setups": [],
            "avoid": [],
            "invalidation": [],
            "contradictions": [],
        }

        with self.assertRaises(LLMOutputError):
            parse_llm_output(reply)

    def test_confidence_outside_zero_to_one_is_rejected(self):
        reply = {
            "interpretation": "",
            "market_state": "RANGE",
            "preferred_direction": "NONE",
            "confidence": 4.2,
            "preferred_setups": [],
            "avoid": [],
            "invalidation": [],
            "contradictions": [],
        }

        with self.assertRaises(LLMOutputError):
            parse_llm_output(reply)


if __name__ == "__main__":
    unittest.main()
