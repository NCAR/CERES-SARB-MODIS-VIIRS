"""Canonical CAM-FV f09 grid and longitude handling.

Ported from NCAR/CERES-SARB-CAM7 ``sp1_aod_pipeline/f09_grid.py``
and the longitude preparation in ``sp1_aod_pipeline/grid.py``. File-grid
validation and the inclusive final longitude edge are additions here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import netCDF4
import numpy as np
from numpy.typing import ArrayLike, NDArray

NLAT = 192
NLON = 288
DLON = 1.25


def f09_lat() -> NDArray[np.float64]:
    return np.linspace(-90.0, 90.0, NLAT)


def f09_lon() -> NDArray[np.float64]:
    return np.arange(NLON, dtype=np.float64) * DLON


def lat_edges() -> NDArray[np.float64]:
    lat = f09_lat()
    mids = 0.5 * (lat[:-1] + lat[1:])
    return np.concatenate([[-90.0], mids, [90.0]])


def lon_edges() -> NDArray[np.float64]:
    return np.arange(NLON + 1, dtype=np.float64) * DLON - 0.5 * DLON


def f09_lat_bnds() -> NDArray[np.float64]:
    edges = lat_edges()
    return np.stack([edges[:-1], edges[1:]], axis=1)


def f09_lon_bnds() -> NDArray[np.float64]:
    edges = lon_edges()
    return np.stack([edges[:-1], edges[1:]], axis=1)


@dataclass(frozen=True)
class GridSpec:
    """Grid centers and edges; data arrays use ``(lat, lon)`` order."""

    lat: NDArray[np.float64]
    lon: NDArray[np.float64]
    lat_edges: NDArray[np.float64]
    lon_edges: NDArray[np.float64]

    @property
    def shape(self) -> tuple[int, int]:
        return self.lat.size, self.lon.size

    @property
    def lat_bnds(self) -> NDArray[np.float64]:
        return np.stack([self.lat_edges[:-1], self.lat_edges[1:]], axis=1)

    @property
    def lon_bnds(self) -> NDArray[np.float64]:
        return np.stack([self.lon_edges[:-1], self.lon_edges[1:]], axis=1)


def load_grid(source: str = "f09") -> GridSpec:
    """Load f09, optionally checking ``file:<path>`` against its centers.

    NetCDF coordinates must be named ``lat`` and ``lon`` and agree with f09
    to 1e-6 degrees (absolute tolerance). Once checked, canonical f09 centers
    and bounds are returned; any file-supplied bounds are intentionally ignored.
    """
    lat = f09_lat()
    lon = f09_lon()
    if source != "f09":
        if not source.startswith("file:") or not source[5:].strip():
            raise ValueError("grid.source must be 'f09' or 'file:<path>'")
        path = Path(source[5:]).expanduser()
        with netCDF4.Dataset(path) as dataset:
            for name, expected in (("lat", lat), ("lon", lon)):
                if name not in dataset.variables:
                    raise ValueError(f"Grid file {path} has no {name!r} coordinate")
                actual = np.ma.asarray(dataset.variables[name][:], dtype=np.float64).filled(np.nan)
                if actual.shape != expected.shape or not np.allclose(
                    actual, expected, rtol=0.0, atol=1e-6
                ):
                    raise ValueError(f"Grid file {path}: {name} centers must equal CAM f09 to 1e-6")
    return GridSpec(lat=lat, lon=lon, lat_edges=lat_edges(), lon_edges=lon_edges())


def normalize_longitude(values: ArrayLike) -> NDArray[np.float64]:
    """Wrap longitude to the grid's [-0.625, 359.375] domain.

    Nonfinite values become NaN. Preserve an input exactly on 359.375 as the
    inclusive final spatial edge (column 287), as required by the binning
    contract; all other finite inputs use ``((lon + 0.625) % 360) - 0.625``.
    Thus small negative longitudes and 359.4 belong to column 0.
    """
    lon = np.asarray(values, dtype=np.float64)
    out = np.full(lon.shape, np.nan, dtype=np.float64)
    valid = np.isfinite(lon)
    first = -0.5 * DLON
    last = 360.0 + first
    out[valid] = ((lon[valid] - first) % 360.0) + first
    out[lon == last] = last
    return out
