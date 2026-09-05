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
    a bullish EMA 20/50 cross, exits on the cross back. If
    config.USE_PATTERN_FILTER is also on, a newly-confirmed bearish
    reversal pattern — double-top, head-and-shoulders, or triangle
    (see patterns.py) — blocks a new entry. Purely a veto, never an
    extra exit trigger.

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
--trade's one-process-per-cron-tick invocation model above. It only
blocks new entries; an existing position still exits through its own
normal rules.

`--trade` logs to both the console and a rotating logs/trading.log
(see _configure_logging()) instead of printing, and wraps the whole
cycle in a try/except that logs any exception with its traceback before
exiting non-zero — the only way cron/systemd (nobody watching stdout
live) can tell a run actually failed instead of it just silently going
missing from the schedule.
"""
import argparse
import logging
import os
import sys

from config import (
    BINANCE_API_KEY,
    CONTEXT_HISTORY_DAYS,
    SYMBOL,
    TIMEFRAME,
    USE_PATTERN_FILTER,
    USE_SETUP_ENGINE,
    USE_TESTNET,
)
from daily_loss_state import load_or_init_starting_capital
from data_fetcher import fetch_ohlcv, get_exchange
from patterns import PATTERN_VETO_LOOKBACK, bearish_veto_mask, detect_reversal_patterns
from risk_manager import DailyLossTracker
from strategy import SLOW_PERIOD, add_signals

logger = logging.getLogger("trading_bot")


def _daily_loss_limit_hit(current_equity: float) -> bool:
    """True if today's (UTC) realized loss already meets or exceeds
    MAX_DAILY_LOSS_PCT — new entries should be skipped for the rest of
    the day. Does not force-close an existing position; that still
    exits through its own normal rules (stop-loss/signal/bias/no_trade).

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
    return not tracker.trading_allowed()


def check_connection() -> None:
    print(f"Connecting to Binance {'TESTNET' if USE_TESTNET else 'LIVE (!)'} ...")
    exchange = get_exchange()

    try:
        markets = exchange.load_markets()
        print(f"Connected OK. {len(markets)} markets available.")
    except Exception as exc:
        print(f"Failed to connect / load markets: {exc}")
        sys.exit(1)

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
            balance = exchange.fetch_balance()
            totals = balance.get("total", {}) or {}
            non_zero = {asset: amount for asset, amount in totals.items() if amount}
            print(f"Auth OK. Non-zero balances: {non_zero or '(none)'}")
        except Exception as exc:
            print(f"Auth check failed (expected if your testnet keys aren't set yet): {exc}")
    else:
        print("\nNo API key set in .env — skipping authenticated balance check.")
        print("(Public market data works fine without keys.)")


def run_trading_cycle(exchange=None) -> dict:
    """Fetch the latest signal and, if it differs from the account's
    current position, place a single Testnet order. Returns a dict
    describing what happened, and also prints it. Dispatches to the
    EMA or Setup Engine cycle per config.USE_SETUP_ENGINE (see the
    module docstring)."""
    exchange = exchange or get_exchange()
    if USE_SETUP_ENGINE:
        return _run_setup_engine_cycle(exchange)
    return _run_ema_cycle(exchange)


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
        place_market_order,
        place_stop_loss_order,
    )
    from risk_manager import position_size, stop_loss_price

    df = fetch_ohlcv(exchange, symbol=SYMBOL, timeframe=TIMEFRAME, limit=SLOW_PERIOD * 3)
    data = add_signals(df)
    latest = data.iloc[-1]
    price = float(latest["close"])
    signal = int(latest["signal"])

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

    entry_blocked_by_pattern = False
    daily_loss_limit_hit = False
    if signal == 1 and not in_position:
        if USE_PATTERN_FILTER:
            pattern_signal = detect_reversal_patterns(data)
            entry_blocked_by_pattern = bool(
                bearish_veto_mask(pattern_signal, PATTERN_VETO_LOOKBACK).iloc[-1]
            )
        quote_balance = get_quote_asset_balance(exchange, SYMBOL)
        daily_loss_limit_hit = _daily_loss_limit_hit(quote_balance + total_balance * price)

    if signal == 1 and not in_position and not entry_blocked_by_pattern and not daily_loss_limit_hit:
        size = position_size(quote_balance, price)
        if size > 0:
            order = place_market_order(exchange, SYMBOL, "buy", size)
            action = "buy"
            entry_price = get_average_fill_price(order) or price
            filled_amount = float(order.get("filled") or size)
            stop_order = place_stop_loss_order(
                exchange, SYMBOL, filled_amount, stop_loss_price(entry_price)
            )
    elif signal == 1 and not in_position and entry_blocked_by_pattern:
        action = "entry_blocked_by_pattern"
    elif signal == 1 and not in_position and daily_loss_limit_hit:
        action = "entry_blocked_by_daily_loss_limit"
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
        stop_order = place_stop_loss_order(
            exchange, SYMBOL, total_balance, stop_loss_price(entry_price)
        )
        action = "stop_loss_replaced"

    result = {
        "timestamp": str(latest.name),
        "price": price,
        "signal": signal,
        "in_position_before": in_position,
        "action": action,
        "order_id": order.get("id") if order else None,
        "stop_order_id": stop_order.get("id") if stop_order else None,
    }
    logger.info(result)
    return result


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
        place_market_order,
        place_stop_loss_order,
    )
    from risk_manager import position_size, stop_loss_price

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

    daily_loss_limit_hit = False
    if long_setup is not None and not in_position and not context.no_trade:
        quote_balance = get_quote_asset_balance(exchange, SYMBOL)
        daily_loss_limit_hit = _daily_loss_limit_hit(quote_balance + total_balance * price)

    if long_setup is not None and not in_position and not context.no_trade and not daily_loss_limit_hit:
        stop_reference = long_setup.invalidation.level
        if stop_reference is None or stop_reference >= price:
            stop_reference = stop_loss_price(price)  # degenerate level: fall back to the flat %
        size = position_size(quote_balance, price, stop_price=stop_reference)
        if size > 0:
            order = place_market_order(exchange, SYMBOL, "buy", size)
            action = "buy"
            filled_amount = float(order.get("filled") or size)
            stop_order = place_stop_loss_order(exchange, SYMBOL, filled_amount, stop_reference)
    elif long_setup is not None and not in_position and not context.no_trade and daily_loss_limit_hit:
        action = "entry_blocked_by_daily_loss_limit"
    elif in_position and (context.no_trade or not bullish_bias or bearish_pattern):
        # The bias that justified this position is gone, context says
        # not to trade at all right now, or a bearish pattern just
        # confirmed — cancel the protective stop first so it doesn't
        # compete with this market sell for the same (currently locked)
        # balance.
        for stale_order in get_open_stop_loss_orders(exchange, SYMBOL):
            cancel_order(exchange, SYMBOL, stale_order["id"])
        free_balance = get_base_asset_balance(exchange, SYMBOL)
        order = place_market_order(exchange, SYMBOL, "sell", free_balance)
        action = "sell"
    elif in_position and not get_open_stop_loss_orders(exchange, SYMBOL):
        # Self-heal, same idea as the EMA cycle: reconstruct a missing
        # stop from the current context's invalidation level rather
        # than leaving the position unprotected.
        stop_reference = context.invalidation.level
        if stop_reference is None or stop_reference >= price:
            stop_reference = stop_loss_price(price)
        stop_order = place_stop_loss_order(exchange, SYMBOL, total_balance, stop_reference)
        action = "stop_loss_replaced"

    result = {
        "timestamp": str(execution.index[-1]),
        "price": price,
        "market_state": context.market_state.value,
        "bias": context.bias.direction.value,
        "in_position_before": in_position,
        "action": action,
        "order_id": order.get("id") if order else None,
        "stop_order_id": stop_order.get("id") if stop_order else None,
    }
    logger.info(result)
    return result


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
            sys.exit(1)
    else:
        check_connection()


if __name__ == "__main__":
    main()
