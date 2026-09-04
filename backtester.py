"""Backtesting engine.

Not implemented yet. Once strategy.py produces buy/sell signals, this
module will replay them against historical OHLCV data (via
data_fetcher.fetch_ohlcv_history) and report:
  - win rate
  - max drawdown
  - total / annualized return
  - number of trades, average trade duration

No strategy is allowed to reach executor.py (even on Testnet) without
first passing through here.
"""

# TODO(phase 2): implement historical simulation + metrics reporting.
