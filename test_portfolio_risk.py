"""Tests for portfolio_risk.py's cross-asset risk cap.

Run with: python -m unittest test_portfolio_risk -v
"""
import unittest
from unittest.mock import patch

import portfolio_risk
from config import RISK_PER_TRADE_PCT


class FakeExchange:
    """Minimal stand-in for ccxt's exchange -- only what
    get_total_base_asset_balance() and fetch_ticker() touch."""

    def __init__(self, totals: dict, prices: dict, raise_on_ticker: bool = False):
        self._totals = totals
        self._prices = prices
        self._raise_on_ticker = raise_on_ticker

    def fetch_balance(self):
        return {"total": self._totals}

    def fetch_ticker(self, symbol):
        if self._raise_on_ticker:
            raise RuntimeError("market not listed on this exchange")
        return {"last": self._prices[symbol]}


class OtherBotHasOpenPositionTests(unittest.TestCase):
    def test_true_when_the_other_symbols_balance_is_worth_more_than_the_dust_threshold(self):
        exchange = FakeExchange(totals={"BTC": 0.01}, prices={"BTC/USDT": 50_000.0})

        self.assertTrue(portfolio_risk.other_bot_has_open_position(exchange, "PAXG/USDT"))

    def test_false_when_the_other_symbols_balance_is_dust(self):
        exchange = FakeExchange(totals={"BTC": 0.00001}, prices={"BTC/USDT": 50_000.0})

        self.assertFalse(portfolio_risk.other_bot_has_open_position(exchange, "PAXG/USDT"))

    def test_false_when_the_other_symbol_has_no_balance_at_all(self):
        exchange = FakeExchange(totals={}, prices={"BTC/USDT": 50_000.0})

        self.assertFalse(portfolio_risk.other_bot_has_open_position(exchange, "PAXG/USDT"))

    def test_checks_paxg_when_called_from_the_btc_side(self):
        exchange = FakeExchange(totals={"PAXG": 1.0}, prices={"PAXG/USDT": 3000.0})

        self.assertTrue(portfolio_risk.other_bot_has_open_position(exchange, "BTC/USDT"))

    def test_false_and_does_not_raise_when_the_other_markets_price_cant_be_fetched(self):
        # e.g. the other symbol isn't listed on this testnet -- a
        # diagnostic check failing must never surface as an error in
        # the calling cycle.
        exchange = FakeExchange(totals={"BTC": 0.01}, prices={}, raise_on_ticker=True)

        self.assertFalse(portfolio_risk.other_bot_has_open_position(exchange, "PAXG/USDT"))


class PortfolioRiskLimitHitTests(unittest.TestCase):
    def test_not_hit_when_the_other_bot_has_no_open_position(self):
        exchange = FakeExchange(totals={}, prices={})

        self.assertFalse(portfolio_risk.portfolio_risk_limit_hit(exchange, "PAXG/USDT"))

    def test_hit_when_the_other_bot_is_open_and_combined_risk_exceeds_the_cap(self):
        exchange = FakeExchange(totals={"BTC": 0.01}, prices={"BTC/USDT": 50_000.0})

        with patch("portfolio_risk.MAX_PORTFOLIO_RISK_PCT", RISK_PER_TRADE_PCT * 1.5):
            self.assertTrue(portfolio_risk.portfolio_risk_limit_hit(exchange, "PAXG/USDT"))

    def test_not_hit_when_the_other_bot_is_open_but_the_cap_is_wide_enough(self):
        exchange = FakeExchange(totals={"BTC": 0.01}, prices={"BTC/USDT": 50_000.0})

        with patch("portfolio_risk.MAX_PORTFOLIO_RISK_PCT", RISK_PER_TRADE_PCT * 2.5):
            self.assertFalse(portfolio_risk.portfolio_risk_limit_hit(exchange, "PAXG/USDT"))


if __name__ == "__main__":
    unittest.main()
