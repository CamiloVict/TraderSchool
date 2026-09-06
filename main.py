"""Entry point.

Two modes:
  1. `python main.py` (default) — Phase 1 connectivity check: proves
     ccxt can reach Binance Testnet, fetches recent OHLCV candles, and
     checks API-key auth if keys are set. Places no orders.
  2. `python main.py --trade` — runs ONE trading cycle and, if it
     decides to act, places a single Testnet order sized by
     risk_manager.position_size().

--trade is designed to be invoked once per candle close (e.g. by cron
or a systemd timer, hourly for the 1h timeframe) rather than run as an
infinite loop inside this process. That keeps each run short, easy to
log, and easy to kill/restart without losing track of state — the
"state" is just whatever the Testnet account currently holds, read
fresh from the exchange every time (see executor.get_base_asset_balance).

Every buy is immediately followed by a real STOP_LOSS_LIMIT sell order
on the exchange (see executor.place_stop_loss_order), so a sharp move
between hourly checks is capped without waiting for the next candle.
Known limitation, still flagged deliberately: no take-profit order is
placed (risk_manager.take_profit_price() is computed but unused) —
exits on a favorable move still wait for the exit condition below, not
a fixed target.

Which signal decides entries depends on config.USE_SETUP_ENGINE
(default False — see config.py for why it's opt-in):

  - **False (default): EMA crossover**, in _run_ema_cycle(). Enters on
    a bullish EMA 20/50 cross, exits on the cross back. config.
    USE_TREND_STRENGTH_FILTER is on by default (backtested, see
    README): a crossover where the EMAs are still closer than
    MIN_TREND_STRENGTH_ATR_MULTIPLE ATRs apart is a likely whipsaw and
    gets blocked ("entry_blocked_by_weak_trend" — see strategy.py's own
    trend_strength column). If config.USE_PATTERN_FILTER is also on
    (off by default, unlike the trend filter), a newly-confirmed
    bearish reversal pattern — double-top, head-and-shoulders, or
    triangle (see patterns.py) — blocks a new entry too. Both are
    purely vetoes, never an extra exit trigger.

  - **True: Setup Engine**, in _run_setup_engine_cycle(). Replaces the
    EMA signal with context_engine's Setup Engine — currently
    LIQUIDITY_SWEEP_RECLAIM (HTF bias, a swept-and-reclaimed level with
    displacement, and a confirming break of structure) or
    CHART_PATTERN_REVERSAL (a confirmed double-top/bottom,
    head-and-shoulders, or triangle agreeing with HTF bias) — every
    setup requires several independent pieces of evidence to agree
    (master prompt: "never treat an isolated pattern as a sufficient
    signal"). Exits
    when the bias no longer supports the position, the context calls
    no_trade, or a bearish chart pattern confirms — that last one does
    NOT require bias agreement first, unlike the entry rule: closing a
    position early is a lower evidence bar than opening one. The
    stop-loss order is still placed exactly as before,
    just priced off the setup's structural invalidation level instead
    of a flat STOP_LOSS_PCT. Long-only, like the rest of this bot — a
    SHORT setup is detected but never acted on.

Both cycles refuse new entries once today's (UTC) realized loss hits
MAX_DAILY_LOSS_PCT (action "entry_blocked_by_daily_loss_limit") — see
_daily_loss_limit_hit() and daily_loss_state.py for why that needs its
own tiny persisted file on top of risk_manager.DailyLossTracker, given
--trade's one-process-per-cron-tick invocation model above. Same
pattern, independently, for the current ISO week
(MAX_WEEKLY_LOSS_PCT, "entry_blocked_by_weekly_loss_limit" — see
_weekly_loss_limit_hit()/weekly_loss_state.py/WeeklyLossTracker) and
for a losing streak (MAX_CONSECUTIVE_LOSSES closed trades in a row,
"entry_blocked_by_consecutive_losses" — see
_consecutive_losses_hit()/risk_manager.consecutive_losses(), read
straight off trade_journal.py's own persisted history). All three only
block new entries; an existing position still exits through its own
normal rules.

`--trade` logs to both the console and a rotating logs/trading.log
(see _configure_logging()) instead of printing, and wraps the whole
cycle in a try/except that logs any exception with its traceback before
exiting non-zero — the only way cron/systemd (nobody watching stdout
live) can tell a run actually failed instead of it just silently going
missing from the schedule.

Every exchange *read* (candles, balances, open orders, trade history)
retries a transient ccxt.NetworkError with backoff (see retry.py) — a
one-off timeout no longer fails the whole cycle. Order *placement*
(executor.place_market_order / place_stop_loss_order) is deliberately
never retried that way: a timeout there doesn't tell you whether the
order actually reached Binance, so a blind retry risks placing it
twice. Those calls carry a deterministic newClientOrderId instead
(symbol + side + current UTC hour), so if the exact same order is ever
submitted twice — a retry, or this same hourly cycle running again —
Binance itself rejects the duplicate rather than executing it again.

Every cycle also syncs Binance's own trade history for SYMBOL into a
local journal (see trade_journal.py) — the only record of what the
live bot has actually done that doesn't require reading the exchange's
own history by hand — and records a heartbeat (see heartbeat.py) for
dead-man's-switch monitoring. All three of this, trade_journal, and
notify() below are best-effort: a failure in any of them is logged but
never fails the cycle itself, same reasoning as the retry policy above
applied to observability instead of to the trade logic.
"""
import argparse
import logging
import os
import sys

from config import (
    BINANCE_API_KEY,
    CONTEXT_HISTORY_DAYS,
    MAX_CONSECUTIVE_LOSSES,
    MIN_TREND_STRENGTH_ATR_MULTIPLE,
    RISK_PER_TRADE_PCT,
    SYMBOL,
    TIMEFRAME,
    USE_PATTERN_FILTER,
    USE_SETUP_ENGINE,
    USE_STRUCTURAL_STOP,
    USE_TESTNET,
    USE_TREND_STRENGTH_FILTER,
)
from balance_snapshot import record_balance
from daily_loss_state import load_or_init_starting_capital
from data_fetcher import fetch_ohlcv, get_exchange
from heartbeat import record_heartbeat
from notifier import notify
from patterns import PATTERN_VETO_LOOKBACK, bearish_veto_mask, detect_reversal_patterns
import portfolio_risk
from retry import call_with_retries
from risk_manager import DailyLossTracker, WeeklyLossTracker, consecutive_losses
from strategy import SLOW_PERIOD, add_signals
from trade_journal import read_journal, record_trades
import weekly_loss_state

logger = logging.getLogger("trading_bot")


def _daily_loss_limit_hit(current_equity: float) -> tuple:
    """(hit, loss_pct): whether today's (UTC) realized loss already
    meets or exceeds MAX_DAILY_LOSS_PCT -- new entries should be
    skipped for the rest of the day -- and the actual loss_pct so far,
    for logging *how close* the breaker is even when it didn't trip.
    Does not force-close an existing position; that still exits
    through its own normal rules (stop-loss/signal/bias/no_trade).

    A fresh DailyLossTracker is built every call because the class
    itself is in-memory only (see its docstring) and this function may
    run in a brand-new process each time (main.py --trade, invoked once
    per cron tick) — daily_loss_state.py is what makes "today's
    starting equity" survive between those runs so the comparison below
    is still meaningful.
    """
    starting_capital = load_or_init_starting_capital(current_equity)
    tracker = DailyLossTracker(starting_capital=starting_capital)
    tracker.record_trade_pnl(current_equity - starting_capital)
    return not tracker.trading_allowed(), tracker.current_loss_pct()


def _weekly_loss_limit_hit(current_equity: float) -> tuple:
    """Same idea as _daily_loss_limit_hit(), over the current ISO week
    instead of the current UTC day -- see weekly_loss_state.py and
    risk_manager.WeeklyLossTracker. Independent of the daily limit: a
    string of small losing days that never individually trips the
    daily breaker can still add up to a week worth stopping to
    reassess.
    """
    starting_capital = weekly_loss_state.load_or_init_starting_capital(current_equity)
    tracker = WeeklyLossTracker(starting_capital=starting_capital)
    tracker.record_trade_pnl(current_equity - starting_capital)
    return not tracker.trading_allowed(), tracker.current_loss_pct()


def _consecutive_losses_hit() -> tuple:
    """(hit, count): whether the most recent MAX_CONSECUTIVE_LOSSES
    *closed* trades all lost money -- new entries should pause until a
    win breaks the streak -- and the actual streak length, for logging
    even when it's below the threshold. Reads whatever trade_journal.py
    already persisted as of the start of this cycle (the previous
    cycle's own record_trades() call already synced any trade that
    closed since); does not itself trigger a fresh exchange fetch.
    """
    journal = read_journal()
    count = consecutive_losses(journal)
    return count >= MAX_CONSECUTIVE_LOSSES, count


def _portfolio_risk_limit_hit(exchange) -> bool:
    """True if the OTHER tracked bot (see portfolio_risk.py) already
    has a real position open, and adding this one on top would exceed
    MAX_PORTFOLIO_RISK_PCT combined. See that module's own docstring
    for why this is currently dormant in practice (BTC isn't traded by
    any live cycle yet) but built and tested anyway.
    """
    return portfolio_risk.portfolio_risk_limit_hit(exchange, SYMBOL)


def check_connection() -> None:
    print(f"Connecting to Binance {'TESTNET' if USE_TESTNET else 'LIVE (!)'} ...")
    exchange = get_exchange()

    try:
        markets = call_with_retries(exchange.load_markets)
        print(f"Connected OK. {len(markets)} markets available.")
    except Exception as exc:
        print(f"Failed to connect / load markets: {exc}")
        sys.exit(1)

    # Explicit and early, not left for the OHLCV fetch below to fail
    # on: Testnet typically lists far fewer pairs than the real
    # exchange (where every backtest in this repo actually ran), so
    # whether config.SYMBOL is tradeable here at all is a real, open
    # question worth a clear yes/no instead of an ambiguous ccxt
    # exception further down.
    if SYMBOL not in markets:
        near_matches = sorted(m for m in markets if SYMBOL.split("/")[0] in m)
        print(f"\n{SYMBOL} is NOT listed on this {'Testnet' if USE_TESTNET else 'exchange'}.")
        if near_matches:
            print(f"Markets with the same base asset that ARE listed: {near_matches}")
        else:
            print(f"No market lists {SYMBOL.split('/')[0]} as a base asset here at all.")
        print("Set SYMBOL in .env to a listed market before running --trade.")
        sys.exit(1)
    print(f"{SYMBOL} is listed and tradeable here.")

    print(f"\nFetching last 10 candles for {SYMBOL} ({TIMEFRAME}) ...")
    try:
        candles = fetch_ohlcv(exchange, symbol=SYMBOL, timeframe=TIMEFRAME, limit=10)
        print(candles.to_string())
    except Exception as exc:
        print(f"Failed to fetch OHLCV data: {exc}")
        sys.exit(1)

    if BINANCE_API_KEY:
        print("\nAPI key detected, checking authenticated access (balance) ...")
        try:
            balance = call_with_retries(exchange.fetch_balance)
            totals = balance.get("total", {}) or {}
            non_zero = {asset: amount for asset, amount in totals.items() if amount}
            print(f"Auth OK. Non-zero balances: {non_zero or '(none)'}")
        except Exception as exc:
            print(f"Auth check failed (expected if your testnet keys aren't set yet): {exc}")
    else:
        print("\nNo API key set in .env — skipping authenticated balance check.")
        print("(Public market data works fine without keys.)")


# Actions that don't need a human's attention -- everything else (a
# buy/sell, an entry blocked by the daily loss limit or the pattern
# filter, a missing stop rebuilt, no data) gets a notify() call. "hold"
# is the expected common case every single cycle; alerting on it would
# make the channel useless within a day.
_SILENT_ACTIONS = ("hold",)


def run_trading_cycle(exchange=None) -> dict:
    """Fetch the latest signal and, if it differs from the account's
    current position, place a single Testnet order. Returns a dict
    describing what happened, and also prints it. Dispatches to the
    EMA or Setup Engine cycle per config.USE_SETUP_ENGINE (see the
    module docstring)."""
    exchange = exchange or get_exchange()
    result = _run_setup_engine_cycle(exchange) if USE_SETUP_ENGINE else _run_ema_cycle(exchange)
    # Best-effort, same posture as notify() below: this cycle's actual
    # trading decision already happened above, so a journal hiccup
    # (a fetch that survives retry.call_with_retries and still fails, a
    # disk write error) must never look like the cycle itself failed.
    try:
        record_trades(exchange, SYMBOL)
    except Exception:
        logger.warning("Failed to update trade_journal.py's local trade history", exc_info=True)
    # Same best-effort posture: a snapshot failure must never look like
    # the trading cycle itself failed. Account-level (not keyed by
    # SYMBOL) -- see balance_snapshot.py's own docstring for why.
    try:
        record_balance(exchange)
    except Exception:
        logger.warning("Failed to record balance snapshot", exc_info=True)
    # Reaching this line means the cycle above completed without
    # raising -- exactly what "the bot is alive" means for the dead
    # man's switch. Same best-effort posture: a heartbeat hiccup is
    # never allowed to look like the trading cycle itself failed.
    try:
        record_heartbeat()
    except Exception:
        logger.warning("Failed to record heartbeat", exc_info=True)
    if result.get("action") not in _SILENT_ACTIONS:
        notify(f"{SYMBOL} {TIMEFRAME}: {result.get('action')} @ {result.get('price')}")
    return result


def _run_ema_cycle(exchange) -> dict:
    """EMA-crossover trading cycle — see the module docstring."""
    from executor import (
        cancel_order,
        get_average_fill_price,
        get_base_asset_balance,
        get_last_fill_price,
        get_open_stop_loss_orders,
        get_quote_asset_balance,
        get_total_base_asset_balance,
        meets_exchange_minimums,
        place_market_order,
        place_stop_loss_order,
    )
    from context_engine.features import atr, last_value
    from risk_manager import position_size, stop_loss_price, structural_stop_price, validate_stop_distance

    df = fetch_ohlcv(exchange, symbol=SYMBOL, timeframe=TIMEFRAME, limit=SLOW_PERIOD * 3)
    data = add_signals(df)
    latest = data.iloc[-1]
    price = float(latest["close"])
    signal = int(latest["signal"])
    current_atr = last_value(atr(data))

    def stop_for(entry_price: float) -> float:
        # Same fallback pattern as the Setup Engine's own structural
        # stop: structural_stop_price already falls back to the flat %
        # on its own when there's no usable swing yet.
        if USE_STRUCTURAL_STOP:
            return structural_stop_price(data, entry_price)
        return stop_loss_price(entry_price)

    # Total (free + locked) balance: an open stop-loss order locks the
    # coins it covers out of the *free* balance, so checking only free
    # balance would make an already-protected position look flat.
    total_balance = get_total_base_asset_balance(exchange, SYMBOL)
    # Dust threshold: ignore leftover balances worth less than $10 so
    # rounding from previous fills doesn't look like an open position.
    in_position = total_balance * price > 10

    action = "hold"
    order = None
    stop_order = None
    stop_price = None
    stop_source = None
    size = None
    size_reject_reason = None
    stop_distance_reject_reason = None
    daily_loss_pct = None
    weekly_loss_pct = None
    consecutive_losses_count = None

    entry_blocked_by_pattern = False
    entry_blocked_by_weak_trend = False
    daily_loss_limit_hit = False
    weekly_loss_limit_hit = False
    consecutive_losses_hit = False
    portfolio_risk_limit_hit = False
    if signal == 1 and not in_position:
        if USE_PATTERN_FILTER:
            pattern_signal = detect_reversal_patterns(data)
            entry_blocked_by_pattern = bool(
                bearish_veto_mask(pattern_signal, PATTERN_VETO_LOOKBACK).iloc[-1]
            )
        if USE_TREND_STRENGTH_FILTER:
            entry_blocked_by_weak_trend = bool(latest["trend_strength"] < MIN_TREND_STRENGTH_ATR_MULTIPLE)
        quote_balance = get_quote_asset_balance(exchange, SYMBOL)
        equity = quote_balance + total_balance * price
        daily_loss_limit_hit, daily_loss_pct = _daily_loss_limit_hit(equity)
        weekly_loss_limit_hit, weekly_loss_pct = _weekly_loss_limit_hit(equity)
        consecutive_losses_hit, consecutive_losses_count = _consecutive_losses_hit()
        portfolio_risk_limit_hit = _portfolio_risk_limit_hit(exchange)

    if (
        signal == 1
        and not in_position
        and not entry_blocked_by_pattern
        and not entry_blocked_by_weak_trend
        and not daily_loss_limit_hit
        and not weekly_loss_limit_hit
        and not consecutive_losses_hit
        and not portfolio_risk_limit_hit
    ):
        stop_price = stop_for(price)
        stop_source = "structural_swing_low" if USE_STRUCTURAL_STOP else "flat_stop_loss_pct"
        stop_distance_ok, stop_distance_reject_reason = validate_stop_distance(price, stop_price, current_atr)
        if stop_distance_ok:
            size = position_size(quote_balance, price, stop_price=stop_price)
            if size > 0:
                size_ok, size_reject_reason = meets_exchange_minimums(exchange, SYMBOL, size, price)
                if size_ok:
                    order = place_market_order(exchange, SYMBOL, "buy", size)
                    action = "buy"
                    entry_price = get_average_fill_price(order) or price
                    filled_amount = float(order.get("filled") or size)
                    # Re-derived off the real average fill price, not
                    # the pre-trade estimate used to size the order
                    # above -- same as this cycle already did for the
                    # flat-% stop.
                    stop_price = stop_for(entry_price)
                    stop_order = place_stop_loss_order(exchange, SYMBOL, filled_amount, stop_price)
                else:
                    action = "entry_skipped_below_exchange_minimum"
        else:
            action = "entry_blocked_by_stop_distance"
    elif signal == 1 and not in_position and entry_blocked_by_pattern:
        action = "entry_blocked_by_pattern"
    elif signal == 1 and not in_position and entry_blocked_by_weak_trend:
        action = "entry_blocked_by_weak_trend"
    elif signal == 1 and not in_position and daily_loss_limit_hit:
        action = "entry_blocked_by_daily_loss_limit"
    elif signal == 1 and not in_position and weekly_loss_limit_hit:
        action = "entry_blocked_by_weekly_loss_limit"
    elif signal == 1 and not in_position and consecutive_losses_hit:
        action = "entry_blocked_by_consecutive_losses"
    elif signal == 1 and not in_position and portfolio_risk_limit_hit:
        action = "entry_blocked_by_portfolio_risk"
    elif signal == 0 and in_position:
        # Cancel the protective stop first so it doesn't compete with
        # this market sell for the same (currently locked) balance.
        for stale_order in get_open_stop_loss_orders(exchange, SYMBOL):
            cancel_order(exchange, SYMBOL, stale_order["id"])
        free_balance = get_base_asset_balance(exchange, SYMBOL)
        order = place_market_order(exchange, SYMBOL, "sell", free_balance)
        action = "sell"
    elif in_position and not get_open_stop_loss_orders(exchange, SYMBOL):
        # Self-heal: holding a position with no protective stop on the
        # exchange (e.g. a previous run crashed between the buy and the
        # stop-loss placement, or it was cancelled outside the bot).
        # Reconstruct the entry price from the last buy fill so the
        # position isn't left unprotected until the next EMA exit.
        entry_price = get_last_fill_price(exchange, SYMBOL, "buy") or price
        stop_price = stop_for(entry_price)
        stop_source = "structural_swing_low" if USE_STRUCTURAL_STOP else "flat_stop_loss_pct"
        stop_order = place_stop_loss_order(exchange, SYMBOL, total_balance, stop_price)
        action = "stop_loss_replaced"

    result = {
        "timestamp": str(latest.name),
        "price": price,
        "signal": signal,
        "in_position_before": in_position,
        "action": action,
        "reason": _ema_action_reason(
            action,
            in_position=in_position,
            stop_price=stop_price,
            stop_source=stop_source,
            size=size,
            size_reject_reason=size_reject_reason,
            stop_distance_reject_reason=stop_distance_reject_reason,
            trend_strength=float(latest["trend_strength"]),
            daily_loss_pct=daily_loss_pct,
            weekly_loss_pct=weekly_loss_pct,
            consecutive_losses_count=consecutive_losses_count,
        ),
        "order_id": order.get("id") if order else None,
        "stop_order_id": stop_order.get("id") if stop_order else None,
        "stop_price": stop_price,
        "stop_source": stop_source,
        "size": size,
        "risk_pct": RISK_PER_TRADE_PCT if action == "buy" else None,
        "daily_loss_pct": daily_loss_pct,
        "weekly_loss_pct": weekly_loss_pct,
        "consecutive_losses_count": consecutive_losses_count,
    }
    logger.info(result)
    return result


def _ema_action_reason(
    action: str,
    *,
    in_position: bool,
    stop_price,
    stop_source,
    size,
    size_reject_reason,
    stop_distance_reject_reason,
    trend_strength,
    daily_loss_pct,
    weekly_loss_pct,
    consecutive_losses_count,
) -> str:
    """One-sentence, human-readable explanation of why the EMA cycle
    did what it did this run -- so a trade decision can be audited
    from logs/trading.log without re-reading this module's branches.
    Every action string above maps to exactly one reason here."""
    if action == "buy":
        return (
            f"EMA fast crossed above slow with no position open; sized to risk "
            f"{RISK_PER_TRADE_PCT}% of capital against a {stop_source} stop at {stop_price}"
        )
    if action == "sell":
        return "EMA fast crossed back below slow while in position -- signal exit"
    if action == "entry_blocked_by_pattern":
        return "EMA signal is bullish, but a confirmed bearish chart pattern is vetoing new entries"
    if action == "entry_blocked_by_weak_trend":
        return (
            f"EMA signal is bullish, but the EMAs are only {trend_strength:.2f} ATRs apart "
            f"(< MIN_TREND_STRENGTH_ATR_MULTIPLE={MIN_TREND_STRENGTH_ATR_MULTIPLE}) -- too weak a "
            "trend to trust the crossover"
        )
    if action == "entry_blocked_by_daily_loss_limit":
        return f"EMA signal is bullish, but today's realized loss ({daily_loss_pct:.2f}%) has hit MAX_DAILY_LOSS_PCT"
    if action == "entry_blocked_by_weekly_loss_limit":
        return f"EMA signal is bullish, but this week's realized loss ({weekly_loss_pct:.2f}%) has hit MAX_WEEKLY_LOSS_PCT"
    if action == "entry_blocked_by_consecutive_losses":
        return f"EMA signal is bullish, but the last {consecutive_losses_count} closed trades all lost -- pausing for a win to break the streak"
    if action == "entry_blocked_by_portfolio_risk":
        return "EMA signal is bullish, but the other tracked bot already has a position open and combined risk would exceed MAX_PORTFOLIO_RISK_PCT"
    if action == "entry_blocked_by_stop_distance":
        return f"EMA signal is bullish, but the computed stop failed distance validation: {stop_distance_reject_reason}"
    if action == "entry_skipped_below_exchange_minimum":
        return f"EMA signal is bullish and every risk check passed, but the sized position isn't executable: {size_reject_reason}"
    if action == "stop_loss_replaced":
        return f"in position with no protective stop on the exchange -- rebuilt a {stop_source} stop at {stop_price} from the last buy fill"
    if in_position:
        return "in position, EMA signal still bullish -- holding, waiting for the exit condition"
    return "no EMA entry signal (fast EMA not above slow)"


def _run_setup_engine_cycle(exchange) -> dict:
    """Setup Engine trading cycle — see the module docstring.

    Order execution still only ever uses Testnet (`exchange`, passed
    in), but building context needs far more history than Testnet
    retains, so — exactly like backtester.py and context_engine's own
    CLI — the *context* is built from real Binance's public market
    data via get_public_data_exchange(). Same split as everywhere else
    in this repo: real data to see the market, Testnet to touch it.
    """
    import pandas as pd

    from context_engine.engine import build_context
    from context_engine.features import atr, last_value
    from context_engine.schema import Bias, Direction
    from context_engine.timeframes import build_timeframe_set
    from data_fetcher import fetch_ohlcv_history, get_public_data_exchange
    from executor import (
        cancel_order,
        get_average_fill_price,
        get_base_asset_balance,
        get_open_stop_loss_orders,
        get_quote_asset_balance,
        get_total_base_asset_balance,
        meets_exchange_minimums,
        place_market_order,
        place_stop_loss_order,
    )
    from risk_manager import position_size, stop_loss_price, validate_stop_distance

    context_exchange = get_public_data_exchange()
    since_ms = context_exchange.parse8601(
        (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=CONTEXT_HISTORY_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    history = fetch_ohlcv_history(context_exchange, symbol=SYMBOL, timeframe=TIMEFRAME, since_ms=since_ms)
    frames = build_timeframe_set(history, base_timeframe=TIMEFRAME)

    execution = frames.get(TIMEFRAME)
    if execution is None or execution.empty:
        result = {
            "timestamp": None,
            "price": None,
            "action": "no_data",
            "reason": f"no {TIMEFRAME} candles came back from build_timeframe_set for {SYMBOL}",
            "in_position_before": None,
            "order_id": None,
            "stop_order_id": None,
        }
        logger.info(result)
        return result

    # Stateless by design (matching the rest of this bot): rather than
    # persist market_state to disk between cron runs, rebuild it fresh
    # by asking what it was as of the *previous* candle, then thread
    # that into building it as of now. build_context() itself never
    # remembers anything between calls.
    previous_state = None
    if len(execution) >= 2:
        previous_state = build_context(frames, asset=SYMBOL, as_of=execution.index[-2]).market_state

    context = build_context(frames, asset=SYMBOL, previous_state=previous_state)
    price = float(execution["close"].iloc[-1])
    current_atr = last_value(atr(execution))

    total_balance = get_total_base_asset_balance(exchange, SYMBOL)
    in_position = total_balance * price > 10

    long_setup = next((s for s in context.setups if s.direction == Direction.LONG), None)
    bullish_bias = context.bias.direction in (Bias.BULLISH, Bias.STRONG_BULLISH)
    # A freshly-confirmed bearish chart pattern closes the position
    # directly, on top of the bias/no_trade exits below — deliberately
    # *not* gated on bias agreeing first, unlike CHART_PATTERN_REVERSAL's
    # entry rule. Getting out early is risk-reducing, not risk-taking,
    # so the bar is lower than for an entry. Only computed while actually
    # holding something to exit.
    bearish_pattern = (
        bool(bearish_veto_mask(detect_reversal_patterns(execution), PATTERN_VETO_LOOKBACK).iloc[-1])
        if in_position
        else False
    )

    action = "hold"
    order = None
    stop_order = None
    stop_price = None
    stop_source = None
    size = None
    size_reject_reason = None
    stop_distance_reject_reason = None
    daily_loss_pct = None
    weekly_loss_pct = None
    consecutive_losses_count = None
    exit_trigger = None

    daily_loss_limit_hit = False
    weekly_loss_limit_hit = False
    consecutive_losses_hit = False
    portfolio_risk_limit_hit = False
    if long_setup is not None and not in_position and not context.no_trade:
        quote_balance = get_quote_asset_balance(exchange, SYMBOL)
        equity = quote_balance + total_balance * price
        daily_loss_limit_hit, daily_loss_pct = _daily_loss_limit_hit(equity)
        weekly_loss_limit_hit, weekly_loss_pct = _weekly_loss_limit_hit(equity)
        consecutive_losses_hit, consecutive_losses_count = _consecutive_losses_hit()
        portfolio_risk_limit_hit = _portfolio_risk_limit_hit(exchange)

    if (
        long_setup is not None
        and not in_position
        and not context.no_trade
        and not daily_loss_limit_hit
        and not weekly_loss_limit_hit
        and not consecutive_losses_hit
        and not portfolio_risk_limit_hit
    ):
        stop_price = long_setup.invalidation.level
        stop_source = "setup_invalidation_level"
        if stop_price is None or stop_price >= price:
            stop_price = stop_loss_price(price)  # degenerate level: fall back to the flat %
            stop_source = "flat_stop_loss_pct_fallback"
        stop_distance_ok, stop_distance_reject_reason = validate_stop_distance(price, stop_price, current_atr)
        if stop_distance_ok:
            size = position_size(quote_balance, price, stop_price=stop_price)
            if size > 0:
                size_ok, size_reject_reason = meets_exchange_minimums(exchange, SYMBOL, size, price)
                if size_ok:
                    order = place_market_order(exchange, SYMBOL, "buy", size)
                    action = "buy"
                    filled_amount = float(order.get("filled") or size)
                    stop_order = place_stop_loss_order(exchange, SYMBOL, filled_amount, stop_price)
                else:
                    action = "entry_skipped_below_exchange_minimum"
        else:
            action = "entry_blocked_by_stop_distance"
    elif long_setup is not None and not in_position and not context.no_trade and daily_loss_limit_hit:
        action = "entry_blocked_by_daily_loss_limit"
    elif long_setup is not None and not in_position and not context.no_trade and weekly_loss_limit_hit:
        action = "entry_blocked_by_weekly_loss_limit"
    elif long_setup is not None and not in_position and not context.no_trade and consecutive_losses_hit:
        action = "entry_blocked_by_consecutive_losses"
    elif long_setup is not None and not in_position and not context.no_trade and portfolio_risk_limit_hit:
        action = "entry_blocked_by_portfolio_risk"
    elif in_position and (context.no_trade or not bullish_bias or bearish_pattern):
        # The bias that justified this position is gone, context says
        # not to trade at all right now, or a bearish pattern just
        # confirmed — cancel the protective stop first so it doesn't
        # compete with this market sell for the same (currently locked)
        # balance. Precedence matches the condition's own order: a
        # bearish pattern can fire alongside either of the other two,
        # but no_trade/bias are checked first since they're the primary
        # exit rule this cycle's docstring describes.
        exit_trigger = "no_trade" if context.no_trade else ("bias_flip" if not bullish_bias else "bearish_pattern")
        for stale_order in get_open_stop_loss_orders(exchange, SYMBOL):
            cancel_order(exchange, SYMBOL, stale_order["id"])
        free_balance = get_base_asset_balance(exchange, SYMBOL)
        order = place_market_order(exchange, SYMBOL, "sell", free_balance)
        action = "sell"
    elif in_position and not get_open_stop_loss_orders(exchange, SYMBOL):
        # Self-heal, same idea as the EMA cycle: reconstruct a missing
        # stop from the current context's invalidation level rather
        # than leaving the position unprotected.
        stop_price = context.invalidation.level
        stop_source = "setup_invalidation_level"
        if stop_price is None or stop_price >= price:
            stop_price = stop_loss_price(price)
            stop_source = "flat_stop_loss_pct_fallback"
        stop_order = place_stop_loss_order(exchange, SYMBOL, total_balance, stop_price)
        action = "stop_loss_replaced"

    result = {
        "timestamp": str(execution.index[-1]),
        "price": price,
        "market_state": context.market_state.value,
        "bias": context.bias.direction.value,
        "in_position_before": in_position,
        "action": action,
        "reason": _setup_engine_action_reason(
            action,
            in_position=in_position,
            stop_price=stop_price,
            stop_source=stop_source,
            exit_trigger=exit_trigger,
            size_reject_reason=size_reject_reason,
            stop_distance_reject_reason=stop_distance_reject_reason,
            daily_loss_pct=daily_loss_pct,
            weekly_loss_pct=weekly_loss_pct,
            consecutive_losses_count=consecutive_losses_count,
        ),
        "order_id": order.get("id") if order else None,
        "stop_order_id": stop_order.get("id") if stop_order else None,
        "stop_price": stop_price,
        "stop_source": stop_source,
        "size": size,
        "risk_pct": RISK_PER_TRADE_PCT if action == "buy" else None,
        "daily_loss_pct": daily_loss_pct,
        "weekly_loss_pct": weekly_loss_pct,
        "consecutive_losses_count": consecutive_losses_count,
    }
    logger.info(result)
    return result


def _setup_engine_action_reason(
    action: str,
    *,
    in_position: bool,
    stop_price,
    stop_source,
    exit_trigger,
    size_reject_reason,
    stop_distance_reject_reason,
    daily_loss_pct,
    weekly_loss_pct,
    consecutive_losses_count,
) -> str:
    """Same purpose as _ema_action_reason() -- one auditable sentence
    per action, for the Setup Engine cycle."""
    if action == "buy":
        return (
            f"a confirmed long setup agrees with HTF bias with no position open; sized to risk "
            f"{RISK_PER_TRADE_PCT}% of capital against a {stop_source} stop at {stop_price}"
        )
    if action == "sell":
        if exit_trigger == "no_trade":
            return "context now calls no_trade while in position -- exiting"
        if exit_trigger == "bias_flip":
            return "HTF bias no longer supports the position -- exiting"
        return "a bearish chart pattern just confirmed while in position -- exiting regardless of bias"
    if action == "entry_blocked_by_daily_loss_limit":
        return f"a confirmed long setup agrees with bias, but today's realized loss ({daily_loss_pct:.2f}%) has hit MAX_DAILY_LOSS_PCT"
    if action == "entry_blocked_by_weekly_loss_limit":
        return f"a confirmed long setup agrees with bias, but this week's realized loss ({weekly_loss_pct:.2f}%) has hit MAX_WEEKLY_LOSS_PCT"
    if action == "entry_blocked_by_consecutive_losses":
        return f"a confirmed long setup agrees with bias, but the last {consecutive_losses_count} closed trades all lost -- pausing for a win to break the streak"
    if action == "entry_blocked_by_portfolio_risk":
        return "a confirmed long setup agrees with bias, but the other tracked bot already has a position open and combined risk would exceed MAX_PORTFOLIO_RISK_PCT"
    if action == "entry_blocked_by_stop_distance":
        return f"a confirmed long setup agrees with bias, but the computed stop failed distance validation: {stop_distance_reject_reason}"
    if action == "entry_skipped_below_exchange_minimum":
        return f"a confirmed long setup agrees with bias and every risk check passed, but the sized position isn't executable: {size_reject_reason}"
    if action == "stop_loss_replaced":
        return f"in position with no protective stop on the exchange -- rebuilt a {stop_source} stop at {stop_price} from the current context"
    if in_position:
        return "in position, bias/context still support it -- holding, waiting for the exit condition"
    return "no confirmed long setup agreeing with HTF bias right now"


def _configure_logging() -> None:
    """Console + a small rotating log file (logs/trading.log).

    Only called from main() — importing this module (as every test in
    this repo does) must never create a logs/ directory or file as a
    side effect. A rotating file, not a plain one, because --trade runs
    unattended via cron/systemd (see the module docstring): nobody is
    there to notice or truncate an ever-growing log.
    """
    from logging.handlers import RotatingFileHandler

    os.makedirs("logs", exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    file_handler = RotatingFileHandler("logs/trading.log", maxBytes=1_000_000, backupCount=5)
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.setLevel(logging.INFO)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Testnet crypto trading bot")
    parser.add_argument(
        "--trade",
        action="store_true",
        help="Run one live (Testnet) trading cycle instead of the connectivity check.",
    )
    args = parser.parse_args()

    if args.trade:
        _configure_logging()
        if not USE_TESTNET:
            logger.error("BINANCE_TESTNET is not 'true' — refusing to trade. See executor.py.")
            sys.exit(1)
        # Cron/systemd only surfaces a failure through the exit code (no
        # one is watching stdout live) — log the full traceback to the
        # persistent file *before* it, so a bad run is debuggable after
        # the fact instead of just silently missing from the schedule.
        try:
            run_trading_cycle()
        except Exception:
            logger.exception("Trading cycle failed")
            notify(f"{SYMBOL} {TIMEFRAME}: trading cycle FAILED — see logs/trading.log")
            sys.exit(1)
    else:
        check_connection()


if __name__ == "__main__":
    main()
