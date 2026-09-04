"""Trading sessions: Asia, London, New York.

Sessions matter because participation is not uniform across the day.
The London open regularly sweeps the Asian range; New York either
extends that move or reverses it. Knowing which session is live is
part of knowing what kind of move to expect.

Known simplification: the windows below are fixed UTC hours. Real
London and New York hours shift with daylight saving, so for part of
the year these boundaries are an hour off. Doing it properly means
localizing to Europe/London and America/New_York and deriving the UTC
window per date; that is deferred, and this is the one assumption in
the engine that is knowingly approximate.

London and New York overlap (12:00-16:00 UTC), which is the highest
participation window of the day. When sessions overlap the later one
wins, since it is the incoming flow that is driving.
"""
import pandas as pd

from context_engine.params import SESSION_WINDOWS
from context_engine.schema import SessionState

# Checked in reverse order so an overlapping hour resolves to the
# session that opened most recently.
SESSION_PRIORITY = ("NEW_YORK", "LONDON", "ASIA")


def session_for(timestamp) -> str:
    """Session containing `timestamp`, or None outside all windows."""
    stamp = pd.Timestamp(timestamp)
    if stamp.tz is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")

    hour = stamp.hour
    for name in SESSION_PRIORITY:
        start, end = SESSION_WINDOWS[name]
        if start <= hour < end:
            return name
    return None


def label_sessions(df: pd.DataFrame) -> pd.Series:
    """Session name per candle (None outside every window)."""
    return pd.Series([session_for(ts) for ts in df.index], index=df.index, dtype=object)


def session_bounds(df: pd.DataFrame, name: str, day) -> tuple:
    """(high, low) of session `name` on the UTC date `day`."""
    start, end = SESSION_WINDOWS[name]
    date = pd.Timestamp(day).tz_convert("UTC").normalize()
    window = df[(df.index >= date + pd.Timedelta(hours=start)) & (df.index < date + pd.Timedelta(hours=end))]
    if window.empty:
        return None, None
    return float(window["high"].max()), float(window["low"].min())


def analyze_sessions(df: pd.DataFrame) -> SessionState:
    """Current session and its range, plus the previous session's.

    Only candles up to the last one in `df` are considered, so the
    "current" session range is the range *so far* — it keeps growing
    while the session is live, which is the honest reading.
    """
    if df is None or df.empty:
        return SessionState(
            current=None,
            high=None,
            low=None,
            range=None,
            previous=None,
            previous_high=None,
            previous_low=None,
        )

    labels = label_sessions(df)
    last_timestamp = df.index[-1]
    current = labels.iloc[-1]

    current_high = current_low = current_range = None
    if current is not None:
        # Walk back only through the contiguous run of this session, so
        # yesterday's London does not merge into today's.
        run = _contiguous_run(labels, current)
        window = df.loc[run]
        current_high = float(window["high"].max())
        current_low = float(window["low"].min())
        current_range = round(current_high - current_low, 8)

    previous_name = _previous_session(current)
    previous_high, previous_low = (None, None)
    if previous_name is not None:
        day = last_timestamp
        # Asia opens the UTC day, so the session before it belongs to
        # the previous calendar day.
        if current == "ASIA" or (current is None and last_timestamp.hour < SESSION_WINDOWS["ASIA"][1]):
            day = last_timestamp - pd.Timedelta(days=1)
        previous_high, previous_low = session_bounds(df, previous_name, day)

    return SessionState(
        current=current,
        high=current_high,
        low=current_low,
        range=current_range,
        previous=previous_name,
        previous_high=previous_high,
        previous_low=previous_low,
    )


def _contiguous_run(labels: pd.Series, name: str):
    """Index of the unbroken tail of candles labelled `name`."""
    values = labels.to_numpy()
    end = len(values)
    start = end
    while start > 0 and values[start - 1] == name:
        start -= 1
    return labels.index[start:end]


def _previous_session(current: str) -> str:
    """Session that ran before `current` in the daily rotation."""
    rotation = ("ASIA", "LONDON", "NEW_YORK")
    if current in rotation:
        return rotation[rotation.index(current) - 1]
    # The only gap in the rotation is after New York closes and before
    # Asia opens, so the most recent session is always New York.
    return "NEW_YORK"
