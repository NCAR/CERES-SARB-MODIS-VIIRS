"""TAI93 seconds to UTC for aerosol retrieval times.

The epoch-plus-nanoseconds conversion is adapted from NCAR/CERES-SARB-CAM7
``sp1_aod_pipeline/records.py``. Unlike that reference, leap seconds inserted
since 1993-01-01 are applied by default. ``ignore`` reproduces its legacy clock.

The table is complete through 2017-01-01 and must be extended if a new leap
second is announced. Python/NumPy's UTC representation cannot express an
inserted 23:59:60: those one-second intervals return NaT (or NaN for Unix
seconds), avoiding assignment to the wrong day. Negative/fill, nonfinite,
and out-of-range values also return missing times. Times before 1993-01-01
are outside this satellite-product conversion's supported range.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
from numpy.typing import ArrayLike, NDArray

LeapSeconds = Literal["apply", "ignore"]
LEAP_SECOND_DATES = (
    "1993-07-01",
    "1994-07-01",
    "1996-01-01",
    "1997-07-01",
    "1999-01-01",
    "2006-01-01",
    "2009-01-01",
    "2012-07-01",
    "2015-07-01",
    "2017-01-01",
)
_EPOCH93 = np.datetime64("1993-01-01T00:00:00", "ns")
_UNIX_EPOCH = np.datetime64("1970-01-01T00:00:00", "ns")
_EPOCH93_UNIX_SECONDS = float((_EPOCH93 - _UNIX_EPOCH) / np.timedelta64(1, "s"))
_MAX_DELTA_NS = np.iinfo(np.int64).max - int(_EPOCH93.astype(np.int64))
# This coarse input limit makes the float-to-int conversion safe; the exact
# datetime64 upper limit is checked in integer nanoseconds below.
_MAX_INPUT_SECONDS = _MAX_DELTA_NS // 1_000_000_000 + len(LEAP_SECOND_DATES) + 1
_UTC_TRANSITIONS = (
    np.asarray(LEAP_SECOND_DATES, dtype="datetime64[ns]") - _EPOCH93
) / np.timedelta64(1, "s")
_TAI_TRANSITIONS = _UTC_TRANSITIONS + np.arange(1, len(LEAP_SECOND_DATES) + 1)


def _utc_nanoseconds(seconds: ArrayLike, leap_seconds: LeapSeconds) -> NDArray[np.int64]:
    if leap_seconds not in ("apply", "ignore"):
        raise ValueError("time.leap_seconds must be 'apply' or 'ignore'")
    values = np.asarray(seconds, dtype=np.float64)
    missing = np.iinfo(np.int64).min
    out = np.full(values.shape, missing, dtype=np.int64)
    valid = np.isfinite(values) & (values >= 0) & (values < _MAX_INPUT_SECONDS)
    selected = values[valid]
    if leap_seconds == "apply":
        count = np.searchsorted(_TAI_TRANSITIONS, selected, side="right")
        next_transition = _TAI_TRANSITIONS[np.minimum(count, len(_TAI_TRANSITIONS) - 1)]
        in_leap_second = (count < len(_TAI_TRANSITIONS)) & (selected >= next_transition - 1)
    else:
        count = np.zeros(selected.shape, dtype=np.int64)
        in_leap_second = np.zeros(selected.shape, dtype=bool)
    # Quantize before subtracting the integer leap offset, retaining the CAM7
    # rounding convention for fractional scan times such as 489110405.94.
    delta = np.rint(selected * 1_000_000_000.0).astype(np.int64) - count * 1_000_000_000
    good = ~in_leap_second & (delta <= _MAX_DELTA_NS)
    converted = np.full(selected.shape, missing, dtype=np.int64)
    converted[good] = delta[good]
    out[valid] = converted
    return out


def tai93_to_datetime64(
    seconds: ArrayLike, *, leap_seconds: LeapSeconds = "apply"
) -> NDArray[np.datetime64]:
    """Return UTC ``datetime64[ns]``, preserving the input's shape.

    Array scalars return a zero-dimensional array. Input float precision limits
    subsecond accuracy; integer seconds and the leap boundaries are exact.
    """
    delta = _utc_nanoseconds(seconds, leap_seconds)
    out = np.full(delta.shape, np.datetime64("NaT"), dtype="datetime64[ns]")
    valid = delta != np.iinfo(np.int64).min
    out[valid] = _EPOCH93 + delta[valid].astype("timedelta64[ns]")
    return out


def tai93_to_unix_seconds(
    seconds: ArrayLike, *, leap_seconds: LeapSeconds = "apply"
) -> NDArray[np.float64]:
    """Return float64 UTC Unix seconds for binning, with NaN for missing times."""
    delta = _utc_nanoseconds(seconds, leap_seconds)
    out = np.full(delta.shape, np.nan, dtype=np.float64)
    valid = delta != np.iinfo(np.int64).min
    out[valid] = delta[valid] / 1_000_000_000.0 + _EPOCH93_UNIX_SECONDS
    return out
