"""AERONET collocation with L2 overpasses and three-hour CAM constraint cells.

Nearest-center indices and great-circle distance are ported from NCAR/DAVINCI
``origin/develop:davinci_monet/pairing/strategies/base.py`` (Apache-2.0).
Only index selection is ported: gridded matchups have no distance cutoff.
The ``cam7`` pairing mode reproduces hourly band means before Angstrom
conversion, following CERES-SARB-CAM7's SP1 validation pipeline.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import xarray as xr
from numpy.typing import ArrayLike, NDArray

from sarb_aod.aeronet.fetch import read_cached_day
from sarb_aod.aeronet.spectral import (
    SpectralMethod,
    angstrom_500,
    convert_550nm,
    window_uncertainty,
)
from sarb_aod.config.loader import configuration_metadata
from sarb_aod.config.schema import RunConfig
from sarb_aod.contract import code_provenance, validate_contract_file
from sarb_aod.evaluation.regions import regions_for_location
from sarb_aod.grid.spec import GridSpec, load_grid
from sarb_aod.paths import aeronet_path, output_path
from sarb_aod.pipeline import (
    config_sha256,
    discover_granules,
    processing_sha256,
    require_processing_provenance,
    stream_recipes,
)
from sarb_aod.provenance import runtime_global_attrs
from sarb_aod.readers import LAND, OCEAN, PixelBatch, read_granule
from sarb_aod.streams import StreamRecipe
from sarb_aod.windows import window_bounds

EARTH_RADIUS_KM = 6371.0
OVERPASS_GAP_SECONDS = 30 * 60
_SITE = "AERONET_Site"
_LAT = "Site_Latitude(Degrees)"
_LON = "Site_Longitude(Degrees)"
_ELEVATION = "Site_Elevation(m)"
_BASE_COLUMNS = [
    "site",
    "time",
    "window_start",
    "latitude",
    "longitude",
    "elevation",
    "lat_index",
    "lon_index",
    "x",
    "y",
    "sigma_x",
    "sigma_y",
    "z",
    "n_aeronet",
    "n_satellite",
    "x_std",
    "y_std",
    "y_median",
    "aeronet_level",
    "surface",
    "stream",
    "sensor",
    "algorithm",
    "collection",
    "level",
    "pairing",
    "run",
    "regions",
    "high_elevation",
    "coastal_site",
    "coastal_known",
    "multiple_sites",
    "active_sites_in_cell",
]


@dataclass(frozen=True)
class CollocationOptions:
    radius_km: float = 25.0
    time_minutes: float = 30.0
    min_pixels: int = 3
    min_aeronet: int = 2
    min_aeronet_grid: int = 2
    high_elevation_m: float = 1000.0

    def __post_init__(self) -> None:
        if not np.isfinite([self.radius_km, self.time_minutes, self.high_elevation_m]).all():
            raise ValueError("Collocation limits must be finite")
        if self.radius_km <= 0 or self.time_minutes < 0:
            raise ValueError("Collocation radius must be positive and time tolerance nonnegative")
        for count in (self.min_pixels, self.min_aeronet, self.min_aeronet_grid):
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError("Collocation minimum counts must be positive integers")


def great_circle_km(
    latitude: ArrayLike, longitude: ArrayLike, site_latitude: float, site_longitude: float
) -> NDArray[np.float64]:
    """Haversine distance, with periodic longitude and roundoff-safe antipodes."""
    lat = np.asarray(latitude, dtype=np.float64)
    lon = np.asarray(longitude, dtype=np.float64)
    dlat = np.deg2rad(lat - site_latitude)
    dlon = np.deg2rad((lon - site_longitude + 180) % 360 - 180)
    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(np.deg2rad(lat)) * np.cos(np.deg2rad(site_latitude)) * np.sin(dlon / 2) ** 2
    )
    return np.asarray(2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1))))


def nearest_cell(
    grid: GridSpec, latitude: ArrayLike, longitude: ArrayLike
) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """DAVINCI's nearest 1D centers, with periodic longitude and no radius cut.

    Exact latitude ties choose the northern center, as in the DAVINCI port;
    longitude ties choose the first coordinate index. Invalid sites are errors.
    """
    lat, lon = np.broadcast_arrays(
        np.asarray(latitude, dtype=float), np.asarray(longitude, dtype=float)
    )
    if not np.isfinite(lat).all() or not np.isfinite(lon).all() or (np.abs(lat) > 90).any():
        raise ValueError("Site coordinates must be finite with latitude in [-90, 90]")
    order = np.argsort(grid.lat)
    centers = grid.lat[order]
    if centers.size == 0 or grid.lon.size == 0:
        raise ValueError("Grid coordinates must be nonempty")
    if centers.size == 1:
        ilat = np.zeros(lat.shape, dtype=np.int64)
    else:
        right = np.clip(np.searchsorted(centers, lat), 1, centers.size - 1)
        left = right - 1
        ilat = order[
            np.where(np.abs(lat - centers[left]) < np.abs(lat - centers[right]), left, right)
        ]
    distances = np.abs((lon.reshape(-1, 1) - grid.lon[None, :] + 180) % 360 - 180)
    ilon = np.argmin(distances, axis=1).reshape(lon.shape)
    return np.asarray(ilat, dtype=np.int64), np.asarray(ilon, dtype=np.int64)


def _utc(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")


def _prepare(aeronet: pd.DataFrame, spectral: SpectralMethod) -> pd.DataFrame:
    required = {_SITE, _LAT, _LON, _ELEVATION, "time", "aeronet_level"}
    if missing := required.difference(aeronet.columns):
        raise ValueError(f"AERONET table lacks required columns: {sorted(missing)}")
    frame = aeronet.copy()
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    if not {"aod_550nm", "spectral_flag"} <= set(frame.columns):
        frame = convert_550nm(frame, method=spectral, include_extrapolated=True)
    located = (
        np.isfinite(frame[_LAT])
        & np.isfinite(frame[_LON])
        & frame[_LAT].between(-90, 90)
        & frame["time"].notna()
    )
    return frame.loc[located].reset_index(drop=True)


def _std(values: NDArray[np.float64]) -> float:
    return (
        float(np.std(values, ddof=1)) if values.size > 1 else 0.0 if values.size else float("nan")
    )


def _aeronet_stats(
    rows: pd.DataFrame,
    *,
    minimum: int,
    include_extrapolated: bool,
    instrument_sigma: float,
    retain_sensitivity_candidates: bool = False,
) -> dict[str, Any] | None:
    usable = rows.loc[np.isfinite(rows["aod_550nm"]) & rows["aod_550nm"].gt(0)]
    direct = usable.loc[usable["spectral_flag"] != "extrapolated"]
    selected = usable if include_extrapolated else direct
    primary_eligible = len(selected) >= minimum
    if len(usable) < minimum or (not primary_eligible and not retain_sensitivity_candidates):
        return None
    values = selected["aod_550nm"].to_numpy(dtype=np.float64)
    all_values = usable["aod_550nm"].to_numpy(dtype=np.float64)
    no_extra = direct["aod_550nm"].to_numpy(dtype=np.float64)
    has_direct = no_extra.size >= minimum
    return {
        "primary_eligible": primary_eligible,
        "x": float(np.mean(values)) if primary_eligible else float("nan"),
        "x_std": _std(values) if primary_eligible else float("nan"),
        "n_aeronet": len(values),
        "sigma_x": (
            window_uncertainty(values, instrument_sigma=instrument_sigma)
            if primary_eligible
            else float("nan")
        ),
        "aeronet_extrapolated_count": len(usable) - len(direct),
        "x_including_extrapolated": float(np.mean(all_values)),
        "n_aeronet_including_extrapolated": len(all_values),
        "sigma_x_including_extrapolated": window_uncertainty(
            all_values, instrument_sigma=instrument_sigma
        ),
        "x_no_extrapolated": float(np.mean(no_extra)) if has_direct else float("nan"),
        "n_aeronet_no_extrapolated": len(no_extra),
        "sigma_x_no_extrapolated": (
            window_uncertainty(no_extra, instrument_sigma=instrument_sigma)
            if has_direct
            else float("nan")
        ),
    }


def _site_metadata(rows: pd.DataFrame, timestamp: pd.Timestamp, grid: GridSpec) -> dict[str, Any]:
    reference = rows.iloc[0]
    # Mobile/relocated coordinates must not be silently collapsed within a table.
    for column in (_LAT, _LON, _ELEVATION):
        values = rows[column].dropna().to_numpy(dtype=float)
        if values.size and not np.allclose(values, values[0], rtol=0, atol=1e-4):
            raise ValueError(
                f"Site {reference[_SITE]} changes {column} within the evaluation period"
            )
    lat, lon = float(reference[_LAT]), float(reference[_LON])
    ilat, ilon = nearest_cell(grid, lat, lon)
    return {
        "site": str(reference[_SITE]),
        "latitude": lat,
        "longitude": lon,
        "elevation": float(reference[_ELEVATION]),
        "lat_index": int(ilat),
        "lon_index": int(ilon),
        "aeronet_level": str(reference["aeronet_level"]),
        # Region activity uses the UTC date, so sub-microsecond precision is
        # immaterial here; preserve it in the separate matchup time column.
        "regions": ",".join(
            regions_for_location(lon, lat, time=timestamp.floor("us").to_pydatetime())
        ),
    }


def _errors(row: dict[str, Any]) -> None:
    denominator = np.hypot(row["sigma_x"], row["sigma_y"])
    row["z"] = (row["y"] - row["x"]) / denominator if denominator > 0 else float("nan")
    alternate = np.hypot(row.get("sigma_x_no_extrapolated", np.nan), row["sigma_y"])
    row["z_no_extrapolated"] = (
        (row["y"] - row.get("x_no_extrapolated", np.nan)) / alternate
        if alternate > 0
        else float("nan")
    )


def flag_representativeness(
    frame: pd.DataFrame,
    *,
    high_elevation_m: float = 1000,
    coastal_sites: Mapping[str, bool] | None = None,
) -> pd.DataFrame:
    """Flag high sites, shared cells, and sites with a majority of mixed L2 passes.

    A coastal majority means strictly more than half of the site's matched L2
    overpasses have both land and ocean pixels. ``coastal_known`` distinguishes
    missing L2 evidence from an observed noncoastal majority for grid matchups.
    """
    output = frame.copy()
    if output.empty:
        return output
    known = dict(coastal_sites or {})
    primary = output.loc[
        np.isfinite(output["x"].to_numpy(dtype=float))
        & np.isfinite(output["y"].to_numpy(dtype=float))
    ]
    l2 = primary.loc[primary["level"] == "l2"]
    for site, rows in l2.groupby("site"):
        known[str(site)] = bool(rows["surface"].eq("coastal").mean() > 0.5)
    output["high_elevation"] = output["elevation"].gt(high_elevation_m)
    output["coastal_known"] = output["site"].isin(known)
    output["coastal_site"] = output["site"].map(known).fillna(False).astype(bool)
    keys = ["run", "window_start", "lat_index", "lon_index"]
    active = primary.groupby(keys, dropna=False)["site"].nunique()
    output["active_sites_in_cell"] = (
        pd.MultiIndex.from_frame(output[keys]).map(active).fillna(0).astype(int)
    )
    output["multiple_sites"] = output["active_sites_in_cell"] > 1
    return output


def collocate_l2(
    batches: Iterable[PixelBatch | tuple[str, PixelBatch]],
    aeronet: pd.DataFrame,
    *,
    recipes: Sequence[StreamRecipe],
    options: CollocationOptions | None = None,
    run: str = "run",
    spectral: SpectralMethod = "quadratic_loglog",
    include_extrapolated: bool = True,
    instrument_sigma: float = 0.01,
    retain_sensitivity_candidates: bool = False,
) -> pd.DataFrame:
    """Match site/stream overpasses, merging adjacent granule pieces before pairing.

    Pixels within the inclusive radius are grouped by site and stream; a gap
    longer than 30 minutes separates overpasses. This merges five/six-minute
    granule boundaries without merging successive polar-orbiter passes.
    Tuple inputs carry source filenames into the matchup provenance.
    """
    opts = options or CollocationOptions()
    frame = _prepare(aeronet, spectral)
    recipe_map = {recipe.stream_id: recipe for recipe in recipes}
    if len(recipe_map) != len(recipes):
        raise ValueError("Only one collection per L2 stream may be collocated")
    sites = {
        (str(site), str(level)): rows
        for (site, level), rows in frame.groupby([_SITE, "aeronet_level"])
    }
    grid = load_grid()
    collected: dict[tuple[str, str, str], list[pd.DataFrame]] = defaultdict(list)
    for item in batches:
        source, batch = item if isinstance(item, tuple) else ("", item)
        if batch.stream_id not in recipe_map:
            raise ValueError(f"No provenance recipe for {batch.stream_id}")
        if not np.isin(batch.surface, [LAND, OCEAN]).all():
            raise ValueError("Pixel surface must be LAND or OCEAN")
        valid = (
            np.isfinite(batch.t)
            & np.isfinite(batch.aod)
            & np.isfinite(batch.lat)
            & np.isfinite(batch.lon)
            & (np.abs(batch.lat) <= 90)
        )
        for (site, level), observations in sites.items():
            reference = observations.iloc[0]
            # Latitude preselection avoids a full haversine calculation per site.
            latitude_margin = np.rad2deg(opts.radius_km / EARTH_RADIUS_KM)
            candidates = np.flatnonzero(
                valid & (np.abs(batch.lat - reference[_LAT]) <= latitude_margin + 1e-10)
            )
            distance = great_circle_km(
                batch.lat[candidates], batch.lon[candidates], reference[_LAT], reference[_LON]
            )
            selected = candidates[distance <= opts.radius_km + 1e-9]
            if not selected.size:
                continue
            collected[(site, level, batch.stream_id)].append(
                pd.DataFrame(
                    {
                        "t": batch.t[selected],
                        "aod": batch.aod[selected],
                        "sigma": batch.sigma[selected],
                        "surface": batch.surface[selected],
                        "source": source,
                    }
                )
            )
    output = []
    for (site, level, stream), parts in collected.items():
        pixels = pd.concat(parts, ignore_index=True).sort_values("t")
        passes = pixels["t"].diff().gt(OVERPASS_GAP_SECONDS).cumsum()
        observations = sites[(site, level)]
        recipe = recipe_map[stream]
        for _, selected in pixels.groupby(passes):
            if len(selected) < opts.min_pixels:
                continue
            timestamp = pd.to_datetime(selected["t"].mean(), unit="s", utc=True)
            delta = pd.Timedelta(minutes=opts.time_minutes)
            nearby = observations.loc[
                observations["time"].between(timestamp - delta, timestamp + delta)
            ]
            stats = _aeronet_stats(
                nearby,
                minimum=opts.min_aeronet,
                include_extrapolated=include_extrapolated,
                instrument_sigma=instrument_sigma,
                retain_sensitivity_candidates=retain_sensitivity_candidates,
            )
            if stats is None:
                continue
            surfaces = set(selected["surface"])
            surface = "coastal" if len(surfaces) == 2 else "land" if LAND in surfaces else "ocean"
            sigma = selected["sigma"].to_numpy(dtype=float)
            sigma_sat = (
                float(np.mean(sigma))
                if np.isfinite(sigma).all() and (sigma >= 0).all()
                else float("nan")
            )
            row = {
                **_site_metadata(observations, timestamp, grid),
                **stats,
                "time": timestamp,
                "window_start": timestamp.floor("3h"),
                "y": float(selected["aod"].mean()),
                "y_median": float(selected["aod"].median()),
                "y_std": _std(selected["aod"].to_numpy(dtype=float)),
                "n_satellite": len(selected),
                "sigma_y": sigma_sat,
                "surface": surface,
                "stream": stream,
                "sensor": recipe.instrument,
                "algorithm": recipe.algorithm,
                "platform": recipe.platform,
                "product": recipe.product,
                "collection": recipe.collection,
                "level": "l2",
                "pairing": "default",
                "run": run,
                "source_granules": json.dumps(sorted(set(selected["source"]) - {""})),
                "overpass_start": pd.to_datetime(selected["t"].min(), unit="s", utc=True),
                "overpass_end": pd.to_datetime(selected["t"].max(), unit="s", utc=True),
            }
            _errors(row)
            output.append(row)
    result = pd.DataFrame(output) if output else pd.DataFrame(columns=_BASE_COLUMNS)
    return flag_representativeness(result, high_elevation_m=opts.high_elevation_m)


def _cam7_hour_stats(rows: pd.DataFrame, instrument_sigma: float) -> dict[str, Any] | None:
    if not {"AOD_500nm", "440-675_Angstrom_Exponent"} <= set(rows.columns):
        raise ValueError("CAM7 pairing requires AOD_500nm and 440-675_Angstrom_Exponent")
    values = rows["AOD_500nm"].to_numpy(dtype=float)
    alpha = rows["440-675_Angstrom_Exponent"].to_numpy(dtype=float)
    band = values[np.isfinite(values)]
    exponent = alpha[np.isfinite(alpha)]
    if not band.size or not exponent.size:
        return None
    mean = float(angstrom_500(band.mean(), exponent.mean()).aod)
    if not np.isfinite(mean):
        return None
    individual = angstrom_500(values, alpha).aod
    individual = individual[np.isfinite(individual)]
    sigma = window_uncertainty(individual, instrument_sigma=instrument_sigma)
    return {
        "primary_eligible": True,
        "x": mean,
        "x_std": _std(individual),
        "n_aeronet": len(rows),
        "sigma_x": sigma,
        "n_aod500": len(band),
        "n_angstrom": len(exponent),
        "aeronet_extrapolated_count": 0,
        "x_including_extrapolated": mean,
        "n_aeronet_including_extrapolated": len(rows),
        "sigma_x_including_extrapolated": sigma,
        "x_no_extrapolated": mean,
        "n_aeronet_no_extrapolated": len(rows),
        "sigma_x_no_extrapolated": sigma,
    }


def collocate_grid(
    dataset: xr.Dataset,
    aeronet: pd.DataFrame,
    *,
    start: datetime | pd.Timestamp,
    pairing: Literal["default", "cam7", "daily"] = "default",
    options: CollocationOptions | None = None,
    run: str = "run",
    spectral: SpectralMethod = "quadratic_loglog",
    include_extrapolated: bool = True,
    instrument_sigma: float = 0.01,
    recipes: Sequence[StreamRecipe] = (),
    coastal_sites: Mapping[str, bool] | None = None,
    retain_sensitivity_candidates: bool = False,
) -> pd.DataFrame:
    """Pair a field with site observations in its half-open UTC interval.

    Daily fields are prepared by :func:`collocate_grid_daily`; the other
    modes take one three-hour contract field.
    """
    if pairing not in {"default", "cam7", "daily"}:
        raise ValueError("pairing must be default, cam7 or daily")
    opts = options or CollocationOptions()
    begin = _utc(start)
    interval = "D" if pairing == "daily" else "3h"
    if begin != begin.floor(interval):
        raise ValueError(f"Grid start must be a UTC {interval} boundary")
    hours = 24 if pairing == "daily" else 3
    end = begin + pd.Timedelta(hours=hours)
    frame = _prepare(aeronet, spectral)
    frame = frame.loc[(frame["time"] >= begin) & (frame["time"] < end)]
    grid = GridSpec(
        np.asarray(dataset["lat"], dtype=float),
        np.asarray(dataset["lon"], dtype=float),
        np.array([]),
        np.array([]),
    )
    fields = {
        str(name): np.asarray(dataset[name].transpose("lat", "lon"), dtype=float)
        for name in dataset.data_vars
        if set(dataset[name].dims) == {"lat", "lon"}
    }
    if "aod_weighted_mean" not in fields:
        raise ValueError("Grid dataset requires aod_weighted_mean(lat, lon)")
    output = []
    for (_, _), observations in frame.groupby([_SITE, "aeronet_level"]):
        groups = (
            observations.groupby(observations["time"].dt.floor("h"))
            if pairing == "cam7"
            else [(begin + pd.Timedelta(hours=hours / 2), observations)]
        )
        for timestamp, rows in groups:
            metadata = _site_metadata(observations, timestamp, grid)
            ilat, ilon = metadata["lat_index"], metadata["lon_index"]
            satellite = float(fields["aod_weighted_mean"][ilat, ilon])
            if not np.isfinite(satellite) or satellite <= -900:
                continue
            stats = (
                _cam7_hour_stats(rows, instrument_sigma)
                if pairing == "cam7"
                else _aeronet_stats(
                    rows,
                    minimum=opts.min_aeronet_grid,
                    include_extrapolated=include_extrapolated,
                    instrument_sigma=instrument_sigma,
                    retain_sensitivity_candidates=retain_sensitivity_candidates,
                )
            )
            if stats is None:
                continue
            sigma = (
                float(fields["aod_uncertainty"][ilat, ilon])
                if "aod_uncertainty" in fields
                else float("nan")
            )
            row = {
                **metadata,
                **stats,
                "time": timestamp,
                "window_start": begin,
                "y": satellite,
                "sigma_y": sigma if sigma >= 0 else float("nan"),
                "y_std": float("nan"),
                "y_median": float("nan"),
                "n_satellite": int(fields["aod_count"][ilat, ilon]) if "aod_count" in fields else 0,
                "level": "grid",
                "pairing": pairing,
                "run": run,
                "stream": "merged",
                "surface": "unknown",
                "sensor": "+".join(sorted({r.instrument for r in recipes})) or "unknown",
                "algorithm": "+".join(sorted({r.algorithm for r in recipes})) or "unknown",
                "collection": json.dumps(
                    {r.stream_id: r.collection for r in recipes}, sort_keys=True
                ),
                "streams": ",".join(r.stream_id for r in recipes)
                or str(dataset.attrs.get("streams", "")),
            }
            for name, values in fields.items():
                if name.startswith("aod_count_"):
                    row[name] = int(values[ilat, ilon]) if np.isfinite(values[ilat, ilon]) else 0
            if pairing == "daily":
                row["n_satellite_windows"] = int(fields["aod_window_count"][ilat, ilon])
            _errors(row)
            output.append(row)
    result = pd.DataFrame(output) if output else pd.DataFrame(columns=_BASE_COLUMNS)
    return flag_representativeness(
        result, high_elevation_m=opts.high_elevation_m, coastal_sites=coastal_sites
    )


def collocate_grid_daily(
    datasets: Sequence[xr.Dataset],
    aeronet: pd.DataFrame,
    *,
    start: datetime | pd.Timestamp,
    **kwargs: Any,
) -> pd.DataFrame:
    """Compare independent daily means, joining only after temporal averaging.

    Satellite means use pixel counts across all eight available contract
    windows at each cell, including windows without AERONET observations.
    AERONET means use every eligible observation in the UTC day, including
    times without satellite coverage. Daily satellite uncertainty is not
    inferred from window uncertainties without a temporal covariance model.
    """
    if len(datasets) != 8:
        raise ValueError("Daily grid pairing requires all eight contract windows")
    reference = datasets[0]
    shape = (reference.sizes["lat"], reference.sizes["lon"])
    numerator = np.zeros(shape)
    denominator = np.zeros(shape)
    windows = np.zeros(shape, dtype=np.int64)
    stream_counts: dict[str, np.ndarray] = {}
    for dataset in datasets:
        if any(
            not np.array_equal(dataset[coordinate].values, reference[coordinate].values)
            for coordinate in ("lat", "lon")
        ):
            raise ValueError("Daily grid windows must share identical coordinates")
        values = np.asarray(dataset["aod_weighted_mean"].transpose("lat", "lon"), dtype=float)
        counts = np.asarray(dataset["aod_count"].transpose("lat", "lon"), dtype=float)
        usable = np.isfinite(values) & (values > -900) & np.isfinite(counts) & (counts > 0)
        numerator += np.where(usable, values * counts, 0)
        denominator += np.where(usable, counts, 0)
        windows += usable
        for name in dataset.data_vars:
            if str(name).startswith("aod_count_"):
                count = np.asarray(dataset[name].transpose("lat", "lon"), dtype=float)
                stream_counts.setdefault(str(name), np.zeros(shape))[:] += np.where(
                    usable & np.isfinite(count) & (count > 0), count, 0
                )
    mean = np.divide(numerator, denominator, out=np.full(shape, np.nan), where=denominator > 0)
    daily = xr.Dataset(
        {
            "aod_weighted_mean": (("lat", "lon"), mean),
            "aod_count": (("lat", "lon"), denominator),
            "aod_window_count": (("lat", "lon"), windows),
            "aod_uncertainty": (("lat", "lon"), np.full(shape, np.nan)),
            **{name: (("lat", "lon"), count) for name, count in stream_counts.items()},
        },
        coords={"lat": reference["lat"], "lon": reference["lon"]},
    )
    return collocate_grid(daily, aeronet, start=start, pairing="daily", **kwargs)


def load_aeronet_neighbors(
    config: RunConfig,
    day: date,
    *,
    loader: Callable[[RunConfig, date], pd.DataFrame] = read_cached_day,
) -> pd.DataFrame:
    """Read D-1, D, D+1; absent or corrupt boundary caches fail explicitly."""
    return pd.concat(
        [loader(config, day + timedelta(days=offset)) for offset in (-1, 0, 1)], ignore_index=True
    )


def write_matchups(
    path: Path, frame: pd.DataFrame, *, attributes: Mapping[str, Any] | None = None
) -> Path:
    """Atomically write a flat CF-time matchup table, including an empty table."""
    variables = {}
    for name in frame.columns:
        values = frame[name]
        inferred = pd.api.types.infer_dtype(values, skipna=True)
        if pd.api.types.is_datetime64_any_dtype(values.dtype) or inferred in {
            "datetime",
            "datetime64",
            "date",
        }:
            data = (
                pd.to_datetime(values, utc=True)
                .dt.tz_convert(None)
                .to_numpy(dtype="datetime64[ns]")
            )
        elif pd.api.types.is_bool_dtype(values.dtype) or inferred == "boolean":
            data = values.to_numpy(dtype=np.uint8)
        elif pd.api.types.is_numeric_dtype(values.dtype) or inferred in {
            "integer",
            "floating",
            "mixed-integer-float",
            "decimal",
        }:
            data = pd.to_numeric(values, errors="raise").to_numpy()
        else:
            data = values.fillna("").astype(str).to_numpy(dtype=str)
        variables[name] = (("matchup",), data)
    dataset = xr.Dataset(variables, coords={"matchup": np.arange(len(frame))})
    dataset.attrs.update(
        {
            "Conventions": "CF-1.10",
            "reference": "AERONET",
            "difference_convention": "y - x (satellite - AERONET)",
            "time_zone": "UTC",
            **code_provenance(),
            **runtime_global_attrs(),
        }
    )
    for key, value in (attributes or {}).items():
        if isinstance(value, np.generic):
            value = value.item()
        dataset.attrs[key] = (
            value if isinstance(value, (str, int, float)) else json.dumps(value, sort_keys=True)
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    try:
        dataset.to_netcdf(temporary, engine="netcdf4")
        os.replace(temporary, path)
    finally:
        dataset.close()
        Path(temporary).unlink(missing_ok=True)
    return path


@dataclass(frozen=True)
class MatchupResult:
    frame: pd.DataFrame
    path: Path


def build_matchups(
    config: RunConfig,
    *,
    level: Literal["l2", "grid"],
    output_dir: Path,
    pairing: Literal["default", "cam7", "daily"] = "default",
    options: CollocationOptions | None = None,
    coastal_sites: Mapping[str, bool] | None = None,
) -> MatchupResult:
    """Read configured inputs, collocate the period, and write its NetCDF table."""
    if level not in {"l2", "grid"} or pairing not in {"default", "cam7", "daily"}:
        raise ValueError("Unknown collocation level or pairing mode")
    if level == "l2" and pairing != "default":
        raise ValueError("CAM7 and daily pairing are defined only for gridded products")
    if config.aeronet.avg != "all_points":
        raise ValueError("Evaluation requires AERONET all_points caches")
    opts = options or CollocationOptions()
    recipes = stream_recipes(config)
    cached: dict[date, pd.DataFrame] = {}

    def load_day(cfg: RunConfig, day: date) -> pd.DataFrame:
        if day not in cached:
            cached[day] = read_cached_day(cfg, day)
        return cached[day]

    for day in config.period.days():
        if level == "l2":
            load_aeronet_neighbors(config, day, loader=load_day)
        else:
            load_day(config, day)
    aeronet = pd.concat([cached[day] for day in sorted(cached)], ignore_index=True)
    common: dict[str, Any] = dict(
        options=opts,
        run=config.name,
        spectral=config.aeronet.spectral,
        include_extrapolated=config.aeronet.include_extrapolated,
        instrument_sigma=config.aeronet.instrument_sigma,
        recipes=recipes,
        retain_sensitivity_candidates=True,
    )
    sources: set[str] = set()
    if level == "l2":

        def batches() -> Iterable[tuple[str, PixelBatch]]:
            for day in config.period.days():
                inputs, _, primary_count = discover_granules(config, day, recipes)
                if not primary_count:
                    raise FileNotFoundError(f"No primary L2 granules for {day}")
                for path, selected in inputs:
                    if str(path) in sources:
                        continue
                    sources.add(str(path))
                    for batch in read_granule(
                        path,
                        selected,
                        qc=config.qc,
                        time=config.time,
                        uncertainty=config.uncertainty,
                        corrections=config.aod_correction,
                    ).values():
                        yield str(path), batch

        table = collocate_l2(batches(), aeronet, **common)
    else:
        tables = []
        for day in config.period.days():
            observations = cached[day]
            daily_fields = []
            daily_paths = []
            for index in range(8):
                start = window_bounds(day, index)[0]
                path = output_path(config, start)
                valid, errors = validate_contract_file(path)
                if not valid:
                    raise ValueError(f"Invalid grid input {path}: {errors}")
                sources.add(str(path))
                with xr.open_dataset(path, decode_times=False) as dataset:
                    require_processing_provenance(dataset, config, window_start=start)
                    if pairing == "daily":
                        names = [
                            name
                            for name in dataset.data_vars
                            if str(name) in {"aod_weighted_mean", "aod_count"}
                            or str(name).startswith("aod_count_")
                        ]
                        daily_fields.append(dataset[names].load())
                        daily_paths.append(str(path))
                        continue
                    matched = collocate_grid(
                        dataset,
                        observations,
                        start=start,
                        pairing=pairing,
                        coastal_sites=coastal_sites,
                        **common,
                    )
                matched["grid_file"] = str(path)
                tables.append(matched)
            if pairing == "daily":
                matched = collocate_grid_daily(
                    daily_fields,
                    observations,
                    start=pd.Timestamp(day),
                    coastal_sites=coastal_sites,
                    **common,
                )
                matched["grid_files"] = json.dumps(daily_paths)
                tables.append(matched)
        table = pd.concat(tables, ignore_index=True)
    if not table.empty:
        begin = _utc(config.period.start)
        end = _utc(config.period.end + timedelta(days=1))
        table = table.loc[(table["time"] >= begin) & (table["time"] < end)].reset_index(drop=True)
        table = flag_representativeness(
            table, high_elevation_m=opts.high_elevation_m, coastal_sites=coastal_sites
        )
    filename = (
        f"matchups_{level}_{pairing}_{config.period.start:%Y%m%d}_{config.period.end:%Y%m%d}.nc"
    )
    path = write_matchups(
        output_dir / filename,
        table,
        attributes={
            "run": config.name,
            "level": level,
            "pairing": pairing,
            "config_sha256": config_sha256(config),
            "processing_sha256": processing_sha256(config),
            "retains_sensitivity_candidates": 1,
            **configuration_metadata(config),
            "collocation_options": asdict(opts),
            "source_files": sorted(sources),
            "aeronet_cache_files": [str(aeronet_path(config, day)) for day in sorted(cached)],
        },
    )
    return MatchupResult(table, path)
