"""Stream staged Level-2 granules into one day's eight CAM7 contract files."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

import netCDF4
import numpy as np

from sarb_aod.combined_product import accumulate_product, product_window
from sarb_aod.config.schema import RunConfig
from sarb_aod.contract import validate_contract_file, write_contract_file
from sarb_aod.grid.combine import CombinedGrid, combine
from sarb_aod.grid.kernels import Accumulators, accumulate, count_pixels
from sarb_aod.grid.spec import load_grid, normalize_longitude
from sarb_aod.paths import granule_dir, granule_pattern, output_path
from sarb_aod.qc import resolve_rule
from sarb_aod.readers import read_granule
from sarb_aod.streams import StreamRecipe, resolve_stream
from sarb_aod.windows import cf_hours, day_time_edges_seconds, window_bounds


@dataclass
class DayResult:
    day: date
    files: list[Path] = field(default_factory=list)
    skipped: bool = False
    source_granule_count: int = 0
    pixel_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    error: str | None = None


def config_sha256(config: RunConfig) -> str:
    return hashlib.sha256(
        json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def processing_sha256(config: RunConfig) -> str:
    """Hash the processing definition, including resolved external catalog maps.

    Plotting, evaluation and acquisition options do not alter gridded values.
    Their complete configuration is recorded separately in ``config_sha256``.
    """
    definition = {
        key: getattr(config, key).model_dump(mode="json")
        for key in ("grid", "qc", "uncertainty", "weighting", "time", "output")
    }
    definition["recipes"] = [
        {
            "stream": recipe.stream_id,
            "product": recipe.product,
            "collection": recipe.collection,
            "file_format": recipe.file_format,
            "geolocation": recipe.geolocation.model_dump(mode="json"),
            "algorithm": recipe.algorithm_spec.model_dump(mode="json"),
        }
        for recipe in stream_recipes(config)
    ]
    if config.combined_product.enabled:
        definition["combined_product"] = config.combined_product.model_dump(mode="json")
        combined_recipes = {}
        for r in stream_recipes(config):
            spec = config.load_catalog().collection(r.product, r.collection).combined_product
            if spec is None:
                raise ValueError(f"no combined product defined for {r.product} {r.collection}")
            combined_recipes[r.product] = spec.model_dump(mode="json")
        definition["combined_recipes"] = combined_recipes
    if config.paths.granule_pattern != "*":
        definition["granule_pattern"] = config.paths.granule_pattern
    return hashlib.sha256(
        json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def require_processing_provenance(
    dataset: Any, config: RunConfig, window_start: datetime | None = None
) -> None:
    if getattr(dataset, "processing_sha256", None) != processing_sha256(config):
        raise ValueError(
            "grid output processing provenance differs from the requested configuration; rerun grid"
        )
    if window_start is not None and float(dataset["time"][...]) != cf_hours(
        window_start + timedelta(hours=1.5)
    ):
        raise ValueError("grid output time differs from the requested window; rerun grid")


def stream_recipes(config: RunConfig) -> list[StreamRecipe]:
    if not config.grid.streams:
        raise ValueError("grid.streams must select at least one stream")
    catalog = config.load_catalog()
    return [
        resolve_stream(
            selection.product,
            selection.collection,
            cast(Literal["dt", "db"], stream_id.rsplit("_", 1)[1]),
            catalog=catalog,
            preset=config.qc.preset,
        )
        for stream_id, selection in config.grid.streams.items()
    ]


def _granules(directory: Path, file_format: str, pattern: str = "*") -> list[Path]:
    suffixes = {".hdf"} if file_format == "hdf4" else {".nc", ".nc4", ".h5", ".hdf5"}
    if not directory.is_dir():
        return []
    return sorted(
        path
        for path in directory.glob(pattern)
        if path.is_file() and path.suffix.lower() in suffixes
    )


def discover_granules(
    config: RunConfig, day: date, recipes: list[StreamRecipe]
) -> tuple[list[tuple[Path, list[StreamRecipe]]], list[str], int]:
    groups: dict[tuple[str, str], list[StreamRecipe]] = defaultdict(list)
    for recipe in recipes:
        groups[(recipe.product, recipe.collection)].append(recipe)
    catalog = config.load_catalog()
    inputs: list[tuple[Path, list[StreamRecipe]]] = []
    missing = []
    primary_count = 0
    for (product, collection), selected in groups.items():
        directory = granule_dir(config.paths, catalog, product, collection, day)
        files = _granules(
            directory,
            selected[0].file_format,
            granule_pattern(config.paths, catalog, product, collection, day),
        )
        if not files:
            raise ValueError(f"no primary granules for {product} {collection} {day}: {directory}")
        primary_count += len(files)
        inputs.extend((path, selected) for path in files)
        if config.grid.edge_granules:
            previous = granule_dir(
                config.paths, catalog, product, collection, day - timedelta(days=1)
            )
            edges = _granules(
                previous,
                selected[0].file_format,
                granule_pattern(
                    config.paths, catalog, product, collection, day - timedelta(days=1)
                ),
            )
            if edges:
                inputs.append((edges[-1], selected))
            else:
                missing.append(f"{product} {collection} {day-timedelta(days=1)}")
    return inputs, missing, primary_count


def _provenance(
    config: RunConfig, recipes: list[StreamRecipe], source_count: int, missing: list[str]
) -> dict[str, Any]:
    thresholds = {
        recipe.stream_id: {
            surface: {
                "qa_min": resolve_rule(recipe.family, surface, config=config.qc).qa_min,
                "aod_min": config.qc.aod_min,
                "aod_max": config.qc.aod_max,
            }
            for surface in recipe.surfaces
        }
        for recipe in recipes
    }
    return {
        "source": "; ".join(f"{r.product} {r.collection} ({r.stream_id})" for r in recipes),
        "qc_thresholds": json.dumps(thresholds, sort_keys=True),
        "merge_method": (
            "gls (pixel inverse variance, within-stream correlation rho, Birge inflation)"
            if config.weighting.method == "gls"
            else "count (arithmetic mean of all valid pixels)"
        ),
        "weighting_method": config.weighting.method,
        "weighting_rho": config.weighting.rho,
        "qc_preset": config.qc.preset,
        "streams": ",".join(r.stream_id for r in recipes),
        "product_collections": json.dumps(
            {r.product: r.collection for r in recipes}, sort_keys=True
        ),
        "leap_seconds": config.time.leap_seconds,
        "config_sha256": config_sha256(config),
        "processing_sha256": processing_sha256(config),
        "source_granule_count": source_count,
        "edge_granules_missing": json.dumps(missing),
    }


def _already_complete(config: RunConfig, files: list[Path], day: date) -> bool:
    expected = processing_sha256(config)
    for index, path in enumerate(files):
        if not path.is_file() or not validate_contract_file(path)[0]:
            return False
        with netCDF4.Dataset(path) as dataset:
            if getattr(dataset, "processing_sha256", None) != expected:
                return False
            if float(dataset["time"][...]) != cf_hours(window_bounds(day, index)[2]):
                return False
    return True


def grid_day(config: RunConfig, day: date, *, overwrite: bool = False) -> DayResult:
    """Process a day; any read/write error propagates to the day's caller."""
    if not config.period.start <= day <= config.period.end:
        raise ValueError(f"{day} outside configured period")
    recipes = stream_recipes(config)
    files = [output_path(config, window_bounds(day, i)[0]) for i in range(8)]
    if not overwrite and _already_complete(config, files, day):
        return DayResult(day=day, files=files, skipped=True)
    grid = load_grid(config.grid.source)
    inputs, missing, primary_count = discover_granules(config, day, recipes)
    if not primary_count:
        raise ValueError(f"no primary granules for {day}")
    a = Accumulators.create(len(recipes))
    native = Accumulators.create(3) if config.combined_product.enabled else None
    indices = {r.stream_id: i for i, r in enumerate(recipes)}
    counts: dict[str, dict[str, int]] = {r.stream_id: defaultdict(int) for r in recipes}
    edges = day_time_edges_seconds(day)
    for path, selected in inputs:
        if native is not None:
            accumulate_product(native, path, selected[0], config, day)
        batches = read_granule(
            path, selected, qc=config.qc, uncertainty=config.uncertainty, time=config.time, day=day
        )
        for stream, batch in batches.items():
            idx = indices[stream]
            accumulate(
                edges,
                grid.lat_edges,
                grid.lon_edges,
                batch.t,
                batch.lat,
                normalize_longitude(batch.lon),
                batch.aod,
                batch.sigma,
                idx,
                *a.arrays(),
            )
            rejected = batch.rejected_high
            count_pixels(
                edges,
                grid.lat_edges,
                grid.lon_edges,
                rejected.t,
                rejected.lat,
                normalize_longitude(rejected.lon),
                idx,
                a.n_rejected_high,
            )
            for key, value in batch.counts.items():
                counts[stream][key] += value
    kwargs = config.weighting.model_dump()
    kwargs["sigma_floor"] = config.uncertainty.sigma_floor
    merged = combine(a, **kwargs)
    instruments = {
        name: combine(
            a,
            stream_indices=[i for i, r in enumerate(recipes) if r.instrument.upper() == name],
            **kwargs,
        )
        for name in ["MODIS", "VIIRS"]
    }
    attrs = _provenance(config, recipes, len(inputs), missing)
    if native is not None:
        attrs["combined_product_config"] = config.combined_product.model_dump_json()
        attrs["combined_product_aggregation"] = (
            "arithmetic mean of native retrievals, Terra and Aqua pooled; independent of primary DT/DB merge"
        )
    for i, path in enumerate(files):
        extra = {
            "aod_chi2_per_dof": merged.chi2_per_dof[i],
            "aod_n_effective": merged.n_effective[i],
            "aod_count_negative": merged.count_negative[i],
            "aod_count_sigma_missing": merged.count_sigma_missing[i],
            "aod_count_rejected_high": merged.count_rejected_high[i],
        }
        for j, recipe in enumerate(recipes):
            extra[f"aod_count_{recipe.stream_id}"] = merged.stream_count[i, j]
            extra[f"aod_mean_{recipe.stream_id}"] = merged.stream_mean[i, j]
        write_contract_file(
            path,
            window_start=window_bounds(day, i)[0],
            aod_weighted_mean=merged.mean[i],
            aod_uncertainty=merged.sigma[i],
            aod_count=merged.count[i],
            aod_weighted_mean_modis=instruments["MODIS"].mean[i],
            aod_weighted_mean_viirs=instruments["VIIRS"].mean[i],
            global_attrs=attrs,
            extra_fields=extra,
            combined_product=product_window(native, i) if native is not None else None,
            clip_negative_mean=config.output.clip_negative_mean,
        )
        valid, errors = validate_contract_file(path)
        if not valid:
            raise ValueError(f"{path}: {errors}")
    return DayResult(
        day=day,
        files=files,
        source_granule_count=len(inputs),
        pixel_counts={s: dict(c) for s, c in counts.items()},
    )


def _run_day(config: RunConfig, day: date, overwrite: bool) -> DayResult:
    try:
        return grid_day(config, day, overwrite=overwrite)
    except Exception as exc:
        return DayResult(day=day, error=f"{type(exc).__name__}: {exc}")


def grid_period(
    config: RunConfig, *, day: date | None = None, overwrite: bool = False, workers: int = 1
) -> list[DayResult]:
    """Run independent days; retain successful output when another day fails."""
    if workers < 1:
        raise ValueError("workers must be positive")
    stream_recipes(config)
    days = [day] if day is not None else list(config.period.days())
    if workers == 1:
        return [_run_day(config, d, overwrite) for d in days]
    results = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_day, config, d, overwrite): d for d in days}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                results.append(DayResult(day=futures[future], error=f"{type(exc).__name__}: {exc}"))
    return sorted(results, key=lambda r: r.day)
