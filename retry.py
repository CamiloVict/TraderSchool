"""Retry-with-backoff for transient exchange read failures.

Only `ccxt.NetworkError` (and its subclasses — RequestTimeout,
ExchangeNotAvailable, DDoSProtection, ...) is retried: those mean "the
request may not have reached the exchange at all, or its response got
lost," which a second attempt can plausibly fix. `ccxt.ExchangeError`
(BadSymbol, InsufficientFunds, InvalidOrder, AuthenticationError, ...)
means the request itself was wrong — retrying it just delays reporting
a real problem.

Deliberately not used for order placement (place_market_order,
place_stop_loss_order in executor.py): a NetworkError there means the
client never saw the response, but the order may have reached Binance
and filled anyway. Blindly retrying risks placing it twice. Those
functions protect against that failure mode differently — a
deterministic `newClientOrderId` per symbol/side/hour so Binance itself
rejects an accidental duplicate submission — rather than by retrying.
"""
import time

import ccxt

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 1.0


def call_with_retries(func, *args, max_attempts: int = DEFAULT_MAX_ATTEMPTS, base_delay: float = DEFAULT_BASE_DELAY_SECONDS, **kwargs):
    """Call `func(*args, **kwargs)`, retrying on ccxt.NetworkError with
    exponential backoff (base_delay, base_delay*2, base_delay*4, ...).
    Any other exception propagates immediately, as does a NetworkError
    once `max_attempts` is exhausted.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return func(*args, **kwargs)
        except ccxt.NetworkError:
            if attempt >= max_attempts:
                raise
            time.sleep(base_delay * (2 ** (attempt - 1)))
