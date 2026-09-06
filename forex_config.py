"""Forex configuration -- separate from config.py because Forex is a
genuinely different market with its own broker/account model, not
another symbol on the same exchange (see README's own Forex section
for the full reasoning behind picking OANDA specifically).

Nothing here is wired into main.py --trade or any live order path yet.
Only forex_data_fetcher.py reads these values so far -- read-only
market data, no orders. Same "everything from .env, safe empty
defaults" convention as config.py: an unconfigured OANDA_API_TOKEN
means every forex_data_fetcher.py call raises immediately (see its own
docstring) rather than silently doing nothing or hitting a real
account by accident.
"""
import os

from dotenv import load_dotenv

load_dotenv()

# --- OANDA v20 REST API -----------------------------------------------------
# Generate a personal access token from your OANDA account (practice or
# live) at https://www.oanda.com/account/tpa/personal_token -- see
# README's Forex section for the full signup walkthrough. Empty by
# default; nothing in this repo can reach OANDA without it.
OANDA_API_TOKEN = os.getenv("OANDA_API_TOKEN", "")
# The account id a token is scoped to (shown next to the token when you
# generate it, format like "101-004-XXXXXXX-001"). Only needed once
# order placement/account-balance calls exist (they don't yet).
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "")
# Safety-critical, same posture as config.USE_TESTNET: defaults to the
# practice (paper) environment. Only ever becomes "live" via an
# explicit, deliberate change to .env -- never hardcode "live" here.
# The two environments are entirely separate OANDA accounts with
# different base URLs (see forex_data_fetcher.py) -- a practice token
# is rejected outright against the live URL and vice versa, so this
# can't silently point a practice token at a real-money account.
OANDA_ENVIRONMENT = os.getenv("OANDA_ENVIRONMENT", "practice").strip().lower()

# --- Market ------------------------------------------------------------
# XAU/USD (spot gold vs. the dollar) is the natural first Forex
# instrument for this repo: it's the same underlying asset PAXG already
# trades (see README's PAXG-vs-XAU/USD explainer), which makes a future
# side-by-side comparison meaningful. OANDA's own instrument naming
# uses an underscore, not a slash.
FOREX_SYMBOL = os.getenv("FOREX_SYMBOL", "XAU_USD")
# OANDA granularity code (see forex_data_fetcher.py's own docstring for
# the full list) -- H1 matches this repo's existing PAXG/BTC timeframe
# conventions (backtester.py, strategy.py) for now.
FOREX_TIMEFRAME = os.getenv("FOREX_TIMEFRAME", "H1")
