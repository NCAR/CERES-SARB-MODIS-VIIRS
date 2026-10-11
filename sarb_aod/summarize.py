"""Numeric coverage and distribution summaries for complete CAM7 output periods."""

from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path
from typing import Any

import netCDF4
import numpy as np

from sarb_aod.config.schema import RunConfig
from sarb_aod.contract import validate_contract_file
from sarb_aod.grid.spec import load_grid
from sarb_aod.paths import output_path, summary_path
from sarb_aod.pipeline import require_processing_provenance
from sarb_aod.variants import read_grid_field
from sarb_aod.windows import window_bounds


def _distribution(values: np.ndarray) -> dict[str, float]:
    if not values.size:
        return {name: float("nan") for name in ["min", "p50", "p90", "p99", "p999", "max"]}
    quantiles = np.percentile(values, [0, 50, 90, 99, 99.9, 100])
    return dict(zip(["min", "p50", "p90", "p99", "p999", "max"], map(float, quantiles)))


def summarize(config: RunConfig) -> tuple[Path, list[dict[str, Any]]]:
    """Write per-window coverage plus the area-weighted count-weighted period mean."""
    grid = load_grid(config.grid.source)
    total_count = np.zeros(grid.shape, dtype=np.int64)
    total_sum = np.zeros(grid.shape)
    area = np.broadcast_to(np.cos(np.deg2rad(grid.lat))[:, None], grid.shape)
    rows = []
    observed = []
    contribution: dict[str, int] = {}
    for day in config.period.days():
        for window in range(8):
            start, _, _ = window_bounds(day, window)
            path = output_path(config, start)
            valid, errors = validate_contract_file(path)
            if not valid:
                raise ValueError(f"{path}: {errors}")
            with netCDF4.Dataset(path) as dataset:
                require_processing_provenance(dataset, config, start)
                dataset.set_auto_mask(False)
                mean = read_grid_field(dataset, config, "aod_weighted_mean")
                count = np.asarray(dataset["aod_count"][:], dtype=np.int64)
                ok = (count > 0) & np.isfinite(mean) & (mean > -900)
                total_count += np.where(ok, count, 0)
                total_sum += np.where(ok, mean * count, 0)
                values = mean[ok]
                observed.append(values)
                row = {
                    "kind": "window",
                    "start": start.isoformat(),
                    "coverage": float(ok.mean()),
                    "aod_count": int(count[ok].sum()),
                    "area_mean": (
                        float(np.average(mean[ok], weights=area[ok])) if ok.any() else float("nan")
                    ),
                    **_distribution(values),
                }
                for stream in config.grid.streams:
                    name = f"aod_count_{stream}"
                    amount = int(np.asarray(dataset[name][:]).sum())
                    row[name] = amount
                    contribution[name] = contribution.get(name, 0) + amount
                rows.append(row)
    cell_mean = np.divide(
        total_sum, total_count, out=np.full(grid.shape, np.nan), where=total_count > 0
    )
    ok = total_count > 0
    period_values = np.concatenate(observed) if observed else np.array([])
    period = {
        "kind": "period",
        "start": f"{config.period.start}/{config.period.end}",
        "coverage": float(ok.mean()),
        "aod_count": int(total_count.sum()),
        "area_mean": (
            float(np.average(cell_mean[ok], weights=area[ok])) if ok.any() else float("nan")
        ),
        **_distribution(period_values),
        **contribution,
    }
    rows.append(period)
    target = summary_path(config)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return target, rows
