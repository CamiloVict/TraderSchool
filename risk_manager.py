"""Risk management: position sizing, stop-loss, daily loss limits.

Not implemented yet. Will consume the RISK_PER_TRADE_PCT,
STOP_LOSS_PCT, TAKE_PROFIT_PCT and MAX_DAILY_LOSS_PCT settings from
config.py to size positions and to veto trades once a daily loss limit
is hit. Required before executor.py is allowed to place any order,
including on Testnet.
"""

# TODO(phase 3): implement position sizing + stop-loss/take-profit + daily limit checks.
