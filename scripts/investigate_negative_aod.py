"""Count accepted negative retrievals by stream and surface without changing QC."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path

import numpy as np

from sarb_aod.config.loader import load_config
from sarb_aod.pipeline import discover_granules, processing_sha256, stream_recipes
from sarb_aod.readers import read_granule
from sarb_aod.readers.base import LAND, OCEAN


def investigate(config, day):
    if not config.period.start <= day <= config.period.end:
        raise ValueError("day must lie within the configured period")
    recipes = stream_recipes(config)
    inputs, missing, primary = discover_granules(config, day, recipes)
    if not primary:
        raise ValueError(f"no primary granules for {day}")
    rows = {}
    for recipe in recipes:
        for surface in recipe.surfaces:
            rows[(recipe.stream_id, surface)] = {
                "run": config.name,
                "day": day.isoformat(),
                "stream": recipe.stream_id,
                "collection": recipe.collection,
                "surface": surface,
                "aod_field": recipe.surfaces[surface].aod.name,
                "n_accepted": 0,
                "n_negative": 0,
                "n_at_lower_limit": 0,
                "minimum": None,
                "first_negative_file": "",
            }
    for path, selected in inputs:
        batches = read_granule(
            path, selected, qc=config.qc, uncertainty=config.uncertainty, time=config.time, day=day
        )
        for stream, batch in batches.items():
            for surface, code in (("land", LAND), ("ocean", OCEAN)):
                if (stream, surface) not in rows:
                    continue
                row = rows[(stream, surface)]
                values = batch.aod[batch.surface == code]
                negative = int(np.count_nonzero(values < 0))
                row["n_accepted"] += len(values)
                row["n_negative"] += negative
                row["n_at_lower_limit"] += int(np.count_nonzero(values == config.qc.aod_min))
                if values.size:
                    row["minimum"] = min(
                        float(values.min()),
                        row["minimum"] if row["minimum"] is not None else float("inf"),
                    )
                if negative and not row["first_negative_file"]:
                    row["first_negative_file"] = str(path)
    for row in rows.values():
        row["negative_fraction"] = (
            row["n_negative"] / row["n_accepted"] if row["n_accepted"] else None
        )
    return list(rows.values()), {
        "run": config.name,
        "config": config.model_dump(mode="json"),
        "processing_sha256": processing_sha256(config),
        "day": day.isoformat(),
        "primary_granules": primary,
        "missing_edge_directories": list(map(str, missing)),
        "source_files": [str(path) for path, _ in inputs],
        "rows": list(rows.values()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows, evidence = investigate(load_config(args.config), args.day)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    with args.output.with_suffix(".tsv").open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(rows[0]), delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        print(row["stream"], row["surface"], row["n_negative"], row["minimum"], flush=True)


if __name__ == "__main__":
    main()
