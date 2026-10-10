"""Half-open UTC windows and their exact CAM7 CF coordinates."""

from datetime import date, datetime, timedelta, timezone

import netCDF4
import numpy as np
import pytest

from sarb_aod.windows import (
    TIME_CALENDAR,
    TIME_UNITS,
    TIMES_PER_DAY,
    WINDOW_LENGTH_SEC,
    WINDOW_SECONDS,
    cf_hours,
    day_time_edges,
    day_time_edges_seconds,
    enumerate_windows,
    pixel_window_mask,
    seconds_since_day,
    window_bounds,
    window_index,
)


def test_window_bounds_cf_centers_and_all_daily_edges() -> None:
    day = date(2008, 7, 1)
    assert (TIMES_PER_DAY, WINDOW_LENGTH_SEC) == (8, 10800)
    assert WINDOW_SECONDS == (0, 10800, 21600, 32400, 43200, 54000, 64800, 75600)
    assert TIME_UNITS == "hours since 2000-01-01 00:00:00 UTC"
    assert TIME_CALENDAR == "gregorian"
    expected = np.arange("2008-07-01", "2008-07-02T03", dtype="datetime64[3h]").astype(
        "datetime64[ns]"
    )
    np.testing.assert_array_equal(day_time_edges(day), expected)
    np.testing.assert_array_equal(np.diff(day_time_edges_seconds(day)), np.full(8, 10800))
    for idx in range(8):
        start, end, center = window_bounds(day, idx)
        assert start == datetime(2008, 7, 1) + timedelta(hours=idx * 3)
        assert end - start == timedelta(hours=3)
        assert center - start == timedelta(minutes=90)
        assert window_index(start) == idx
        assert window_index(end - timedelta(microseconds=1)) == idx
        assert cf_hours(center) == netCDF4.date2num(center, TIME_UNITS, TIME_CALENDAR)
        assert cf_hours(end) - cf_hours(start) == 3.0
    assert cf_hours(window_bounds(date(2000, 1, 1), 0)[2]) == 1.5


def test_boundary_pixel_occurs_in_exactly_one_window_and_next_day() -> None:
    day = date(2008, 7, 1)
    times = np.array(
        ["2008-07-01T02:59:59.999999999", "2008-07-01T03", "2008-07-02T00", "NaT"],
        dtype="datetime64[ns]",
    )
    masks = np.array([pixel_window_mask(times, *window_bounds(day, idx)[:2]) for idx in range(8)])
    np.testing.assert_array_equal(masks.sum(axis=0), [1, 1, 0, 0])
    assert masks[0, 0]
    assert masks[1, 1]
    next_mask = pixel_window_mask(times, *window_bounds(date(2008, 7, 2), 0)[:2])
    np.testing.assert_array_equal(next_mask, [False, False, True, False])
    np.testing.assert_array_equal(
        seconds_since_day(times, day), [10799.999999999, 10800, 86400, np.nan]
    )


def test_aware_datetimes_are_converted_to_utc() -> None:
    local = datetime(2008, 7, 1, 23, 0, tzinfo=timezone(timedelta(hours=-4)))
    assert window_index(local) == 1  # 03:00 UTC on July 2.
    assert cf_hours(local) == cf_hours(datetime(2008, 7, 2, 3))
    assert window_bounds(local, 0)[0] == datetime(2008, 7, 2)
    times = np.array(
        ["2008-07-02T02:59", "2008-07-02T03:00", "2008-07-02T06:00"], dtype="datetime64[ns]"
    )
    np.testing.assert_array_equal(
        pixel_window_mask(times, local, local + timedelta(hours=3)), [False, True, False]
    )


@pytest.mark.parametrize("idx", [-1, 8, True, 0.5])
def test_invalid_window_indices(idx: int) -> None:
    with pytest.raises(ValueError, match="window index"):
        window_bounds(date(2008, 7, 1), idx)


def test_invalid_time_window() -> None:
    with pytest.raises(ValueError, match="end must be after"):
        pixel_window_mask([], datetime(2008, 7, 2), datetime(2008, 7, 1))


def test_enumerate_windows_includes_both_dates() -> None:
    windows = enumerate_windows(date(2008, 7, 1), date(2008, 7, 2))
    assert len(windows) == 16
    assert windows[0] == (date(2008, 7, 1), 0)
    assert windows[-1] == (date(2008, 7, 2), 7)
    with pytest.raises(ValueError, match="on or after"):
        enumerate_windows(date(2008, 7, 2), date(2008, 7, 1))
