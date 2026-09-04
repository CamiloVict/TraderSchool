"""Data quality gate.

Runs before anything reads a price. The distinction that matters is
between problems that make the data *wrong* and problems that make it
*incomplete*:

  - FATAL (duplicate timestamps, high < low, non-monotonic index):
    the series is internally contradictory. Any structure computed on
    it is fiction, so the engine refuses to produce a snapshot.

  - WARNING (a few missing candles, an unusual gap, missing volume):
    the series is usable but thinner than assumed. The snapshot is
    still produced and stamped `degraded`, so a reader can discount it
    rather than being handed a confident-looking answer built on
    swiss cheese.

Master prompt section 29: never run signals on corrupt data.
"""
import pandas as pd

from context_engine.params import (
    GAP_ATR_MULTIPLE,
    MAX_MISSING_CANDLE_RATIO,
    MIN_CANDLES_PER_TIMEFRAME,
    TIMEFRAME_MINUTES,
)
from context_engine.schema import DataIssue, DataQuality, Severity

REQUIRED_COLUMNS = ("open", "high", "low", "close")


class DataValidationError(ValueError):
    """Raised when a frame is too broken to analyze."""

    def __init__(self, issues: list):
        self.issues = issues
        detail = "; ".join(f"[{i.timeframe}] {i.code}: {i.detail}" for i in issues)
        super().__init__(f"data validation failed: {detail}")


def validate_frame(df: pd.DataFrame, timeframe: str) -> list:
    """Return the list of DataIssues found in one timeframe's candles."""
    issues = []

    if df is None or df.empty:
        return [
            DataIssue(
                code="NO_DATA",
                severity=Severity.FATAL,
                timeframe=timeframe,
                detail="no candles supplied",
            )
        ]

    missing_columns = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_columns:
        return [
            DataIssue(
                code="MISSING_COLUMNS",
                severity=Severity.FATAL,
                timeframe=timeframe,
                detail=f"missing {', '.join(missing_columns)}",
            )
        ]

    issues.extend(_check_index(df, timeframe))
    issues.extend(_check_ohlc_consistency(df, timeframe))
    issues.extend(_check_missing_candles(df, timeframe))
    issues.extend(_check_gaps(df, timeframe))
    issues.extend(_check_volume(df, timeframe))
    issues.extend(_check_length(df, timeframe))
    return issues


def validate_frames(frames: dict) -> DataQuality:
    """Validate every timeframe and fold the result into one verdict."""
    if not frames:
        return DataQuality(
            valid=False,
            degraded=True,
            issues=[
                DataIssue(
                    code="NO_TIMEFRAMES",
                    severity=Severity.FATAL,
                    timeframe="-",
                    detail="no timeframes supplied",
                )
            ],
        )

    issues = []
    for timeframe, df in frames.items():
        issues.extend(validate_frame(df, timeframe))

    fatal = [i for i in issues if i.severity is Severity.FATAL]
    return DataQuality(valid=not fatal, degraded=bool(issues), issues=issues)


def assert_valid(quality: DataQuality) -> None:
    """Raise if any FATAL issue was found."""
    fatal = [i for i in quality.issues if i.severity is Severity.FATAL]
    if fatal:
        raise DataValidationError(fatal)


def _check_index(df: pd.DataFrame, timeframe: str) -> list:
    issues = []
    if not isinstance(df.index, pd.DatetimeIndex):
        return [
            DataIssue(
                code="INDEX_NOT_DATETIME",
                severity=Severity.FATAL,
                timeframe=timeframe,
                detail=f"index is {type(df.index).__name__}",
            )
        ]

    duplicated = int(df.index.duplicated().sum())
    if duplicated:
        issues.append(
            DataIssue(
                code="DUPLICATE_TIMESTAMPS",
                severity=Severity.FATAL,
                timeframe=timeframe,
                detail=f"{duplicated} duplicated timestamp(s)",
            )
        )

    if not df.index.is_monotonic_increasing:
        issues.append(
            DataIssue(
                code="INDEX_NOT_SORTED",
                severity=Severity.FATAL,
                timeframe=timeframe,
                detail="timestamps are not in ascending order",
            )
        )

    if df.index.tz is None:
        # Recoverable by ensure_utc(), but worth surfacing: a naive
        # index means session bucketing could be silently offset.
        issues.append(
            DataIssue(
                code="NAIVE_TIMESTAMPS",
                severity=Severity.WARNING,
                timeframe=timeframe,
                detail="timestamps carry no timezone; assuming UTC",
            )
        )
    return issues


def _check_ohlc_consistency(df: pd.DataFrame, timeframe: str) -> list:
    issues = []

    inverted = df["high"] < df["low"]
    if bool(inverted.any()):
        issues.append(
            DataIssue(
                code="HIGH_BELOW_LOW",
                severity=Severity.FATAL,
                timeframe=timeframe,
                detail=f"{int(inverted.sum())} candle(s) with high < low",
            )
        )

    body_max = df[["open", "close"]].max(axis=1)
    body_min = df[["open", "close"]].min(axis=1)
    high_too_low = df["high"] < body_max
    low_too_high = df["low"] > body_min
    if bool(high_too_low.any()) or bool(low_too_high.any()):
        count = int(high_too_low.sum() + low_too_high.sum())
        issues.append(
            DataIssue(
                code="OHLC_INCONSISTENT",
                severity=Severity.FATAL,
                timeframe=timeframe,
                detail=f"{count} candle(s) where high/low do not contain open/close",
            )
        )

    non_positive = (df[list(REQUIRED_COLUMNS)] <= 0).any(axis=1)
    if bool(non_positive.any()):
        issues.append(
            DataIssue(
                code="NON_POSITIVE_PRICE",
                severity=Severity.FATAL,
                timeframe=timeframe,
                detail=f"{int(non_positive.sum())} candle(s) with a price <= 0",
            )
        )

    nan_rows = df[list(REQUIRED_COLUMNS)].isna().any(axis=1)
    if bool(nan_rows.any()):
        issues.append(
            DataIssue(
                code="NAN_PRICE",
                severity=Severity.FATAL,
                timeframe=timeframe,
                detail=f"{int(nan_rows.sum())} candle(s) with a NaN price",
            )
        )
    return issues


def _check_missing_candles(df: pd.DataFrame, timeframe: str) -> list:
    minutes = TIMEFRAME_MINUTES.get(timeframe)
    # Weekly bars land on irregular calendar boundaries; a fixed-step
    # grid would report phantom holes, so skip the check there.
    if minutes is None or timeframe == "1w" or len(df) < 2:
        return []

    step = pd.Timedelta(minutes=minutes)
    expected = pd.date_range(start=df.index[0], end=df.index[-1], freq=step)
    missing = len(expected.difference(df.index))
    if not missing:
        return []

    ratio = missing / len(expected)
    severity = Severity.WARNING if ratio <= MAX_MISSING_CANDLE_RATIO else Severity.FATAL
    return [
        DataIssue(
            code="MISSING_CANDLES",
            severity=severity,
            timeframe=timeframe,
            detail=f"{missing} of {len(expected)} expected candles missing ({ratio:.2%})",
        )
    ]


def _check_gaps(df: pd.DataFrame, timeframe: str) -> list:
    if len(df) < 3:
        return []

    move = (df["close"] - df["close"].shift()).abs()
    # Median true range rather than a full ATR: this runs before the
    # feature layer, and the median resists the very outliers being
    # hunted for here.
    typical = float((df["high"] - df["low"]).median())
    if typical <= 0:
        return []

    threshold = typical * GAP_ATR_MULTIPLE
    outliers = move > threshold
    if not bool(outliers.any()):
        return []

    worst = float(move.max())
    return [
        DataIssue(
            code="ANOMALOUS_GAP",
            severity=Severity.WARNING,
            timeframe=timeframe,
            detail=(
                f"{int(outliers.sum())} close-to-close move(s) above "
                f"{GAP_ATR_MULTIPLE}x the typical range (largest {worst:.2f})"
            ),
        )
    ]


def _check_volume(df: pd.DataFrame, timeframe: str) -> list:
    if "volume" not in df.columns:
        return [
            DataIssue(
                code="NO_VOLUME",
                severity=Severity.WARNING,
                timeframe=timeframe,
                detail="no volume column; volume confirmation is unavailable",
            )
        ]

    negative = df["volume"] < 0
    if bool(negative.any()):
        return [
            DataIssue(
                code="NEGATIVE_VOLUME",
                severity=Severity.FATAL,
                timeframe=timeframe,
                detail=f"{int(negative.sum())} candle(s) with negative volume",
            )
        ]
    return []


def _check_length(df: pd.DataFrame, timeframe: str) -> list:
    if len(df) >= MIN_CANDLES_PER_TIMEFRAME:
        return []
    return [
        DataIssue(
            code="SHORT_HISTORY",
            severity=Severity.WARNING,
            timeframe=timeframe,
            detail=f"only {len(df)} candles (want >= {MIN_CANDLES_PER_TIMEFRAME})",
        )
    ]
