#!/usr/bin/env python
"""Locate high AOD in configured CAM7 products without altering retrieval QC.

The distribution of written three-hour cell-window means and the distribution
of count-weighted monthly cell means are deliberately reported separately.
The latter is the field displayed by the period map suite.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import netCDF4
import numpy as np
import pandas as pd

from sarb_aod.config.loader import load_config
from sarb_aod.config.schema import RunConfig
from sarb_aod.contract import validate_contract_file
from sarb_aod.evaluation.regions import REGIONS, spatial_mask
from sarb_aod.grid.spec import load_grid
from sarb_aod.paths import output_path
from sarb_aod.pipeline import processing_sha256, require_processing_provenance
from sarb_aod.windows import window_bounds


def distribution(values: np.ndarray) -> dict[str, float | None]:
    names = ["min", "p10", "p50", "p90", "p99", "p999", "max"]
    values = np.asarray(values, dtype=float)
    if not values.size:
        return {name: None for name in names}
    return dict(zip(names, map(float, np.percentile(values, [0, 10, 50, 90, 99, 99.9, 100]))))


def investigate(
    config: RunConfig, threshold: float = 3, examples_per_category: int = 12
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not np.isfinite(threshold) or threshold < 0 or examples_per_category < 1:
        raise ValueError("threshold must be finite and nonnegative; examples must be positive")
    grid = load_grid(config.grid.source)
    lon, lat = np.meshgrid((grid.lon + 180) % 360 - 180, grid.lat)
    streams = list(config.grid.streams)
    valid_count = 0
    all_values = []
    coverages = []
    records = []
    monthly_sum, monthly_count = np.zeros(grid.shape), np.zeros(grid.shape, dtype=np.int64)
    for day in config.period.days():
        for window in range(8):
            start = window_bounds(day, window)[0]
            path = output_path(config, start)
            valid, errors = validate_contract_file(path)
            assert valid, (path, errors)
            with netCDF4.Dataset(path) as ds:
                ds.set_auto_mask(False)
                require_processing_provenance(ds, config, start)
                mean, count = np.array(ds["aod_weighted_mean"][:]), np.array(ds["aod_count"][:])
                ok = np.isfinite(mean) & (mean > -900) & (count > 0)
                valid_count += int(ok.sum())
                all_values.append(mean[ok])
                coverages.append(float(ok.mean()))
                monthly_sum += np.where(ok, mean.astype(float) * count, 0.0)
                monthly_count += np.where(ok, count, 0)
                high = ok & (mean > threshold)
                frame = pd.DataFrame(
                    {
                        "start": start.isoformat(),
                        "latitude": lat[high],
                        "longitude": lon[high],
                        "aod": mean[high],
                        "count": count[high],
                        "cell_index": np.flatnonzero(high),
                    }
                )
                for name in [
                    "aod_uncertainty",
                    "aod_chi2_per_dof",
                    *[f"aod_mean_{s}" for s in streams],
                    *[f"aod_count_{s}" for s in streams],
                ]:
                    frame[name] = np.array(ds[name][:])[high]
                records.append(frame)
    high = pd.concat(records, ignore_index=True)
    presence = np.column_stack(
        [(high[f"aod_count_{s}"] > 0) & (high[f"aod_mean_{s}"] > -900) for s in streams]
    )
    raw_count_total = int(high[[f"aod_count_{s}" for s in streams]].to_numpy().sum())
    by_region = {
        key: int(spatial_mask(key, high.longitude, high.latitude).sum())
        for key in REGIONS
        if key not in {"global", "labor_day_2020_smoke"}
    }
    hotspots = (
        high.groupby("cell_index")
        .agg(
            latitude=("latitude", "first"),
            longitude=("longitude", "first"),
            windows_above_threshold=("aod", "size"),
            max_aod=("aod", "max"),
            mean_aod=("aod", "mean"),
            median_count=("count", "median"),
            total_count=("count", "sum"),
        )
        .sort_values(["windows_above_threshold", "total_count"], ascending=False)
        .head(10)
        .reset_index(drop=True)
    )
    monthly = np.divide(
        monthly_sum, monthly_count, out=np.full(grid.shape, np.nan), where=monthly_count > 0
    )
    monthly_high = np.isfinite(monthly) & (monthly > threshold)
    monthly_examples = pd.DataFrame(
        {
            "latitude": lat[monthly_high],
            "longitude": lon[monthly_high],
            "aod": monthly[monthly_high],
            "count": monthly_count[monthly_high],
        }
    ).sort_values(["aod", "count"], ascending=False)
    result: dict[str, Any] = {
        "processing_sha256": processing_sha256(config),
        "valid_cell_windows": valid_count,
        "cell_windows_above_threshold": len(high),
        "percent_cell_windows_above_threshold": (
            100 * len(high) / valid_count if valid_count else None
        ),
        "window_coverage_percent": {
            "min": min(coverages) * 100,
            "max": max(coverages) * 100,
            "windows_above_10_percent": sum(v > 0.1 for v in coverages),
        },
        "written_aod_distribution": distribution(np.concatenate(all_values)),
        "monthly_aod_distribution": distribution(monthly[np.isfinite(monthly)]),
        "high_aod_distribution": distribution(high.aod),
        "high_count_distribution": distribution(high["count"]),
        "high_count_eq_1": int((high["count"] == 1).sum()),
        "high_count_le_3": int((high["count"] <= 3).sum()),
        "high_count_ge_10": int((high["count"] >= 10).sum()),
        "high_count_ge_100": int((high["count"] >= 100).sum()),
        "high_one_estimable_stream": int((presence.sum(axis=1) == 1).sum()),
        "high_multiple_estimable_streams": int((presence.sum(axis=1) > 1).sum()),
        "regions": by_region,
        "recurring_high_cells": hotspots.to_dict("records"),
        "monthly_cells_above_threshold": int(monthly_high.sum()),
        "monthly_cells_with_data": int((monthly_count > 0).sum()),
        "monthly_high_examples": monthly_examples.head(10).to_dict("records"),
        "streams": {},
    }
    for index, stream in enumerate(streams):
        present = presence[:, index]
        result["streams"][stream] = {
            "present_high_cell_windows": int(present.sum()),
            "stream_mean_above_threshold_cell_windows": int(
                (present & (high[f"aod_mean_{stream}"] > threshold)).sum()
            ),
            "accepted_count_in_high_cells": int(high[f"aod_count_{stream}"].sum()),
            "fraction_raw_accepted_counts_in_high_cells": (
                float(high[f"aod_count_{stream}"].sum()) / raw_count_total
                if raw_count_total
                else None
            ),
            "mean_aod_distribution_in_high_cells": distribution(
                high.loc[present, f"aod_mean_{stream}"]
            ),
        }
    examples = []
    for category, frame in (
        (
            "largest_aod",
            high.sort_values(["aod", "count"], ascending=False).head(examples_per_category),
        ),
        (
            "largest_count",
            high.sort_values(["count", "aod"], ascending=False).head(examples_per_category),
        ),
    ):
        for row in frame.drop(columns="cell_index").to_dict("records"):
            examples.append({"configuration": config.name, "selection": category, **row})
    return result, examples


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        action="append",
        type=Path,
        required=True,
        help="Run YAML; repeat to compare runs",
    )
    parser.add_argument("--threshold", type=float, default=3)
    parser.add_argument("--output", type=Path, required=True, help="Summary JSON path")
    parser.add_argument(
        "--examples-output", type=Path, help="Example TSV; defaults to JSON stem plus _examples.tsv"
    )
    parser.add_argument("--examples-per-category", type=int, default=12)
    args = parser.parse_args()
    output: dict[str, Any] = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "threshold": args.threshold,
        "definition": "Strictly above the configured threshold in written aod_weighted_mean, with aod_count > 0. Percentages use all valid three-hour cell-windows. No additional filtering or clipping applied.",
        "distribution_note": "written_aod_distribution pools unweighted three-hour cell-window means. monthly_aod_distribution pools count-weighted period cell means, one value per cell with data.",
        "stream_counts_note": "Raw accepted retrieval counts, including missing uncertainty, are not GLS weights or unique physical footprints across algorithms.",
        "region_note": "Region rectangles overlap; regional counts are not additive.",
        "runs": {},
    }
    examples = []
    for config_path in args.config:
        config = load_config(config_path)
        if config.name in output["runs"]:
            raise ValueError("configuration names must be distinct")
        result, rows = investigate(config, args.threshold, args.examples_per_category)
        result["config"] = str(config_path)
        result["period"] = config.period.model_dump(mode="json")
        output["runs"][config.name] = result
        examples.extend(rows)
        print(
            config.name,
            "valid",
            result["valid_cell_windows"],
            "above threshold",
            result["cell_windows_above_threshold"],
            "monthly p99.9",
            result["monthly_aod_distribution"]["p999"],
            flush=True,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True, allow_nan=False) + "\n")
    target = args.examples_output or args.output.with_name(args.output.stem + "_examples.tsv")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w") as handle:
        columns = list(dict.fromkeys(key for row in examples for key in row)) or [
            "configuration",
            "selection",
            "start",
            "latitude",
            "longitude",
            "aod",
            "count",
        ]
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(examples)


if __name__ == "__main__":
    main()
