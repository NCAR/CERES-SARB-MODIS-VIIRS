"""UTC leap boundaries and the late July 2008 MODIS scan regression."""

from datetime import date

import numpy as np
import pytest

from sarb_aod.timeconv import tai93_to_datetime64, tai93_to_unix_seconds
from sarb_aod.windows import day_time_edges_seconds, pixel_window_mask, window_bounds

EPOCH93 = np.datetime64("1993-01-01T00:00:00", "ns")
UNIX = np.datetime64("1970-01-01T00:00:00", "ns")
# Independent test table: the new offset applies from this UTC midnight.
LEAPS = [
    ("1993-07-01", 1),
    ("1994-07-01", 2),
    ("1996-01-01", 3),
    ("1997-07-01", 4),
    ("1999-01-01", 5),
    ("2006-01-01", 6),
    ("2009-01-01", 7),
    ("2012-07-01", 8),
    ("2015-07-01", 9),
    ("2017-01-01", 10),
]


@pytest.mark.parametrize("effective,offset", LEAPS)
def test_each_leap_transition_and_unrepresentable_inserted_second(
    effective: str, offset: int
) -> None:
    midnight = np.datetime64(effective, "ns")
    transition = float((midnight - EPOCH93) / np.timedelta64(1, "s")) + offset
    seconds = [transition - 1.25, transition - 1, transition - 0.5, transition, transition + 0.25]
    times = tai93_to_datetime64(seconds)
    np.testing.assert_array_equal(
        times[[0, 3, 4]],
        [midnight - np.timedelta64(250, "ms"), midnight, midnight + np.timedelta64(250, "ms")],
    )
    assert np.isnat(times[1:3]).all()
    unix = tai93_to_unix_seconds(seconds)
    assert np.isnan(unix[1:3]).all()
    midnight_unix = float((midnight - UNIX) / np.timedelta64(1, "s"))
    np.testing.assert_array_equal(unix[[0, 3, 4]], midnight_unix + np.array([-0.25, 0, 0.25]))
    assert np.isnat(tai93_to_datetime64(np.nextafter(transition, -np.inf)))
    assert not np.isnat(tai93_to_datetime64(np.nextafter(transition - 1, -np.inf)))


def test_july_2008_last_scan_kept_in_its_utc_day() -> None:
    tai93 = np.array([489110405.94])
    applied = tai93_to_datetime64(tai93)
    ignored = tai93_to_datetime64(tai93, leap_seconds="ignore")
    np.testing.assert_array_equal(
        applied, np.array(["2008-07-01T23:59:59.940000000"], dtype="datetime64[ns]")
    )
    np.testing.assert_array_equal(
        ignored, np.array(["2008-07-02T00:00:05.940000000"], dtype="datetime64[ns]")
    )
    start, end, _ = window_bounds(date(2008, 7, 1), 7)
    assert pixel_window_mask(applied, start, end)[0]
    assert not pixel_window_mask(ignored, start, end)[0]
    edges = day_time_edges_seconds(date(2008, 7, 1))
    assert edges[7] <= tai93_to_unix_seconds(tai93)[0] < edges[8]
    assert tai93_to_unix_seconds(tai93, leap_seconds="ignore")[0] >= edges[8]


@pytest.mark.parametrize("utc,offset", [("1993-01-01", 0), ("2008-07-01", 6), ("2020-09-08", 10)])
def test_epoch_and_target_period_offsets(utc: str, offset: int) -> None:
    expected = np.datetime64(utc, "ns")
    tai93 = float((expected - EPOCH93) / np.timedelta64(1, "s")) + offset
    assert tai93_to_datetime64(tai93) == expected
    assert tai93_to_datetime64(tai93, leap_seconds="ignore") == expected + np.timedelta64(
        offset, "s"
    )
    assert tai93_to_unix_seconds(tai93) == float((expected - UNIX) / np.timedelta64(1, "s"))


def test_ignore_does_not_drop_leap_second_interval() -> None:
    transition = float((np.datetime64("2017-01-01", "ns") - EPOCH93) / np.timedelta64(1, "s")) + 10
    assert not np.isnat(tai93_to_datetime64(transition - 0.5, leap_seconds="ignore"))


@pytest.mark.parametrize("mode", ["apply", "ignore"])
def test_invalid_values_do_not_warn_or_wrap(mode: str) -> None:
    seconds = [[np.nan, np.inf, -np.inf, -9999], [-1, 1e30, 10_000_000_000, 0]]
    with np.errstate(all="raise"):
        result = tai93_to_datetime64(seconds, leap_seconds=mode)
        unix = tai93_to_unix_seconds(seconds, leap_seconds=mode)
    assert result.shape == (2, 4)
    np.testing.assert_array_equal(
        np.isnat(result), [[True, True, True, True], [True, True, True, False]]
    )
    np.testing.assert_array_equal(np.isnan(unix), np.isnat(result))
    assert result[1, 3] == EPOCH93


def test_near_datetime64_limit_does_not_overflow() -> None:
    final_whole_second = np.datetime64("2262-04-11T23:47:16", "ns")
    seconds = float((final_whole_second - EPOCH93) / np.timedelta64(1, "s")) + 10
    assert tai93_to_datetime64(seconds) == final_whole_second
    assert np.isnat(tai93_to_datetime64(seconds + 1))
    assert np.isnat(tai93_to_datetime64(seconds + 0.9))


def test_empty_and_scalar_shapes_and_invalid_mode() -> None:
    assert tai93_to_datetime64([]).shape == (0,)
    assert tai93_to_unix_seconds([]).shape == (0,)
    assert tai93_to_datetime64(0).shape == ()
    assert tai93_to_unix_seconds(0).shape == ()
    with pytest.raises(ValueError, match="time.leap_seconds"):
        tai93_to_datetime64([0], leap_seconds="unknown")
