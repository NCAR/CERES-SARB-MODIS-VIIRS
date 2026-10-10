"""Task 2.4: reproduce the September smoke tables without changing QC or inputs.

Run from the repository root with the sarb-aod environment Python. Use
--grids-only to refresh pending grid gates without rereading L2 granules.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import netCDF4
import numpy as np

from sarb_aod.config.loader import load_config
from sarb_aod.config.schema import VarRef
from sarb_aod.evaluation.verification import box_mask, grid_statistics, pixel_statistics
from sarb_aod.paths import output_path
from sarb_aod.pipeline import discover_granules, require_processing_provenance, stream_recipes
from sarb_aod.readers.base import assemble_batch
from sarb_aod.readers.modis_hdf4 import HDF4Source
from sarb_aod.readers.netcdf import NetCDFSource
from sarb_aod.windows import window_bounds


def write_tsv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(rows[0]), delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def days(start, end):
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def retrieval_definition(configs, start, end, bounds):
    """Resolve all reader settings and external catalog maps before hashing."""
    common = None
    recipes = {}
    for config in configs:
        settings = {
            "qc": config.qc.model_dump(mode="json"),
            "time": config.time.model_dump(mode="json"),
            "uncertainty": config.uncertainty.model_dump(mode="json"),
            "edge_granules": config.grid.edge_granules,
            "data_root": str(config.paths.data_root),
            "granule_dir": config.paths.granule_dir,
        }
        if common is not None and common != settings:
            raise ValueError("comparison configs require common reader settings and input paths")
        common = settings
        for recipe in stream_recipes(config):
            recipes[(recipe.stream_id, recipe.collection)] = {
                "stream": recipe.stream_id,
                "product": recipe.product,
                "collection": recipe.collection,
                "platform": recipe.platform,
                "instrument": recipe.instrument,
                "file_format": recipe.file_format,
                "geolocation": recipe.geolocation.model_dump(mode="json"),
                "algorithm": recipe.algorithm_spec.model_dump(mode="json"),
            }
    return {
        "version": 1,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "bounds": list(map(float, bounds)),
        "reader": common,
        "recipes": [recipes[key] for key in sorted(recipes)],
    }


def retrieval_fingerprint(definition):
    return hashlib.sha256(
        json.dumps(definition, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def save_retrieval_cache(path, rows, definition):
    write_tsv(path, rows)
    metadata = {
        "fingerprint": retrieval_fingerprint(definition),
        "summary_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "definition": definition,
    }
    path.with_suffix(".retrieval.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )


def load_retrieval_cache(path, definition):
    metadata_path = path.with_suffix(".retrieval.json")
    if not metadata_path.exists():
        raise ValueError("cached retrieval summary has no fingerprint; rerun the Level-2 scan")
    metadata = json.loads(metadata_path.read_text())
    if (
        metadata.get("fingerprint") != retrieval_fingerprint(definition)
        or metadata.get("definition") != definition
    ):
        raise ValueError(
            "cached retrieval fingerprint differs from the requested reader settings or resolved recipes; rerun the Level-2 scan"
        )
    if metadata.get("summary_sha256") != hashlib.sha256(path.read_bytes()).hexdigest():
        raise ValueError(
            "cached retrieval summary content differs from its fingerprint record; rerun the Level-2 scan"
        )
    with path.open() as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if not rows:
        raise ValueError("cached retrieval summary contains no stream rows")
    return rows


def retrieval_summary(configs, start, end, bounds):
    retrieval_definition(configs, start, end, bounds)
    base = configs[0]
    recipes = {}
    for config in configs:
        for recipe in stream_recipes(config):
            recipes[(recipe.stream_id, recipe.collection)] = recipe
    chunks, high_counts, granules = defaultdict(list), defaultdict(int), defaultdict(int)
    for day in days(start, end):
        inputs, _, primary = discover_granules(base, day, list(recipes.values()))
        if not primary:
            raise ValueError(f"no staged granules for {day}")
        for path, selected in inputs:
            source_type = HDF4Source if selected[0].file_format == "hdf4" else NetCDFSource
            with source_type(path, base.qc) as source:
                geo = selected[0].geolocation
                lat = source.read(VarRef(name=geo.lat), round_values=False)
                lon = source.read(VarRef(name=geo.lon), round_values=False)
                if not np.any(box_mask(lon, lat, bounds)):
                    continue
                for recipe in selected:
                    key = (recipe.stream_id, recipe.collection)
                    batch = assemble_batch(
                        source,
                        recipe,
                        qc=base.qc,
                        uncertainty=base.uncertainty,
                        time=base.time,
                        day=day,
                    )
                    values = batch.aod[box_mask(batch.lon, batch.lat, bounds)]
                    chunks[key].append(values)
                    high = batch.rejected_high
                    high_counts[key] += int(np.count_nonzero(box_mask(high.lon, high.lat, bounds)))
                    granules[key] += 1
        print(f"L2 {day}: scanned {primary} primary granules", flush=True)
    return [
        dict(
            start=start.isoformat(),
            end=end.isoformat(),
            west=bounds[0],
            east=bounds[1],
            south=bounds[2],
            north=bounds[3],
            stream=key[0],
            product=recipe.product,
            collection=key[1],
            aod_max=base.qc.aod_max,
            granules_in_box=granules[key],
            **pixel_statistics(
                chunks[key], aod_max=base.qc.aod_max, rejected_high=high_counts[key]
            ),
        )
        for key, recipe in recipes.items()
    ]


def inspect_grids(configs, start, end, bounds):
    rows = []
    for config in configs:
        for day in days(start, end):
            for index in range(8):
                window = window_bounds(day, index)[0]
                path = output_path(config, window)
                if not path.exists():
                    continue
                with netCDF4.Dataset(path) as dataset:
                    require_processing_provenance(dataset, config, window)
                    mask = box_mask(dataset["lon"][:][None, :], dataset["lat"][:][:, None], bounds)
                    stats = grid_statistics(
                        *(
                            dataset[name][:]
                            for name in (
                                "aod_weighted_mean",
                                "aod_weighted_mean_modis",
                                "aod_weighted_mean_viirs",
                                "aod_chi2_per_dof",
                                "aod_count_rejected_high",
                            )
                        ),
                        mask,
                    )
                rows.append(
                    dict(run=config.name, date=day.isoformat(), second=index * 10800, **stats)
                )
    return rows


def gates(retrievals, grids, configs, start, end):
    by_stream = {(row["stream"], row["collection"]): row for row in retrievals}
    comparisons = []
    for (stream, collection), row in by_stream.items():
        if not stream.endswith("_db"):
            continue
        dt = next(
            value for (name, _), value in by_stream.items() if name == stream.replace("_db", "_dt")
        )
        left = float(row["p90"]) if row["p90"] not in (None, "") else None
        right = float(dt["p90"]) if dt["p90"] not in (None, "") else None
        comparisons.append(
            dict(
                platform=stream.removesuffix("_db"),
                db_collection=collection,
                db_p90=left,
                dt_p90=right,
                passed=left is not None and right is not None and left > right,
            )
        )
    expected = len(days(start, end)) * 8
    runs = []
    for config in configs:
        rows = [row for row in grids if row["run"] == config.name]
        complete = len(rows) == expected
        runs.append(
            dict(
                run=config.name,
                windows=len(rows),
                expected_windows=expected,
                complete=complete,
                rejected_high=sum(row["n_rejected_high_global"] for row in rows),
                chi2_gt_one_box=sum(row["n_chi2_gt_one_box"] for row in rows),
                comparable_cells=sum(row["n_companion_cells_global"] for row in rows),
                outside_companions=sum(row["n_outside_companions_global"] for row in rows),
            )
        )
    complete = all(row["complete"] for row in runs)
    result = {
        "db_p90_exceeds_dt": all(row["passed"] for row in comparisons),
        "rejected_high_on_at_least_one_day": any(row["rejected_high"] > 0 for row in runs),
        "chi2_gt_one_in_smoke_box": all(row["chi2_gt_one_box"] > 0 for row in runs),
        "mixed_between_sensor_companions": all(
            row["comparable_cells"] > 0 and row["outside_companions"] == 0 for row in runs
        ),
    }
    return dict(
        start=start.isoformat(),
        end=end.isoformat(),
        complete=complete,
        accepted=complete and all(result.values()),
        gates=result,
        gate_states={
            name: (
                "pending"
                if not complete and name != "db_p90_exceeds_dt"
                else "passed" if passed else "failed"
            )
            for name, passed in result.items()
        },
        p90_comparisons=comparisons,
        runs=runs,
        convexity_tolerance=1e-6,
        native_valid_range_honored=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        action="append",
        default=None,
        help="Mixed run config; repeat for v2.1 comparison",
    )
    parser.add_argument("--start", type=date.fromisoformat, default=date(2020, 9, 8))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2020, 9, 14))
    parser.add_argument(
        "--box",
        type=float,
        nargs=4,
        default=(-125, -116, 36, 49),
        metavar=("WEST", "EAST", "SOUTH", "NORTH"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("docs/validation"))
    parser.add_argument("--grids-only", action="store_true")
    args = parser.parse_args()
    if args.end < args.start:
        parser.error("end must not precede start")
    configs = [
        load_config(path)
        for path in (
            args.config
            or [
                "config/runs/mixed_l2_202009_gls_rho05.yaml",
                "config/runs/mixed_db21_l2_202009_gls_rho05.yaml",
            ]
        )
    ]
    summary = args.output_dir / "sept2020_summary.tsv"
    definition = retrieval_definition(configs, args.start, args.end, args.box)
    if args.grids_only:
        retrievals = load_retrieval_cache(summary, definition)
        if not retrievals or any(
            row["start"] != args.start.isoformat()
            or row["end"] != args.end.isoformat()
            or [float(row[key]) for key in ("west", "east", "south", "north")] != list(args.box)
            or float(row["aod_max"]) != configs[0].qc.aod_max
            for row in retrievals
        ):
            raise ValueError(
                "cached retrieval summary does not match requested period, box or AOD maximum"
            )
    else:
        retrievals = retrieval_summary(configs, args.start, args.end, args.box)
        # Resolve again after the scan so concurrent catalog edits cannot
        # attach an old fingerprint to values read with changed definitions.
        if retrieval_definition(configs, args.start, args.end, args.box) != definition:
            raise ValueError("retrieval definition changed during the Level-2 scan")
        save_retrieval_cache(summary, retrievals, definition)
    grids = inspect_grids(configs, args.start, args.end, args.box)
    if grids:
        write_tsv(args.output_dir / "sept2020_grid_windows.tsv", grids)
    report = gates(retrievals, grids, configs, args.start, args.end)
    target = args.output_dir / "sept2020_verification.json"
    target.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
