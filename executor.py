"""Order execution against Binance.

Not implemented yet, and intentionally so: no order should be placed,
not even on Testnet, before the strategy has been backtested
(backtester.py) and risk checks are in place (risk_manager.py).

Safety rule for whoever implements this: every function here must
assert `config.USE_TESTNET is True` before calling any order-placing
ccxt method, for as long as this bot is unproven. Removing that guard
is a deliberate, explicit decision for later — never a side effect of
another change.
"""

# TODO(phase 3+): implement Testnet-only order placement, guarded by config.USE_TESTNET.
