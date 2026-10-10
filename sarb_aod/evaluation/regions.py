"""Shared aerosol regions for collocation, statistics, and plotting.

The twelve published extents and aliases are ported from NCAR/DAVINCI,
``origin/develop:davinci_monet/geography/domains.py`` (Apache-2.0), based on
Bhattacharjee et al. (2018), GMD 11:2333, Table 1:
https://doi.org/10.5194/gmd-11-2333-2018. Display names follow NCAR/CERES-SARB-CAM7,
``scripts/prepare_2008_regional_aod.py``. Neither repository is imported.

Rectangles include their boundaries and apply no land mask. Regions overlap;
global statistics must be computed from the original observations, never by
summing region statistics. The Southern Ocean band is strictly south of 40 S.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from types import MappingProxyType
from typing import Mapping

import numpy as np
from numpy.typing import ArrayLike, NDArray

Extent = tuple[float, float, float, float]

# Preserve the published twelve-region catalog independently of our additions.
AEROSOL_REGIONS: Mapping[str, Extent] = MappingProxyType(
    {
        "north_atlantic_ocean": (-80.0, -10.0, 0.0, 35.0),
        "south_atlantic_ocean": (-40.0, 20.0, -35.0, 0.0),
        "north_indian_ocean": (40.0, 100.0, 0.0, 24.0),
        "north_africa": (-18.0, 30.0, 0.0, 30.0),
        "southern_africa": (8.0, 35.0, -30.0, 0.0),
        "eastern_us": (-95.0, -68.0, 25.0, 48.0),
        "western_us": (-125.0, -95.0, 25.0, 48.0),
        "canada": (-160.0, -60.0, 48.0, 70.0),
        "south_america": (-80.0, -35.0, -35.0, 0.0),
        "middle_east": (30.0, 70.0, 10.0, 32.0),
        "east_asia": (100.0, 140.0, 20.0, 48.0),
        "india": (68.0, 95.0, 8.0, 35.0),
    }
)

AEROSOL_REGION_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "sahara": "north_africa",
        "saharan_north_africa": "north_africa",
        "south_africa": "southern_africa",
        "eastern_usa": "eastern_us",
        "western_usa": "western_us",
        "north_atlantic": "north_atlantic_ocean",
        "south_atlantic": "south_atlantic_ocean",
    }
)

REGION_DISPLAY_NAMES: Mapping[str, str] = MappingProxyType(
    {
        "north_atlantic_ocean": "North Atlantic Ocean",
        "south_atlantic_ocean": "South Atlantic Ocean",
        "north_indian_ocean": "North Indian Ocean",
        "north_africa": "North Africa (Sahara)",
        "southern_africa": "Southern Africa",
        "eastern_us": "Eastern US",
        "western_us": "Western US",
        "canada": "Canada",
        "south_america": "South America",
        "middle_east": "Middle East",
        "east_asia": "East Asia",
        "india": "India",
        "global": "Global",
        "southern_ocean": "Southern Ocean",
        "labor_day_2020_smoke": "Labor Day Smoke",
    }
)


@dataclass(frozen=True)
class Region:
    """A rectangle and optional inclusive UTC activity dates.

    Extents are ``(west, east, south, north)`` in degrees. ``west > east``
    represents a rectangle crossing the antimeridian. Configured overrides can
    be passed directly to :func:`region_mask` without mutating the catalog.
    """

    key: str
    display_name: str
    extent: Extent
    start_date: date | None = None
    end_date: date | None = None
    include_north_boundary: bool = True

    def __post_init__(self) -> None:
        west, east, south, north = self.extent
        if not np.isfinite(self.extent).all():
            raise ValueError("Region extent must be finite")
        if not (-180 <= west <= 180 and -180 <= east <= 180):
            raise ValueError("Region longitude bounds must be in [-180, 180]")
        if not -90 <= south <= north <= 90:
            raise ValueError("Region latitude bounds must satisfy -90 <= south <= north <= 90")
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("Region activity requires both start_date and end_date")
        if self.start_date is not None and self.end_date is not None:
            if self.start_date > self.end_date:
                raise ValueError("Region end_date precedes start_date")


REGIONS: Mapping[str, Region] = MappingProxyType(
    {
        **{
            key: Region(key, REGION_DISPLAY_NAMES[key], extent)
            for key, extent in AEROSOL_REGIONS.items()
        },
        "global": Region("global", REGION_DISPLAY_NAMES["global"], (-180, 180, -90, 90)),
        "southern_ocean": Region(
            "southern_ocean",
            REGION_DISPLAY_NAMES["southern_ocean"],
            (-180, 180, -90, -40),
            include_north_boundary=False,
        ),
        "labor_day_2020_smoke": Region(
            "labor_day_2020_smoke",
            REGION_DISPLAY_NAMES["labor_day_2020_smoke"],
            (-125, -116, 36, 49),
            start_date=date(2020, 9, 8),
            end_date=date(2020, 9, 14),
        ),
    }
)


def get_region(name: str | Region) -> Region:
    """Resolve a region, including DAVINCI aliases and human-friendly spellings."""
    if isinstance(name, Region):
        return name
    key = "_".join(name.strip().casefold().replace("-", " ").split())
    key = AEROSOL_REGION_ALIASES.get(key, key)
    try:
        return REGIONS[key]
    except KeyError:
        raise KeyError(f"Unknown region {name!r}; choose from {', '.join(REGIONS)}") from None


def get_region_extent(name: str | Region) -> Extent:
    """Return a plotting extent in ``(west, east, south, north)`` order."""
    return get_region(name).extent


def spatial_mask(
    region: str | Region, longitude: ArrayLike, latitude: ArrayLike
) -> NDArray[np.bool_]:
    """Rectangle membership of broadcastable coordinates, independent of dates.

    Longitudes are normalized to [-180, 180); nonfinite coordinates never
    belong to any region. The returned mask has the broadcast coordinate shape.
    Pass ``longitude[None, :]`` and ``latitude[:, None]`` for a rectilinear grid.
    """
    spec = get_region(region)
    lon, lat = np.broadcast_arrays(
        np.asarray(longitude, dtype=np.float64), np.asarray(latitude, dtype=np.float64)
    )
    finite = np.isfinite(lon) & np.isfinite(lat)
    lon = np.remainder(lon + 180.0, 360.0, out=np.full_like(lon, np.nan), where=finite) - 180.0
    west, east, south, north = spec.extent
    lon_ok = (lon >= west) & (lon <= east)
    if west > east:
        lon_ok = (lon >= west) | (lon <= east)
    if east == 180:
        # +180 is the same inclusive boundary as normalized -180.
        lon_ok |= lon == -180
    lat_ok = (lat >= south) & (lat <= north if spec.include_north_boundary else lat < north)
    return np.asarray(finite & lon_ok & lat_ok, dtype=np.bool_)


def _utc_days(times: ArrayLike | date) -> NDArray[np.datetime64]:
    """Convert UTC timestamps; normalize aware Python datetimes before truncation."""
    values = np.asarray(times)
    if np.issubdtype(values.dtype, np.datetime64):
        return values.astype("datetime64[D]")
    days = []
    for value in values.flat:
        if isinstance(value, str) and value != "NaT":
            value = datetime.fromisoformat(value)
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                value = value.astimezone(timezone.utc)
            value = value.date()
        if value is not None and not isinstance(value, (date, np.datetime64, str)):
            raise TypeError("Region times must be dates, datetimes, or ISO date strings")
        days.append(
            value.astype("datetime64[D]")
            if isinstance(value, np.datetime64)
            else np.datetime64(value, "D")
        )
    return np.asarray(days, dtype="datetime64[D]").reshape(values.shape)


def region_mask(
    region: str | Region,
    longitude: ArrayLike,
    latitude: ArrayLike,
    *,
    times: ArrayLike | date | None = None,
) -> NDArray[np.bool_]:
    """Geographic membership, constrained to a region's UTC activity dates.

    Dates are inclusive, so the smoke region includes all of September 14.
    Date-limited regions require ``times``; use :func:`spatial_mask` when only
    geometry is wanted (for example, a plot extent). Naive timestamps are UTC.
    Coordinate and timestamp arrays must be broadcastable. ``NaT`` is excluded
    from date-limited regions; static regions do not filter times.
    """
    spec = get_region(region)
    mask = spatial_mask(spec, longitude, latitude)
    if spec.start_date is None:
        return mask
    if times is None:
        raise ValueError(f"Region {spec.key!r} requires times for its activity dates")
    days = _utc_days(times)
    active = (days >= np.datetime64(spec.start_date)) & (days <= np.datetime64(spec.end_date))
    return np.asarray(mask & active, dtype=np.bool_)


def regions_for_location(
    longitude: float,
    latitude: float,
    *,
    time: date | datetime | np.datetime64 | str | None = None,
    include_global: bool = False,
) -> tuple[str, ...]:
    """Return every matching canonical key, retaining overlapping memberships.

    With no ``time``, only static regions are considered. Global membership is
    opt-in so sites outside all regional rectangles can be identified; its
    statistics must always be computed separately from regional aggregates.
    """
    return tuple(
        key
        for key, spec in REGIONS.items()
        if (include_global or key != "global")
        and (time is not None or spec.start_date is None)
        and bool(region_mask(spec, longitude, latitude, times=time))
    )
