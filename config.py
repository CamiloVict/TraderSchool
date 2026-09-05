"""Central configuration for the trading bot.

Everything here is read from environment variables (.env) with
defaults. In Phase 1 only the exchange/market settings are actually
used (by data_fetcher.py and main.py). The risk parameters below are
placeholders with conservative, commonly-used defaults so that
risk_manager.py and strategy.py have something to import once they are
implemented in later phases — the actual values (and whether SMA or
EMA, which periods, etc.) should be revisited deliberately before any
order is ever placed, even on testnet.
"""
import os

from dotenv import load_dotenv

load_dotenv()

# --- Exchange / connection -------------------------------------------------
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")

# Safety-critical: defaults to True. Only ever becomes False via an
# explicit, deliberate change to .env — never hardcode "false" here.
USE_TESTNET = os.getenv("BINANCE_TESTNET", "true").strip().lower() == "true"

# --- Market ------------------------------------------------------------
# PAXG/USDT (oro) is the default focus of this bot; override via .env for BTC/USDT etc.
SYMBOL = os.getenv("SYMBOL", "PAXG/USDT")
TIMEFRAME = os.getenv("TIMEFRAME", "1h")

# --- Risk management (not used until risk_manager.py is implemented) ------
# % of capital risked on any single trade.
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))
# % move against entry price that triggers a stop-loss.
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "2.0"))
# % move in favor of entry price that triggers a take-profit.
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "4.0"))
# Daily circuit breaker: stop trading for the day after losing this % of capital.
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "5.0"))
# How far below the stop-loss trigger the STOP_LOSS_LIMIT's limit price
# sits, so the order still fills during a fast drop instead of resting
# unfilled above the market once triggered.
STOP_LOSS_LIMIT_SLIPPAGE_PCT = float(os.getenv("STOP_LOSS_LIMIT_SLIPPAGE_PCT", "0.5"))

# --- Chart-pattern confirmation filter (see patterns.py) -------------------
# Opt-in, off by default: when True, a newly-confirmed bearish reversal
# pattern (double-top, head-and-shoulders, triangle) blocks new
# EMA-crossover entries for a while. Purely a veto on entries — never
# forces an exit, never generates its own trades. See the analysis in the
# session that added this for why it's scoped this narrowly (chart
# patterns are backward-looking pattern matching, same family as the
# EMA crossover, not a "predictive" model — the evidence for them is
# weak even as a confirmation signal, essentially nonexistent as a
# stand-alone strategy).
USE_PATTERN_FILTER = os.getenv("USE_PATTERN_FILTER", "false").strip().lower() == "true"

# --- Setup Engine / Daily Market Context (see context_engine/) ------------
# Opt-in, off by default: when True, main.py --trade replaces the EMA
# crossover with context_engine's Setup Engine (LIQUIDITY_SWEEP_RECLAIM
# or CHART_PATTERN_REVERSAL) as the only reason to enter — every setup
# requires several independent pieces of evidence (bias, structure,
# liquidity, or a confirmed chart pattern) to agree at once, per the
# master prompt this responds to ("never treat an isolated pattern as a
# sufficient signal"). Off by default because
# it changes what actually places orders, and that has only been
# exercised in this session's own tests — never against a live
# Testnet feed. Turn it on deliberately once you've reviewed
# context_engine/ yourself, not as a side effect of upgrading.
USE_SETUP_ENGINE = os.getenv("USE_SETUP_ENGINE", "false").strip().lower() == "true"
# Days of 1h history main.py --trade fetches to build multi-timeframe
# context when USE_SETUP_ENGINE is on. Weekly structure needs ~60
# weekly candles (context_engine's own README math): 540 days is ~77
# weeks. Irrelevant, and not fetched, when USE_SETUP_ENGINE is False.
CONTEXT_HISTORY_DAYS = int(os.getenv("CONTEXT_HISTORY_DAYS", "540"))
