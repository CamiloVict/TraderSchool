"""Fetch OHLC market data from OANDA's v20 REST API -- the Forex
equivalent of data_fetcher.py, but for a genuinely different kind of
broker (see README's Forex section for why OANDA specifically: a plain
HTTPS REST API needs nothing running locally besides this script,
unlike MetaTrader/Interactive Brokers which need a terminal process
alive at all times -- the only option that fits this repo's existing
headless-cron architecture without adding new infrastructure).

Read-only market data only, same Phase 1 scope data_fetcher.py started
with. No orders, no account/balance calls -- those need
OANDA_ACCOUNT_ID and a real order-placement module this repo doesn't
have yet (see README).

**Not verified against a real OANDA account yet** -- there is no
network access to anything outside Binance's public API from this
development sandbox, so this is written directly from OANDA's public
v20 REST API documentation (stable, widely used) rather than tested
end-to-end. Before trusting this for anything, run it once against
your own practice account (see README's Forex section for the exact
command) and treat any mismatch with what actually comes back as a bug
in this file, not in the docs it was written from.
"""
import time

import pandas as pd
import requests

from forex_config import OANDA_API_TOKEN, OANDA_ENVIRONMENT

_BASE_URLS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}

# OANDA's own candlestick granularity codes (v20 API): seconds (S5..S30),
# minutes (M1..M30), hours (H1..H12), and D/W/M for day/week/month.
# Listed here only so a typo in FOREX_TIMEFRAME fails fast and locally
# instead of as an opaque 400 from OANDA.
GRANULARITIES = (
    "S5", "S10", "S15", "S30",
    "M1", "M2", "M4", "M5", "M10", "M15", "M30",
    "H1", "H2", "H3", "H4", "H6", "H8", "H12",
    "D", "W", "M",
)

MAX_CANDLES_PER_REQUEST = 5000  # OANDA's own documented cap on `count`.

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 1.0


def _call_with_retries(func, *args, max_attempts: int = DEFAULT_MAX_ATTEMPTS, base_delay: float = DEFAULT_BASE_DELAY_SECONDS, **kwargs):
    """Same exponential-backoff idea as retry.call_with_retries, but for
    `requests`' own transient-failure exceptions instead of ccxt's --
    kept separate rather than generalizing that module, since its own
    docstring is specifically about ccxt's NetworkError/ExchangeError
    split and this repo would rather not risk regressing a working,
    already-tested module for a feature that can't be tested end-to-end
    yet anyway (see this module's own docstring).

    Retries connection/timeout failures (the request may never have
    reached OANDA) but never an HTTP error response (a 4xx/5xx means
    OANDA *did* answer, just not with success -- retrying blindly only
    delays reporting a real problem, e.g. a bad token or bad params).
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return func(*args, **kwargs)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            if attempt >= max_attempts:
                raise
            time.sleep(base_delay * (2 ** (attempt - 1)))


def _base_url() -> str:
    if OANDA_ENVIRONMENT not in _BASE_URLS:
        raise ValueError(
            f"OANDA_ENVIRONMENT={OANDA_ENVIRONMENT!r} is not 'practice' or 'live' -- check .env"
        )
    return _BASE_URLS[OANDA_ENVIRONMENT]


def _require_token() -> str:
    if not OANDA_API_TOKEN:
        raise RuntimeError(
            "OANDA_API_TOKEN is not set -- see README's Forex section for how to "
            "generate one from your OANDA practice account before calling this."
        )
    return OANDA_API_TOKEN


def _candles_to_dataframe(candles: list, price: str) -> pd.DataFrame:
    price_key = {"M": "mid", "B": "bid", "A": "ask"}[price]
    rows = []
    index = []
    for candle in candles:
        # OANDA includes the still-forming current candle in a live
        # request with complete: false -- same reason ccxt's own feed
        # would never be trusted mid-candle; skip it rather than treat
        # a partial bar as a closed one.
        if not candle.get("complete", True):
            continue
        ohlc = candle[price_key]
        index.append(pd.Timestamp(candle["time"]))
        rows.append(
            {
                "open": float(ohlc["o"]),
                "high": float(ohlc["h"]),
                "low": float(ohlc["l"]),
                "close": float(ohlc["c"]),
                "volume": float(candle.get("volume", 0)),
            }
        )
    return pd.DataFrame(rows, index=pd.DatetimeIndex(index, name="timestamp"))


def fetch_candles(
    instrument: str,
    granularity: str = "H1",
    count: int = 500,
    price: str = "M",
) -> pd.DataFrame:
    """Fetch up to `count` (max 5000, OANDA's own cap) most recent
    candles for `instrument` (e.g. "XAU_USD") as a DataFrame indexed by
    time -- the Forex equivalent of data_fetcher.fetch_ohlcv().

    `price`: "M" (midpoint, default), "B" (bid), or "A" (ask). Backtests
    and signal generation should use the midpoint, same as how a
    Binance OHLCV candle is already a single trade-price series with no
    separate bid/ask -- comparing strategies built on PAXG (midpoint-
    like, one price) against Forex needs the same convention on both
    sides, not bid on one and ask on the other.
    """
    if granularity not in GRANULARITIES:
        raise ValueError(f"granularity={granularity!r} is not one of OANDA's own codes: {GRANULARITIES}")
    if count > MAX_CANDLES_PER_REQUEST:
        raise ValueError(f"count={count} exceeds OANDA's own {MAX_CANDLES_PER_REQUEST}-candle cap per request")

    token = _require_token()
    url = f"{_base_url()}/v3/instruments/{instrument}/candles"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"granularity": granularity, "count": count, "price": price}

    response = _call_with_retries(requests.get, url, headers=headers, params=params, timeout=15)
    response.raise_for_status()
    payload = response.json()
    return _candles_to_dataframe(payload["candles"], price=price)


def fetch_candles_history(
    instrument: str,
    since,
    granularity: str = "H1",
    price: str = "M",
) -> pd.DataFrame:
    """Page through OANDA's candles endpoint from `since` (a
    pandas-parseable timestamp) to now, for a longer backtest history
    than one `fetch_candles()` call's cap allows -- the Forex
    equivalent of data_fetcher.fetch_ohlcv_history().

    OANDA's `from`/`to` pagination is RFC3339-timestamp-based rather
    than Binance's since-in-milliseconds-plus-limit, so this walks
    forward MAX_CANDLES_PER_REQUEST candles' worth of time at a time
    (estimated from `granularity`) instead of reusing
    data_fetcher_history's own cursor logic verbatim.
    """
    if granularity not in GRANULARITIES:
        raise ValueError(f"granularity={granularity!r} is not one of OANDA's own codes: {GRANULARITIES}")

    token = _require_token()
    url = f"{_base_url()}/v3/instruments/{instrument}/candles"
    headers = {"Authorization": f"Bearer {token}"}

    cursor = pd.Timestamp(since)
    if cursor.tz is None:
        cursor = cursor.tz_localize("UTC")
    now = pd.Timestamp.now(tz="UTC")

    frames = []
    while cursor < now:
        params = {
            "granularity": granularity,
            "price": price,
            "from": cursor.isoformat(),
            "count": MAX_CANDLES_PER_REQUEST,
        }
        response = _call_with_retries(requests.get, url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        candles = response.json()["candles"]
        if not candles:
            break

        frame = _candles_to_dataframe(candles, price=price)
        if frame.empty:
            break
        frames.append(frame)

        last_time = frame.index[-1]
        if last_time <= cursor:
            break  # OANDA returned nothing newer than what we already have -- avoid spinning forever
        cursor = last_time + pd.Timedelta(seconds=1)

        if len(candles) < MAX_CANDLES_PER_REQUEST:
            break  # short page -- we've caught up to `now`

    if not frames:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    combined = pd.concat(frames)
    return combined[~combined.index.duplicated(keep="first")]
