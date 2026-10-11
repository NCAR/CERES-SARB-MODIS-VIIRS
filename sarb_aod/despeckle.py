"""Geographic support for experimental source-wise spatial AOD filtering."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from sarb_aod.config.schema import DespeckleConfig
from sarb_aod.grid.spec import GridSpec

EARTH_RADIUS_KM = 6371.0
DESPECKLE_VERSION = "1"


def cell_areas(grid: GridSpec) -> np.ndarray:
    """Finite spherical wedge areas in km², including the pole-centered rows."""
    height = np.diff(np.sin(np.deg2rad(grid.lat_edges)))
    width = np.deg2rad(np.diff(grid.lon_edges))
    return EARTH_RADIUS_KM**2 * height[:, None] * width[None, :]


@dataclass(frozen=True)
class NeighborRow:
    lat_indices: np.ndarray
    lon_offsets: np.ndarray


@dataclass(frozen=True)
class Neighborhoods:
    """Sparse, rotationally shared neighbor indices on a regular longitude grid."""

    shape: tuple[int, int]
    rows: tuple[NeighborRow | None, ...]
    areas: np.ndarray

    def indices(self, row: int, column: int) -> np.ndarray:
        neighborhood = self.rows[row]
        if neighborhood is None:
            return np.array([], dtype=np.int64)
        return (
            neighborhood.lat_indices * self.shape[1]
            + (neighborhood.lon_offsets + column) % self.shape[1]
        )


def neighborhoods(grid: GridSpec, settings: DespeckleConfig) -> Neighborhoods:
    """Cache only geometry; thresholds/actions never change neighbor membership."""
    return _neighborhoods(
        tuple(grid.lat),
        tuple(grid.lon),
        tuple(grid.lat_edges),
        tuple(grid.lon_edges),
        settings.latitude_bands,
        settings.radius_km,
    )


@lru_cache(maxsize=8)
def _neighborhoods(
    lat: tuple[float, ...],
    lon: tuple[float, ...],
    lat_edges: tuple[float, ...],
    lon_edges: tuple[float, ...],
    bands: tuple[tuple[float, float], ...],
    radius_km: float,
) -> Neighborhoods:
    grid = GridSpec(*(np.asarray(values) for values in (lat, lon, lat_edges, lon_edges)))
    nlat, nlon = grid.shape
    if not np.allclose(
        np.diff(grid.lon_edges), 360.0 / nlon, rtol=0, atol=1e-10
    ) or not np.allclose(np.diff(grid.lon), 360.0 / nlon, rtol=0, atol=1e-10):
        raise ValueError("despeckle neighborhoods require regular global longitudes")
    areas = cell_areas(grid)
    if np.any(~np.isfinite(areas) | (areas <= 0)):
        raise ValueError("despeckle grid cells require positive finite areas")
    areas.setflags(write=False)
    phi = np.deg2rad(grid.lat)
    dlon = np.deg2rad(grid.lon - grid.lon[0])
    angular_radius = radius_km / EARTH_RADIUS_KM
    rows: list[NeighborRow | None] = []
    for i, latitude in enumerate(grid.lat):
        if not any(lo <= latitude <= hi for lo, hi in bands):
            rows.append(None)
            continue
        possible = np.flatnonzero(np.abs(phi - phi[i]) <= angular_radius + 1e-12)
        # Haversine avoids arccos cancellation for very small separations.
        h = np.sin((phi[possible, None] - phi[i]) / 2) ** 2 + (
            np.cos(phi[possible, None]) * np.cos(phi[i]) * np.sin(dlon[None, :] / 2) ** 2
        )
        distance = 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(h, 0, 1)))
        ilat, ilon = np.nonzero(distance <= radius_km + 1e-8)
        ilat = possible[ilat]
        keep = (ilat != i) | (ilon != 0)
        ilat, ilon = ilat[keep], ilon[keep]
        ilat.setflags(write=False)
        ilon.setflags(write=False)
        rows.append(NeighborRow(ilat, ilon))
    return Neighborhoods((nlat, nlon), tuple(rows), areas)
