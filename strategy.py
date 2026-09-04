"""Trading strategy logic.

Not implemented yet — out of scope for Phase 1, which only validates
exchange connectivity and the OHLCV data flow (see data_fetcher.py and
main.py).

Planned for Phase 2: a moving-average crossover (fast MA crosses above
slow MA -> long entry signal; crosses below -> exit signal). Whether to
use SMA or EMA, and which periods, are strategy decisions that should
be discussed and confirmed before implementation, then validated in
backtester.py before ever running against Testnet order execution.
"""

# TODO(phase 2): implement moving-average crossover signal generation.
