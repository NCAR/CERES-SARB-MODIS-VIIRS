"""Reproduce numeric July despeckle audits and selected-day sensitivity.

Inputs are tracked configs plus the preselected, unfiltered case inventory.
Gridding, evaluation and PDF generation remain ordinary sarb-aod commands.
Sensitivity deliberately re-filters stored float32 *source* means only; it does
not reconstruct missing GLS coefficients or claim a full regridding experiment.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import netCDF4
import numpy as np
import pandas as pd

from sarb_aod.config.loader import load_config
from sarb_aod.contract import code_provenance, validate_contract_file
from sarb_aod.despeckle import filter_source, neighborhoods
from sarb_aod.evaluation.campaign import json_values, sha256
from sarb_aod.grid.spec import load_grid
from sarb_aod.paths import output_path
from sarb_aod.pipeline import processing_sha256, require_processing_provenance
from sarb_aod.windows import window_bounds


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "mask", "mean", "inventory", "destination"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--selected-only", action="store_true")
    parser.add_argument("--sensitivity", action="store_true")
    args = parser.parse_args()
    configs = {mode: load_config(getattr(args, mode)) for mode in ("baseline", "mask", "mean")}
    inventory = json.loads(args.inventory.read_text())
    selected = sorted(
        {
            datetime.fromisoformat(case["window_start"]).date()
            for case in inventory["cases"].values()
        }
    )
    days = selected if args.selected_only else list(configs["baseline"].period.days())
    grid = load_grid()
    band = np.broadcast_to((grid.lat >= 65)[:, None], grid.shape)
    files, source_rows, product_rows, case_rows = [], [], [], []
    for day in days:
        for index in range(8):
            start = window_bounds(day, index)[0]
            control = output_path(configs["baseline"], start)
            with netCDF4.Dataset(control) as ds:
                ds.set_auto_mask(False)
                baseline = {name: np.asarray(ds[name][:]) for name in ds.variables}
            for mode, cfg in configs.items():
                path = output_path(cfg, start)
                valid, errors = validate_contract_file(path)
                assert valid, (path, errors)
                with netCDF4.Dataset(path) as ds:
                    require_processing_provenance(ds, cfg, start)
                    ds.set_auto_mask(False)
                    for name, values in baseline.items():
                        np.testing.assert_array_equal(
                            ds[name][:], values, err_msg=f"{path}: {name}"
                        )
                    files.append(
                        {
                            "mode": mode,
                            "window": start.isoformat(),
                            "path": str(path),
                            "bytes": path.stat().st_size,
                            "sha256": sha256(path),
                            "git_commit": ds.git_commit,
                        }
                    )
                    if mode == "baseline":
                        continue
                    before = np.asarray(ds["aod_weighted_mean_unfiltered"][:])
                    after = np.asarray(ds["aod_weighted_mean_filtered"][:])
                    flags = np.asarray(ds["aod_despeckle_flags"][:])
                    np.testing.assert_array_equal(before[~band], after[~band])
                    assert np.all(after[before <= -900] == -999)
                    for scope, area in (("global", np.ones(grid.shape, bool)), ("north65", band)):
                        valid = area & (before > -900)
                        common = valid & (after > -900)
                        product_rows.append(
                            {
                                "mode": mode,
                                "window": start.isoformat(),
                                "scope": scope,
                                "original_cells": int(valid.sum()),
                                "retained_cells": int(common.sum()),
                                "removed_cells": int((valid & ~common).sum()),
                                "replaced_cells": int((valid & ((flags & 2) != 0)).sum()),
                                "changed_cells": int((valid & (flags > 0)).sum()),
                                "common_difference_sum": float(
                                    (after[common] - before[common]).sum()
                                ),
                            }
                        )
                    for source in cfg.grid.streams:
                        action = np.asarray(ds[f"aod_despeckle_flag_{source}"][:])
                        source_rows.append(
                            {
                                "mode": mode,
                                "window": start.isoformat(),
                                "source": source,
                                **{f"action_{i}": int((action == i).sum()) for i in range(5)},
                            }
                        )
                    for name, case in inventory["cases"].items():
                        if datetime.fromisoformat(case["window_start"]) != start:
                            continue
                        i, j = case["lat_index"], case["lon_index"]
                        for source in cfg.grid.streams:
                            case_rows.append(
                                {
                                    "mode": mode,
                                    "case": name,
                                    "source": source,
                                    "window": start.isoformat(),
                                    "product_before": float(before[i, j]),
                                    "product_after": float(after[i, j]),
                                    **{
                                        key: float(ds[variable][i, j])
                                        for key, variable in {
                                            "before": f"aod_mean_{source}_unfiltered",
                                            "after": f"aod_mean_{source}_filtered",
                                            "count": f"aod_count_{source}",
                                            "flag": f"aod_despeckle_flag_{source}",
                                            "support": f"aod_despeckle_support_fraction_{source}",
                                            "neighbors": f"aod_despeckle_neighbor_count_{source}",
                                            "donors": f"aod_despeckle_donor_count_{source}",
                                        }.items()
                                    },
                                }
                            )
        print(f"Audited {day}", flush=True)
    destination = args.destination
    destination.mkdir(parents=True, exist_ok=True)
    prefix = "selected" if args.selected_only else "monthly"
    for name, rows in (
        ("files", files),
        ("source_actions", source_rows),
        ("product_actions", product_rows),
        ("cases", case_rows),
    ):
        pd.DataFrame(rows).to_csv(destination / f"{prefix}_{name}.tsv", sep="\t", index=False)
    metadata = {
        "status": "passed",
        "baseline_arrays_equal": True,
        "outside_band_equal": True,
        "missing_targets_preserved": True,
        "files": len(files),
        "days_per_run": len(days),
        "configs": {
            mode: {
                "path": str(getattr(args, mode)),
                "sha256": sha256(getattr(args, mode)),
                "processing_sha256": processing_sha256(cfg),
            }
            for mode, cfg in configs.items()
        },
        "inventory_sha256": sha256(args.inventory),
        **code_provenance(),
    }
    (destination / f"{prefix}_audit.json").write_text(
        json.dumps(json_values(metadata), indent=2) + "\n"
    )
    if args.sensitivity:
        rows = []
        cfg = configs["mean"]
        settings = {"reference": cfg.despeckle}
        for radius in (100, 200):
            settings[f"radius_{radius}"] = cfg.despeckle.model_copy(update={"radius_km": radius})
        for support in (0.1, 0.3):
            settings[f"support_{support}"] = cfg.despeckle.model_copy(
                update={"min_valid_area_fraction": support}
            )
        settings["mad_4"] = cfg.despeckle.model_copy(
            update={"spike": cfg.despeckle.spike.model_copy(update={"mad_multiplier": 4.0})}
        )
        for day in selected:
            fields = {source: [] for source in cfg.grid.streams}
            stored_flags = {source: [] for source in cfg.grid.streams}
            for index in range(8):
                with netCDF4.Dataset(output_path(cfg, window_bounds(day, index)[0])) as ds:
                    ds.set_auto_mask(False)
                    for source in fields:
                        stored_flags[source].append(
                            np.asarray(ds[f"aod_despeckle_flag_{source}"][:])
                        )
                        fields[source].append(np.asarray(ds[f"aod_mean_{source}_unfiltered"][:]))
            for setting, values in settings.items():
                geometry = neighborhoods(grid, values)
                for source, windows in fields.items():
                    result = filter_source(np.asarray(windows), geometry, values)
                    rows.append(
                        {
                            "setting": setting,
                            "day": str(day),
                            "source": source,
                            "radius_km": values.radius_km,
                            "support": values.min_valid_area_fraction,
                            "mad_multiplier": values.spike.mad_multiplier,
                            "stored_flag_mismatches": (
                                int((result.flag != np.asarray(stored_flags[source])).sum())
                                if setting == "reference"
                                else None
                            ),
                            **{f"action_{i}": int((result.flag == i).sum()) for i in range(5)},
                        }
                    )
            print(f"Sensitivity {day}", flush=True)
        pd.DataFrame(rows).to_csv(
            destination / "sensitivity_source_actions.tsv", sep="\t", index=False
        )
        (destination / "sensitivity_settings.json").write_text(
            json.dumps(
                {
                    "scope": "Selected days; source fields from stored float32 unfiltered means; no combined-product recomputation",
                    "settings": {
                        name: value.model_dump(mode="json") for name, value in settings.items()
                    },
                },
                indent=2,
            )
            + "\n"
        )


if __name__ == "__main__":
    main()
