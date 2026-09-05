"""Tests for context_engine/setups.py — the Setup Engine (master prompt
section 17): LIQUIDITY_SWEEP_RECLAIM and CHART_PATTERN_REVERSAL.

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
    SetupName,
    StructureState,
    SwingKind,
    Trend,
)
from context_engine.setups import (
    detect_chart_pattern_reversal,
    detect_liquidity_sweep_reclaim,
    detect_setups,
)

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

    def test_can_return_both_setups_at_once_when_both_confirm(self):
        from test_patterns import double_bottom_closes, make_ohlc

        structures = {EXECUTION_TF: make_structure(last_bos=BreakKind.BULLISH_BOS)}
        liquidity = make_liquidity(kind=LiquidityEventKind.SWEEP_LOW)
        execution_df = make_ohlc(double_bottom_closes())

        setups = detect_setups(structures, liquidity, Bias.BULLISH, INVALIDATION, EXECUTION_TF, execution_df)

        names = {setup.name for setup in setups}
        self.assertEqual(len(setups), 2)
        self.assertIn(SetupName.LIQUIDITY_SWEEP_RECLAIM, names)
        self.assertIn(SetupName.CHART_PATTERN_REVERSAL, names)


class ChartPatternReversalTests(unittest.TestCase):
    """CHART_PATTERN_REVERSAL's whole point is that a pattern never
    fires alone -- these tests are built around that requirement, not
    just around patterns.py's own detection logic (already covered by
    test_patterns.py)."""

    def test_confirms_long_on_a_double_bottom_with_bullish_bias(self):
        from test_patterns import double_bottom_closes, make_ohlc

        structures = {EXECUTION_TF: make_structure()}
        execution_df = make_ohlc(double_bottom_closes())

        setup = detect_chart_pattern_reversal(
            structures, make_liquidity(kind=None), Bias.BULLISH, INVALIDATION, EXECUTION_TF, execution_df
        )

        self.assertIsNotNone(setup)
        self.assertEqual(setup.direction, Direction.LONG)
        self.assertEqual(setup.invalidation, INVALIDATION)

    def test_confirms_short_on_a_double_top_with_bearish_bias(self):
        from test_patterns import double_top_closes, make_ohlc

        structures = {EXECUTION_TF: make_structure()}
        execution_df = make_ohlc(double_top_closes())

        setup = detect_chart_pattern_reversal(
            structures, make_liquidity(kind=None), Bias.BEARISH, INVALIDATION, EXECUTION_TF, execution_df
        )

        self.assertIsNotNone(setup)
        self.assertEqual(setup.direction, Direction.SHORT)

    def test_a_confirmed_pattern_against_bias_does_not_fire(self):
        """The whole point: a bullish double-bottom with a bearish HTF
        bias is exactly the "isolated pattern" patterns.py's own
        docstring says is not sufficient evidence on its own."""
        from test_patterns import double_bottom_closes, make_ohlc

        structures = {EXECUTION_TF: make_structure()}
        execution_df = make_ohlc(double_bottom_closes())

        setup = detect_chart_pattern_reversal(
            structures, make_liquidity(kind=None), Bias.BEARISH, INVALIDATION, EXECUTION_TF, execution_df
        )

        self.assertIsNone(setup)

    def test_neutral_bias_never_fires(self):
        from test_patterns import double_bottom_closes, make_ohlc

        structures = {EXECUTION_TF: make_structure()}
        execution_df = make_ohlc(double_bottom_closes())

        setup = detect_chart_pattern_reversal(
            structures, make_liquidity(kind=None), Bias.NEUTRAL, INVALIDATION, EXECUTION_TF, execution_df
        )

        self.assertIsNone(setup)

    def test_no_execution_df_does_not_fire(self):
        structures = {EXECUTION_TF: make_structure()}

        setup = detect_chart_pattern_reversal(
            structures, make_liquidity(kind=None), Bias.BULLISH, INVALIDATION, EXECUTION_TF, None
        )

        self.assertIsNone(setup)

    def test_a_pattern_confirmed_too_long_ago_no_longer_fires(self):
        """Same staleness rule as the EMA veto (PATTERN_VETO_LOOKBACK)
        -- padding enough flat candles after the confirmed breakout
        pushes it outside the lookback window."""
        from patterns import PATTERN_VETO_LOOKBACK
        from test_patterns import double_bottom_closes, make_ohlc

        closes = double_bottom_closes() + [double_bottom_closes()[-1]] * (PATTERN_VETO_LOOKBACK + 5)
        structures = {EXECUTION_TF: make_structure()}
        execution_df = make_ohlc(closes)

        setup = detect_chart_pattern_reversal(
            structures, make_liquidity(kind=None), Bias.BULLISH, INVALIDATION, EXECUTION_TF, execution_df
        )

        self.assertIsNone(setup)

    def test_no_confirmed_pattern_does_not_fire(self):
        import numpy as np
        from test_patterns import make_ohlc

        structures = {EXECUTION_TF: make_structure()}
        execution_df = make_ohlc(np.linspace(100, 101, 40).tolist())  # flat drift, no pattern

        setup = detect_chart_pattern_reversal(
            structures, make_liquidity(kind=None), Bias.BULLISH, INVALIDATION, EXECUTION_TF, execution_df
        )

        self.assertIsNone(setup)


if __name__ == "__main__":
    unittest.main()
