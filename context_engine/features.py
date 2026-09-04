"""Numeric primitives shared by every engine downstream.

Pure functions over a Series/DataFrame, no classification and no
signals: deciding what an ATR of 125 *means* belongs to volatility.py,
and deciding whether to act on it belongs even further downstream.
Keeping the arithmetic here is what stops each engine from growing its
own slightly-different ATR.

Every function is causal — the value at row `i` depends only on rows
`<= i`. No centered windows, no `shift(-1)`, nothing that peeks.

Note on EMA: strategy.py computes its own EMAs for the existing
crossover backtest. It is deliberately left alone — rewiring it would
ripple into backtester.py, the exported dashboard JSON and two test
files for no behavioural gain. `ema()` here is the shared primitive
for everything new.
"""
import numpy as np
import pandas as pd

from context_engine.params import ATR_PERIOD, RSI_PERIOD


def ema(series: pd.Series, span: int) -> pd.Series:
    """Exponential moving average (`adjust=False`, the recursive form
    a live feed would produce bar by bar)."""
    return series.ewm(span=span, adjust=False).mean()


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    """Wilder's true range: the greater of the candle's own range and
    its distance from the previous close, so an opening gap counts as
    real movement instead of being invisible."""
    previous_close = df["close"].shift()
    candle_range = df["high"] - df["low"]
    gap_up = (df["high"] - previous_close).abs()
    gap_down = (df["low"] - previous_close).abs()
    return pd.concat([candle_range, gap_up, gap_down], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    """Average true range, Wilder-smoothed (`alpha = 1/period`)."""
    return true_range(df).ewm(alpha=1 / period, adjust=False).mean()


def atr_percent(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    """ATR as a percentage of price — the comparable form. A $125 ATR
    means something very different at $110k than at $2k."""
    return atr(df, period) / df["close"] * 100


def rsi(series: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """Wilder's RSI. Returns NaN until `period` deltas exist."""
    delta = series.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)

    avg_gain = gains.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1 / period, adjust=False).mean()

    # Blank the zero denominators rather than dividing into inf, then
    # fill the two degenerate cases explicitly: nothing but gains is
    # RSI 100, and a perfectly flat window has no momentum either way.
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    out = out.mask(avg_loss == 0, 100.0)
    out = out.mask((avg_gain == 0) & (avg_loss == 0), 50.0)
    out.iloc[:period] = np.nan
    return out


def typical_price(df: pd.DataFrame) -> pd.Series:
    return (df["high"] + df["low"] + df["close"]) / 3


def vwap(df: pd.DataFrame, reset: str = None) -> pd.Series:
    """Volume-weighted average price, cumulative from the start of the
    series or restarting each `reset` bucket ("D" for a daily VWAP).

    `reset` is a floor frequency, so buckets align to UTC boundaries
    (`to_period` would work too but discards the timezone).

    Falls back to a plain cumulative typical price when the frame has
    no volume, so a volume-less data source degrades instead of
    returning NaN and silently disabling everything built on it.
    """
    price = typical_price(df)
    if "volume" not in df.columns or df["volume"].sum() == 0:
        return price.expanding().mean()

    volume = df["volume"]
    if reset is None:
        return (price * volume).cumsum() / volume.cumsum().replace(0, np.nan)

    groups = df.index.floor(reset)
    weighted = (price * volume).groupby(groups).cumsum()
    weights = volume.groupby(groups).cumsum().replace(0, np.nan)
    return weighted / weights


def relative_volume(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Current volume over its own recent average. Above 1 means this
    candle drew more participation than usual."""
    if "volume" not in df.columns:
        return pd.Series(np.nan, index=df.index)
    average = df["volume"].rolling(window=window, min_periods=max(2, window // 2)).mean()
    return df["volume"] / average.replace(0, np.nan)


def candle_anatomy(df: pd.DataFrame) -> pd.DataFrame:
    """Per-candle body/wick geometry as fractions of the total range.

    The vocabulary the candlestick engine will need later, and already
    useful now: `body_percent` is how much of the range was directional
    conviction versus indecision.
    """
    candle_range = (df["high"] - df["low"]).replace(0, np.nan)
    body = (df["close"] - df["open"]).abs()
    upper_wick = df["high"] - df[["open", "close"]].max(axis=1)
    lower_wick = df[["open", "close"]].min(axis=1) - df["low"]

    return pd.DataFrame(
        {
            "range": df["high"] - df["low"],
            "body": body,
            "upper_wick": upper_wick,
            "lower_wick": lower_wick,
            "body_percent": body / candle_range * 100,
            "upper_wick_percent": upper_wick / candle_range * 100,
            "lower_wick_percent": lower_wick / candle_range * 100,
            "bullish": df["close"] > df["open"],
        },
        index=df.index,
    )


def is_displacement(df: pd.DataFrame, multiple: float, period: int = ATR_PERIOD) -> pd.Series:
    """True where the candle's body exceeds `multiple` x ATR.

    Displacement is the difference between price drifting back over a
    level and being driven through it. Measured on the body, not the
    range: a long wick is rejection, not commitment.
    """
    body = (df["close"] - df["open"]).abs()
    return body > (atr(df, period) * multiple)


def last_value(series: pd.Series, default: float = None):
    """Final non-NaN value of `series`, or `default` if there is none.

    Used all over the engine, where "not enough history yet" is a
    normal state that must not become a NaN leaking into the JSON.
    """
    if series is None or len(series) == 0:
        return default
    cleaned = series.dropna()
    if cleaned.empty:
        return default
    return float(cleaned.iloc[-1])
