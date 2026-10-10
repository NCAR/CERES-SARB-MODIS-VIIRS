"""Published geographic extents, overlapping membership, and event dates."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from sarb_aod.evaluation.regions import (
    AEROSOL_REGION_ALIASES,
    AEROSOL_REGIONS,
    REGION_DISPLAY_NAMES,
    REGIONS,
    Region,
    get_region,
    get_region_extent,
    region_mask,
    regions_for_location,
    spatial_mask,
)


def test_published_catalog_and_display_names() -> None:
    assert dict(AEROSOL_REGIONS) == {
        "north_atlantic_ocean": (-80, -10, 0, 35),
        "south_atlantic_ocean": (-40, 20, -35, 0),
        "north_indian_ocean": (40, 100, 0, 24),
        "north_africa": (-18, 30, 0, 30),
        "southern_africa": (8, 35, -30, 0),
        "eastern_us": (-95, -68, 25, 48),
        "western_us": (-125, -95, 25, 48),
        "canada": (-160, -60, 48, 70),
        "south_america": (-80, -35, -35, 0),
        "middle_east": (30, 70, 10, 32),
        "east_asia": (100, 140, 20, 48),
        "india": (68, 95, 8, 35),
    }
    assert len(REGIONS) == 15
    assert set(REGION_DISPLAY_NAMES) == set(REGIONS)
    assert get_region("north_africa").display_name == "North Africa (Sahara)"
    assert get_region("labor_day_2020_smoke").display_name == "Labor Day Smoke"


@pytest.mark.parametrize("alias,key", AEROSOL_REGION_ALIASES.items())
def test_aliases_preserve_canonical_identity(alias: str, key: str) -> None:
    assert get_region(alias) is get_region(key)
    assert get_region(f"  {alias.upper().replace('_', '-')}  ") is get_region(key)


def test_unknown_region_is_an_error() -> None:
    with pytest.raises(KeyError, match="Unknown region"):
        get_region("Atlantic Ocean")


@pytest.mark.parametrize("key,extent", AEROSOL_REGIONS.items())
def test_published_rectangles_include_all_four_corners(key: str, extent: tuple) -> None:
    west, east, south, north = extent
    np.testing.assert_array_equal(
        spatial_mask(key, [west, west, east, east], [south, north, south, north]),
        [True, True, True, True],
    )
    assert not spatial_mask(key, west - 0.01, south)
    assert not spatial_mask(key, east, north + 0.01)


def test_memberships_overlap_and_can_be_empty() -> None:
    assert set(regions_for_location(-95, 48)) == {"western_us", "eastern_us", "canada"}
    assert regions_for_location(-170, 0) == ()
    assert regions_for_location(-170, 0, include_global=True) == ("global",)
    # The North Atlantic rectangle includes inland Colombia; there is no land mask.
    assert region_mask("north_atlantic_ocean", -75, 10)


def test_longitude_normalization_and_coordinate_broadcasting() -> None:
    np.testing.assert_array_equal(
        spatial_mask("western_us", np.array([-120, 240, 600, -480])[None, :], [[30], [60]]),
        [[True] * 4, [False] * 4],
    )
    np.testing.assert_array_equal(
        spatial_mask("global", [-180, 180, 360, -360], [-90, 90, -90, 90]),
        [True] * 4,
    )


def test_global_excludes_invalid_coordinates_without_warnings() -> None:
    with np.errstate(all="raise"):
        np.testing.assert_array_equal(
            spatial_mask("global", [np.nan, np.inf, 0, 0, 0], [0, 0, np.nan, 91, -91]),
            [False] * 5,
        )


def test_southern_ocean_is_strictly_south_of_40_degrees() -> None:
    assert get_region_extent("southern_ocean") == (-180, 180, -90, -40)
    np.testing.assert_array_equal(
        region_mask("southern_ocean", [0, 80, 180, 270], [-40, -40.0001, -90, -39.99]),
        [False, True, True, False],
    )


def test_smoke_box_dates_include_entire_last_day() -> None:
    times = np.array(
        [
            "2020-09-07T23:59:59",
            "2020-09-08T00:00:00",
            "2020-09-14T23:59:59",
            "2020-09-15T00:00:00",
            "NaT",
        ],
        dtype="datetime64[s]",
    )
    np.testing.assert_array_equal(
        region_mask("labor_day_2020_smoke", -120, 40, times=times),
        [False, True, True, False, False],
    )
    assert not region_mask("labor_day_2020_smoke", -100, 40, times=date(2020, 9, 8))
    with pytest.raises(ValueError, match="requires times"):
        region_mask("labor_day_2020_smoke", -120, 40)
    assert spatial_mask("labor_day_2020_smoke", -120, 40)


def test_smoke_dates_broadcast_and_respect_utc() -> None:
    after_local_midnight = datetime(2020, 9, 15, 1, tzinfo=timezone(timedelta(hours=2)))
    assert region_mask("labor_day_2020_smoke", -120, 40, times=after_local_midnight)
    assert region_mask("labor_day_2020_smoke", -120, 40, times="2020-09-15T01:00:00+02:00")
    np.testing.assert_array_equal(
        region_mask(
            "labor_day_2020_smoke",
            [-120, -100],
            40,
            times=[[date(2020, 9, 8)], [date(2020, 9, 15)]],
        ),
        [[True, False], [False, False]],
    )
    assert "labor_day_2020_smoke" not in regions_for_location(-120, 40)
    assert "labor_day_2020_smoke" in regions_for_location(-120, 40, time="2020-09-08")
    assert "labor_day_2020_smoke" not in regions_for_location(-120, 40, time="2020-09-15")


def test_explicit_overrides_do_not_mutate_catalog() -> None:
    default = get_region("labor_day_2020_smoke")
    override = replace(default, extent=(-130, -110, 30, 50))
    assert region_mask(override, -128, 40, times="2020-09-08")
    assert not region_mask(default, -128, 40, times="2020-09-08")
    assert get_region_extent(default) == (-125, -116, 36, 49)


def test_custom_antimeridian_region() -> None:
    region = Region("pacific", "Pacific", (170, -170, -10, 10))
    np.testing.assert_array_equal(
        region_mask(region, [170, 180, -170, -175, 0], 0), [True, True, True, True, False]
    )
    east_edge = Region("pacific_edge", "Pacific", (170, 180, -10, 10))
    np.testing.assert_array_equal(
        region_mask(east_edge, [170, 180, -180, -179], 0), [True, True, True, False]
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"extent": (np.nan, 10, 0, 10)},
        {"extent": (-190, 10, 0, 10)},
        {"extent": (0, 10, 40, 30)},
        {"start_date": date(2020, 9, 8)},
        {"start_date": date(2020, 9, 8), "end_date": date(2020, 9, 7)},
    ],
)
def test_invalid_region_definition(changes: dict) -> None:
    with pytest.raises(ValueError):
        replace(get_region("global"), **changes)
