"""UTC, half-open, three-hour windows and CAM7 CF time coordinates.

Ported from NCAR/CERES-SARB-CAM7 ``sp1_aod_pipeline/aggregate.py``
and the time constants/conversion in ``sp1_aod_pipeline/contract.py``.
Naive datetimes denote UTC; aware datetimes are converted to UTC first.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
from numpy.typing import ArrayLike, NDArray

TIMES_PER_DAY = 8
WINDOW_LENGTH_SEC = 10800
WINDOW_SECONDS = tuple(i * WINDOW_LENGTH_SEC for i in range(TIMES_PER_DAY))
TIME_UNITS = "hours since 2000-01-01 00:00:00 UTC"
TIME_CALENDAR = "gregorian"
EPOCH = datetime(2000, 1, 1)
UNIX_EPOCH = np.datetime64("1970-01-01T00:00:00", "ns")


def _utc_naive(value: datetime) -> datetime:
    if value.utcoffset() is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(tzinfo=None)


def _day_start(day: date) -> datetime:
    if isinstance(day, datetime):
        day = _utc_naive(day).date()
    return datetime(day.year, day.month, day.day)


def window_bounds(day: date, idx: int) -> tuple[datetime, datetime, datetime]:
    """Return UTC ``(start, end, center)`` for daily window 0 through 7."""
    if isinstance(idx, bool) or not isinstance(idx, (int, np.integer)) or not 0 <= idx < 8:
        raise ValueError("window index must be an integer from 0 through 7")
    start = _day_start(day) + timedelta(seconds=WINDOW_SECONDS[idx])
    return (
        start,
        start + timedelta(seconds=WINDOW_LENGTH_SEC),
        start + timedelta(seconds=WINDOW_LENGTH_SEC // 2),
    )


def window_index(time: datetime) -> int:
    """Index within the timestamp's UTC day; exact boundaries start a window."""
    value = _utc_naive(time)
    seconds = value.hour * 3600 + value.minute * 60 + value.second
    return seconds // WINDOW_LENGTH_SEC


def pixel_window_mask(times: ArrayLike, start: datetime, end: datetime) -> NDArray[np.bool_]:
    """Select finite UTC timestamps in ``[start, end)``."""
    values = np.asarray(times, dtype="datetime64[ns]")
    start64 = np.datetime64(_utc_naive(start), "ns")
    end64 = np.datetime64(_utc_naive(end), "ns")
    if end64 <= start64:
        raise ValueError("window end must be after its start")
    return ~np.isnat(values) & (values >= start64) & (values < end64)


def enumerate_windows(start_date: date, end_date: date) -> list[tuple[date, int]]:
    """Return all daily windows, including both dates."""
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    out: list[tuple[date, int]] = []
    day = start_date
    while day <= end_date:
        out.extend((day, idx) for idx in range(TIMES_PER_DAY))
        day += timedelta(days=1)
    return out


def day_time_edges(day: date) -> NDArray[np.datetime64]:
    """Nine UTC boundaries, with midnight of the next day as the last edge."""
    seconds = np.arange(TIMES_PER_DAY + 1, dtype=np.int64) * WINDOW_LENGTH_SEC
    return np.datetime64(_day_start(day), "ns") + seconds.astype("timedelta64[s]")


def day_time_edges_seconds(day: date) -> NDArray[np.float64]:
    """Daily UTC window edges in Unix seconds for the numeric binning kernels."""
    return np.asarray((day_time_edges(day) - UNIX_EPOCH) / np.timedelta64(1, "s"))


def seconds_since_day(times: ArrayLike, day: date) -> NDArray[np.float64]:
    """Convert UTC timestamps to seconds from day-start, mapping NaT to NaN."""
    values = np.asarray(times, dtype="datetime64[ns]")
    return np.asarray((values - np.datetime64(_day_start(day), "ns")) / np.timedelta64(1, "s"))


def cf_hours(value: datetime) -> float:
    """Hours in the CAM7 contract's Gregorian calendar and CF epoch."""
    return (_utc_naive(value) - EPOCH).total_seconds() / 3600.0
