"""Tests for context_engine/setups.py — the Setup Engine (master prompt
section 17). Only LIQUIDITY_SWEEP_RECLAIM exists so far.

Run with: python -m unittest test_context_setups -v
"""
import unittest

from context_engine.schema import (
    Bias,
    BreakKind,
    Direction,
    Invalidation,
    LiquidityEvent,
    LiquidityEventKind,
    LiquidityState,
    Phase,
    StructureState,
    SwingKind,
    Trend,
)
from context_engine.setups import detect_liquidity_sweep_reclaim, detect_setups

EXECUTION_TF = "1h"
INVALIDATION = Invalidation(type="CLOSE_BELOW", level=90.0, detail="test invalidation")


def make_structure(last_bos=None) -> StructureState:
    return StructureState(
        trend=Trend.UP,
        sequence=[],
        last_bos=last_bos,
        last_choch=None,
        phase=Phase.PULLBACK,
        last_swing_high=110.0,
        last_swing_low=90.0,
    )


def make_liquidity(kind=None, reclaimed=True, displacement=True, level_name="PDL", level=95.0):
    events = []
    if kind is not None:
        events.append(
            LiquidityEvent(
                kind=kind,
                level_name=level_name,
                level=level,
                occurred_at="2024-01-01T00:00:00+00:00",
                reclaimed=reclaimed,
                displacement=displacement,
            )
        )
    return LiquidityState(events=events, recent_event=kind)


class LiquiditySweepReclaimTests(unittest.TestCase):
    def test_confirms_long_when_every_leg_aligns(self):
        structures = {EXECUTION_TF: make_structure(last_bos=BreakKind.BULLISH_BOS)}
        liquidity = make_liquidity(kind=LiquidityEventKind.SWEEP_LOW)

        setup = detect_liquidity_sweep_reclaim(structures, liquidity, Bias.BULLISH, INVALIDATION, EXECUTION_TF)

        self.assertIsNotNone(setup)
        self.assertEqual(setup.direction, Direction.LONG)
        self.assertEqual(setup.invalidation, INVALIDATION)
        self.assertTrue(setup.reasons)

    def test_confirms_short_when_every_leg_aligns(self):
        structures = {EXECUTION_TF: make_structure(last_bos=BreakKind.BEARISH_BOS)}
        liquidity = make_liquidity(kind=LiquidityEventKind.SWEEP_HIGH, level_name="PDH", level=115.0)

        setup = detect_liquidity_sweep_reclaim(structures, liquidity, Bias.BEARISH, INVALIDATION, EXECUTION_TF)

        self.assertIsNotNone(setup)
        self.assertEqual(setup.direction, Direction.SHORT)

    def test_neutral_bias_never_fires(self):
        structures = {EXECUTION_TF: make_structure(last_bos=BreakKind.BULLISH_BOS)}
        liquidity = make_liquidity(kind=LiquidityEventKind.SWEEP_LOW)

        setup = detect_liquidity_sweep_reclaim(structures, liquidity, Bias.NEUTRAL, INVALIDATION, EXECUTION_TF)

        self.assertIsNone(setup)

    def test_unreclaimed_sweep_does_not_fire(self):
        structures = {EXECUTION_TF: make_structure(last_bos=BreakKind.BULLISH_BOS)}
        liquidity = make_liquidity(kind=LiquidityEventKind.SWEEP_LOW, reclaimed=False)

        setup = detect_liquidity_sweep_reclaim(structures, liquidity, Bias.BULLISH, INVALIDATION, EXECUTION_TF)

        self.assertIsNone(setup)

    def test_sweep_without_displacement_does_not_fire(self):
        structures = {EXECUTION_TF: make_structure(last_bos=BreakKind.BULLISH_BOS)}
        liquidity = make_liquidity(kind=LiquidityEventKind.SWEEP_LOW, displacement=False)

        setup = detect_liquidity_sweep_reclaim(structures, liquidity, Bias.BULLISH, INVALIDATION, EXECUTION_TF)

        self.assertIsNone(setup)

    def test_missing_confirming_bos_does_not_fire(self):
        structures = {EXECUTION_TF: make_structure(last_bos=None)}
        liquidity = make_liquidity(kind=LiquidityEventKind.SWEEP_LOW)

        setup = detect_liquidity_sweep_reclaim(structures, liquidity, Bias.BULLISH, INVALIDATION, EXECUTION_TF)

        self.assertIsNone(setup)

    def test_bos_in_the_wrong_direction_does_not_fire(self):
        """A bearish BOS on the execution timeframe doesn't confirm a
        long sweep-reclaim, even with everything else aligned."""
        structures = {EXECUTION_TF: make_structure(last_bos=BreakKind.BEARISH_BOS)}
        liquidity = make_liquidity(kind=LiquidityEventKind.SWEEP_LOW)

        setup = detect_liquidity_sweep_reclaim(structures, liquidity, Bias.BULLISH, INVALIDATION, EXECUTION_TF)

        self.assertIsNone(setup)

    def test_no_sweep_event_at_all_does_not_fire(self):
        structures = {EXECUTION_TF: make_structure(last_bos=BreakKind.BULLISH_BOS)}
        liquidity = make_liquidity(kind=None)

        setup = detect_liquidity_sweep_reclaim(structures, liquidity, Bias.BULLISH, INVALIDATION, EXECUTION_TF)

        self.assertIsNone(setup)

    def test_uses_the_matching_kind_event_not_just_the_latest(self):
        """recent_event on LiquidityState is whatever fired last,
        regardless of kind -- the detector must search `events` for one
        of the *right* kind rather than trusting recent_event blindly."""
        structures = {EXECUTION_TF: make_structure(last_bos=BreakKind.BULLISH_BOS)}
        sweep_low = LiquidityEvent(
            kind=LiquidityEventKind.SWEEP_LOW,
            level_name="PDL",
            level=95.0,
            occurred_at="2024-01-01T00:00:00+00:00",
            reclaimed=True,
            displacement=True,
        )
        unrelated_expansion = LiquidityEvent(
            kind=LiquidityEventKind.LIQUIDITY_EXPANSION,
            level_name="PWH",
            level=130.0,
            occurred_at="2024-01-02T00:00:00+00:00",
            reclaimed=False,
            displacement=True,
        )
        liquidity = LiquidityState(
            events=[sweep_low, unrelated_expansion], recent_event=unrelated_expansion.kind
        )

        setup = detect_liquidity_sweep_reclaim(structures, liquidity, Bias.BULLISH, INVALIDATION, EXECUTION_TF)

        self.assertIsNotNone(setup)


class DetectSetupsTests(unittest.TestCase):
    def test_returns_the_confirmed_setup(self):
        structures = {EXECUTION_TF: make_structure(last_bos=BreakKind.BULLISH_BOS)}
        liquidity = make_liquidity(kind=LiquidityEventKind.SWEEP_LOW)

        setups = detect_setups(structures, liquidity, Bias.BULLISH, INVALIDATION, EXECUTION_TF)

        self.assertEqual(len(setups), 1)

    def test_returns_nothing_when_no_setup_confirms(self):
        structures = {EXECUTION_TF: make_structure(last_bos=None)}
        liquidity = make_liquidity(kind=None)

        setups = detect_setups(structures, liquidity, Bias.NEUTRAL, INVALIDATION, EXECUTION_TF)

        self.assertEqual(setups, [])


if __name__ == "__main__":
    unittest.main()
