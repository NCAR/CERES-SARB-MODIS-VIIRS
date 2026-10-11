"""Geographic support for experimental source-wise spatial AOD filtering."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from sarb_aod.config.schema import DespeckleConfig
from sarb_aod.grid.combine import FILL_FLOAT, CombinedGrid
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


FLAG_FILL = -127
UNCHANGED, ISOLATED, REPLACED, SPIKE_MASKED, DONORS_MISSING = range(5)


@dataclass(frozen=True)
class SourceFilter:
    """Filtered source values and diagnostics, with arbitrary leading window axes."""

    mean: np.ndarray
    flag: np.ndarray
    support_fraction: np.ndarray
    neighbor_count: np.ndarray
    donor_count: np.ndarray
    donor_fraction: np.ndarray


def weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    """First sorted value reaching half the positive weight (a lower tie median)."""
    order = np.argsort(values, kind="stable")
    index = np.searchsorted(np.cumsum(weights[order]), weights.sum() / 2, side="left")
    return float(values[order[index]])


def filter_source(
    values: np.ndarray, geometry: Neighborhoods, settings: DespeckleConfig
) -> SourceFilter:
    """Classify once from original values, then replace from unflagged originals."""
    # Outputs are reshaped into writable window views below; use one layout
    # so reshape never silently copies a Fortran-ordered output.
    original = np.ascontiguousarray(values, dtype=np.float64)
    if original.shape[-2:] != geometry.shape:
        raise ValueError("source field and neighborhood grid shapes differ")
    valid = np.isfinite(original) & (original > -900)
    mean = np.where(valid, original, FILL_FLOAT)
    flag = np.where(valid, UNCHANGED, FLAG_FILL).astype(np.int8)
    support = np.full(original.shape, FILL_FLOAT)
    neighbors = np.full(original.shape, -1, dtype=np.int32)
    donor_count = np.full(original.shape, -1, dtype=np.int32)
    donor_fraction = np.full(original.shape, FILL_FLOAT)
    result = SourceFilter(mean, flag, support, neighbors, donor_count, donor_fraction)
    if not settings.enabled:
        return result
    width = geometry.shape[1]
    size = int(np.prod(geometry.shape))
    area = geometry.areas.ravel()
    targets = np.repeat([row is not None for row in geometry.rows], width)
    for x, good, y, actions, fraction, number, dn, df in zip(
        *(
            array.reshape(-1, size)
            for array in (
                original,
                valid,
                mean,
                flag,
                support,
                neighbors,
                donor_count,
                donor_fraction,
            )
        ),
        strict=True,
    ):
        for cell in np.flatnonzero(targets & good & (x >= settings.aod_min)):
            indices = geometry.indices(int(cell // width), int(cell % width))
            usable = indices[good[indices]]
            total_area = area[indices].sum()
            number[cell] = usable.size
            fraction[cell] = area[usable].sum() / total_area if total_area > 0 else 0
            if (
                number[cell] < settings.min_valid_neighbors
                or fraction[cell] < settings.min_valid_area_fraction
            ):
                actions[cell] = ISOLATED
                continue
            if settings.spike.enabled:
                median = weighted_median(x[usable], area[usable])
                mad = weighted_median(np.abs(x[usable] - median), area[usable])
                excess = max(
                    settings.spike.min_excess, settings.spike.mad_multiplier * 1.4826 * mad
                )
                if x[cell] - median > excess:
                    actions[cell] = (
                        REPLACED if settings.spike.action == "neighbor_mean" else SPIKE_MASKED
                    )
        # Freeze classification before checking donors. Failed replacements remain excluded.
        donor_ok = good & (actions == UNCHANGED)
        y[(actions == ISOLATED) | (actions == SPIKE_MASKED)] = FILL_FLOAT
        for cell in np.flatnonzero(actions == REPLACED):
            indices = geometry.indices(int(cell // width), int(cell % width))
            usable = indices[donor_ok[indices]]
            dn[cell] = usable.size
            total_area = area[indices].sum()
            df[cell] = area[usable].sum() / total_area if total_area > 0 else 0
            if (
                dn[cell] < settings.min_valid_neighbors
                or df[cell] < settings.min_valid_area_fraction
            ):
                actions[cell] = DONORS_MISSING
                y[cell] = FILL_FLOAT
            else:
                y[cell] = np.average(x[usable], weights=area[usable])
    return result


@dataclass(frozen=True)
class FilteredGrid:
    mean: np.ndarray
    sigma: np.ndarray
    flags: np.ndarray


def combine_filtered(
    baseline: CombinedGrid, sources: list[SourceFilter], *, min_pixels: int = 1
) -> FilteredGrid:
    """Experimental fixed-coefficient combination; altered uncertainty is unavailable."""
    if len(sources) != len(baseline.stream_indices):
        raise ValueError("filtered sources must match baseline stream selection")
    mean, sigma = baseline.mean.copy(), baseline.sigma.copy()
    valid = np.isfinite(mean) & (mean > -900) & (baseline.count > 0)
    flags = np.where(valid, 0, FLAG_FILL).astype(np.int8)
    if not sources:
        return FilteredGrid(mean, sigma, flags)
    values = np.stack([source.mean for source in sources], axis=1)
    actions = np.stack([source.flag for source in sources], axis=1)
    if values.shape != baseline.stream_mean.shape or actions.shape != values.shape:
        raise ValueError("filtered source and baseline shapes differ")
    contributed = baseline.stream_weight > 0
    affected = valid & np.any((actions > 0) & contributed, axis=1)
    if not affected.any():
        return FilteredGrid(mean, sigma, flags)
    for action in (ISOLATED, REPLACED, SPIKE_MASKED, DONORS_MISSING):
        has_action = np.any((actions == action) & contributed, axis=1) & valid
        flags[has_action] |= 1 << (action - 1)
    usable = np.isfinite(values) & (values > -900)
    weights = np.where(usable, baseline.stream_weight, 0)
    total_weight = weights.sum(axis=1)
    masked = (actions == ISOLATED) | (actions == SPIKE_MASKED) | (actions == DONORS_MISSING)
    count = np.where(masked, 0, baseline.stream_count).sum(axis=1)
    surviving = affected & (count >= min_pixels) & (total_weight > 0)
    mean[affected] = FILL_FLOAT
    numerator = (weights * np.where(usable, values, 0)).sum(axis=1)
    np.divide(numerator, total_weight, out=mean, where=surviving)
    sigma[affected] = FILL_FLOAT
    return FilteredGrid(mean, sigma, flags)
