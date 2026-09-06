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
# Reject an entry if its stop sits fewer than this many ATRs from
# price -- close enough to be inside normal noise, likely to stop out
# on nothing meaningful. Deliberately low by default: this is a sanity
# floor against a degenerate stop, not a strategy-quality knob to tune
# aggressively without backtesting first.
MIN_STOP_DISTANCE_ATR_MULTIPLE = float(os.getenv("MIN_STOP_DISTANCE_ATR_MULTIPLE", "0.3"))
# Reject an entry if its stop sits more than this many ATRs from
# price -- a stop this wide is either a degenerate level (bad data, a
# gap, a bug in whatever computed it) or genuinely excessive structural
# risk for one trade. Deliberately generous by default: a low-volatility
# asset like PAXG can have an hourly ATR that's a small fraction of a
# percent of price, while the flat STOP_LOSS_PCT (2% default) doesn't
# scale down with it -- this is meant as a guard rail against a broken
# stop, not a strategy-quality filter tuned to second-guess the flat
# stop's own already-backtested distance. Revisit with real ATR data
# for whatever SYMBOL/TIMEFRAME you actually run before tightening it.
MAX_STOP_DISTANCE_ATR_MULTIPLE = float(os.getenv("MAX_STOP_DISTANCE_ATR_MULTIPLE", "15.0"))
# % move in favor of entry price that triggers a take-profit.
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "4.0"))
# Binance spot taker fee, one side, % -- risk_manager.position_size()
# folds 2x this (a round trip: one entry fill + one exit fill) into
# the loss-per-unit at the stop, so RISK_PER_TRADE_PCT is the actual
# worst-case loss (price move + both fees), not just its price
# component. Also the single source backtester.py/scalping_backtester.py
# import their own TAKER_FEE_PCT from, so a live fee change and a
# backtest fee assumption can't silently drift apart.
TAKER_FEE_PCT = float(os.getenv("TAKER_FEE_PCT", "0.1"))
# Daily circuit breaker: stop trading for the day after losing this % of capital.
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "5.0"))
# Weekly circuit breaker: same idea as MAX_DAILY_LOSS_PCT, over the
# current ISO week (UTC, Monday-start) instead of the current UTC day.
# A bad week can clear the daily limit's bar on no single day yet still
# be a week worth stopping to reassess.
MAX_WEEKLY_LOSS_PCT = float(os.getenv("MAX_WEEKLY_LOSS_PCT", "10.0"))
# Stop new entries after this many *closed* trades in a row lost money
# (existing positions still exit through their own normal rules). Does
# not itself change RISK_PER_TRADE_PCT -- see risk_manager.py's own
# note on why risk is never auto-adjusted from a losing streak without
# separate, deliberate evidence that doing so helps.
MAX_CONSECUTIVE_LOSSES = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "5"))
# Cross-asset circuit breaker (see portfolio_risk.py): the max combined
# RISK_PER_TRADE_PCT this account should have open across the two
# tracked bots (PAXG and BTC) at once. Both bots size independently
# against only their own equity slice, so two live positions at once
# could otherwise stack real simultaneous risk with neither side aware
# of the other. Below 2x RISK_PER_TRADE_PCT by default (a plain
# worst-case sum, not correlation-adjusted -- PAXG/gold and BTC have no
# established correlation to model), forcing a real tradeoff instead of
# silently allowing both at full size.
MAX_PORTFOLIO_RISK_PCT = float(os.getenv("MAX_PORTFOLIO_RISK_PCT", "1.5"))
# How far below the stop-loss trigger the STOP_LOSS_LIMIT's limit price
# sits, so the order still fills during a fast drop instead of resting
# unfilled above the market once triggered.
STOP_LOSS_LIMIT_SLIPPAGE_PCT = float(os.getenv("STOP_LOSS_LIMIT_SLIPPAGE_PCT", "0.5"))

# Opt-in, off by default: when True, the EMA-crossover cycle's stop is
# risk_manager.structural_stop_price() (last confirmed swing low/high,
# from context_engine.structure -- the same building block the Setup
# Engine's own stop already uses) instead of the flat STOP_LOSS_PCT.
# Off by default for the same reason USE_SETUP_ENGINE/USE_PATTERN_FILTER
# are: it changes what actually protects a live order, so turn it on
# deliberately once you've backtested it, not as a side effect of
# upgrading.
USE_STRUCTURAL_STOP = os.getenv("USE_STRUCTURAL_STOP", "false").strip().lower() == "true"
# How many ATRs beyond the swing level the stop sits, so it isn't
# resting exactly on the level that invalidates the trade (see
# risk_manager.structural_stop_price's own docstring).
STRUCTURAL_STOP_ATR_BUFFER_MULTIPLE = float(os.getenv("STRUCTURAL_STOP_ATR_BUFFER_MULTIPLE", "0.2"))

# --- Pyramiding: adding to an already-open position (see risk_manager.py) --
# On by default (backtested, see README's own walk-forward numbers --
# unlike USE_STRUCTURAL_STOP/USE_PATTERN_FILTER, which stay off pending
# their own backtest evidence): an already-open long can get ONE
# additional tranche (see MAX_PYRAMID_ENTRIES) if price has moved at
# least PYRAMID_TRIGGER_ATR_MULTIPLE ATRs beyond the last entry while
# the EMA signal is still bullish -- i.e. only adds to a trend that's
# still confirming itself, never to a stalling or reversing one. The
# stop for the whole position is recalculated (not left at the original
# tranche's level) once a new tranche is added, so pyramiding also
# locks in some of the first tranche's paper gain instead of just
# adding exposure. A 4-segment walk-forward on 365 real PAXG/USDT 1h
# days showed the same amplifying pattern in every segment (better in
# the 3 winning ones, worse in the 1 losing one) rather than a fluke
# concentrated in one window -- see README for the numbers. Set to
# "false" in .env to go back to plain single-tranche entries.
USE_PYRAMIDING = os.getenv("USE_PYRAMIDING", "true").strip().lower() == "true"
# Max additional tranches per position (so total tranches = 1 + this).
# Deliberately capped low by default -- pyramiding without a cap can
# turn a single bad reversal into far more than RISK_PER_TRADE_PCT lost,
# the opposite of what risk_manager.py is for.
MAX_PYRAMID_ENTRIES = int(os.getenv("MAX_PYRAMID_ENTRIES", "1"))
# How many ATRs price must move beyond the previous tranche's entry
# before another tranche is added. Placeholder -- backtest it for the
# SYMBOL/TIMEFRAME you actually run before trusting it.
PYRAMID_TRIGGER_ATR_MULTIPLE = float(os.getenv("PYRAMID_TRIGGER_ATR_MULTIPLE", "1.5"))
# % of capital risked on an add-on tranche, sized the same way
# RISK_PER_TRADE_PCT sizes a fresh entry (see risk_manager.position_size's
# own risk_pct parameter). Deliberately half of RISK_PER_TRADE_PCT by
# default, not the same: a full second helping of risk on top of an
# already-open position doubles worst-case loss on one reversal without
# a corresponding edge to justify it -- that's added variance, not
# added expected profit. Revisit only with real backtest evidence that
# a higher value actually improves risk-adjusted returns, the same bar
# MIN_TREND_STRENGTH_ATR_MULTIPLE had to clear before its own default
# changed.
PYRAMID_RISK_PCT = float(os.getenv("PYRAMID_RISK_PCT", str(RISK_PER_TRADE_PCT / 2)))

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

# --- Trend-strength confirmation filter (see strategy.py) ------------------
# On by default (unlike USE_STRUCTURAL_STOP/USE_PATTERN_FILTER, which
# stay off pending backtest evidence this filter now has): when True, a
# fresh EMA crossover is only taken if strategy.py's own trend_strength
# (|ema_fast - ema_slow| / ATR) is at least MIN_TREND_STRENGTH_ATR_MULTIPLE.
# The EMAs crossing is necessary but not sufficient evidence of a real
# trend -- a ranging/choppy market produces the same crossover with the
# two EMAs still right on top of each other, which tends to reverse
# again a few candles later (a whipsaw) and, on PAXG, eats the
# round-trip fee for nothing. Purely a veto on entries -- never forces
# an exit, never generates its own trades. Turned on after backtesting
# against 365 real days of PAXG/USDT 1h (see README): at
# MIN_TREND_STRENGTH_ATR_MULTIPLE's own default of 1.5, Sharpe
# 0.99->1.09, profit factor 1.39->1.89, max drawdown -12.17%->-8.12%,
# for a small cost in raw return (8.50%->7.80%). Set to "false" in
# .env to go back to the raw EMA crossover with no trend confirmation.
USE_TREND_STRENGTH_FILTER = os.getenv("USE_TREND_STRENGTH_FILTER", "true").strip().lower() == "true"
# Minimum trend_strength (EMA separation, in ATRs) required to take a
# crossover entry when USE_TREND_STRENGTH_FILTER is on. Backtested
# against 365 real days of PAXG/USDT 1h (see README's own section on
# this filter): 1.0 (this value's first, unbacktested guess) was a net
# loss versus no filter at all (lower Sharpe AND lower return -- it cut
# some bad trades but also some good ones). 1.5 was a real improvement
# on every quality axis -- Sharpe 0.99->1.09, profit factor 1.39->1.89,
# max drawdown -12.17%->-8.12%, win rate 33.75%->56.67% -- for a small
# cost in raw return (8.50%->7.80%) and a third of the trades (80->30).
# Re-backtest before trusting this for a different SYMBOL/TIMEFRAME.
MIN_TREND_STRENGTH_ATR_MULTIPLE = float(os.getenv("MIN_TREND_STRENGTH_ATR_MULTIPLE", "1.5"))

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

# --- Notifications (see notifier.py) ---------------------------------------
# Opt-in, off by default: main.py --trade fans a message out to
# whichever of these are configured whenever it does something worth
# knowing about (a buy/sell, an entry blocked by the daily loss limit
# or the pattern filter, no data) or the cycle raises an unhandled
# exception. Leave both empty to keep --trade silent except for
# logs/trading.log, exactly like before this existed.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
# Generic webhook URL (a Slack incoming webhook, a Discord webhook, or
# anything else that accepts a POSTed JSON body) — notifier.py sends
# both {"text": ...} and {"content": ...} so either works without this
# needing to know which service it's pointed at.
ALERT_WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "")

# --- Dead man's switch (see heartbeat.py) -----------------------------------
# Opt-in, off by default: a free external service like
# https://healthchecks.io or https://cronitor.io that main.py --trade
# pings after every cycle that completes without raising. Configure the
# expected-ping interval on the service itself (outside this machine) —
# that's what actually lets it notice when --trade has stopped running
# altogether (cron died, the server is down), which nothing running on
# this same machine could ever detect about itself.
HEARTBEAT_PING_URL = os.getenv("HEARTBEAT_PING_URL", "")
