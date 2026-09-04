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
SYMBOL = os.getenv("SYMBOL", "BTC/USDT")
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
