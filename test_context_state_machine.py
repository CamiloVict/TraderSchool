"""Tests for context_engine/state_machine.py (master prompt section
15/36): bounded transitions and the per-state setup registry.

Run with: python -m unittest test_context_state_machine -v
"""
import unittest

from context_engine.schema import MarketState, SetupName
from context_engine.state_machine import allowed_setup_names, next_state


class NextStateTests(unittest.TestCase):
    def test_first_snapshot_accepts_the_classified_state_directly(self):
        self.assertEqual(next_state(None, MarketState.TREND_UP), MarketState.TREND_UP)

    def test_staying_in_the_same_state_is_always_allowed(self):
        self.assertEqual(next_state(MarketState.RANGE, MarketState.RANGE), MarketState.RANGE)

    def test_a_listed_transition_is_accepted(self):
        self.assertEqual(
            next_state(MarketState.TREND_UP, MarketState.PULLBACK), MarketState.PULLBACK
        )

    def test_an_unlisted_transition_holds_the_previous_state(self):
        # TREND_UP -> FAILED_BREAKOUT is not a one-hop transition: a
        # single candle shouldn't flip a standing uptrend straight into
        # "the breakout just failed" without passing through anything
        # in between (master prompt section 38).
        self.assertEqual(
            next_state(MarketState.TREND_UP, MarketState.FAILED_BREAKOUT), MarketState.TREND_UP
        )

    def test_entering_a_risk_override_is_immediate(self):
        self.assertEqual(
            next_state(MarketState.TREND_UP, MarketState.HIGH_VOLATILITY),
            MarketState.HIGH_VOLATILITY,
        )
        self.assertEqual(
            next_state(MarketState.RANGE, MarketState.NO_TRADE), MarketState.NO_TRADE
        )

    def test_leaving_a_risk_override_is_immediate(self):
        # Once volatility normalizes or the no-trade conditions clear,
        # the classifier's read is trusted right away rather than
        # requiring a listed transition out of a state that was never
        # meant to be "held in".
        self.assertEqual(
            next_state(MarketState.HIGH_VOLATILITY, MarketState.TREND_UP), MarketState.TREND_UP
        )
        self.assertEqual(
            next_state(MarketState.NO_TRADE, MarketState.RANGE), MarketState.RANGE
        )


class AllowedSetupNamesTests(unittest.TestCase):
    def test_allowed_setup_is_returned_for_a_trading_state(self):
        self.assertIn(SetupName.LIQUIDITY_SWEEP_RECLAIM, allowed_setup_names(MarketState.TREND_UP))

    def test_forbidden_setup_is_excluded_even_if_never_listed_as_allowed(self):
        self.assertEqual(allowed_setup_names(MarketState.NO_TRADE), ())
        self.assertEqual(allowed_setup_names(MarketState.HIGH_VOLATILITY), ())


if __name__ == "__main__":
    unittest.main()
