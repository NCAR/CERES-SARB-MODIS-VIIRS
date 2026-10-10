"""Reproduce the July/September campaign evidence without rerunning retrievals."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from sarb_aod.config.loader import load_config
from sarb_aod.errors import SarbAodError
from sarb_aod.evaluation.campaign import coverage, json_values, scope_rows, verified_tables

GRID_VARIANTS = (
    "gls_rho05",
    "gls_rho0",
    "count",
    "gls_rho025",
    "gls_rho075",
    "gls_rho1",
    "gls_rho05_min3",
    "gls_rho05_min5",
    "gls_rho05_nonnegative",
    "ocean_qa3",
)
L2_VARIANTS = ("gls_rho05", "ocean_qa3", "best_ocean", "cam7_qc")


def expected_campaign():
    for prefix in ("modis_l2_200807", "mixed_l2_202009"):
        for level, variants in (("grid", GRID_VARIANTS), ("l2", L2_VARIANTS)):
            for variant in variants:
                yield f"{prefix}_{variant}", level
    for prefix in ("modis_l2_202009", "viirs_l2_202009", "mixed_db21_l2_202009"):
        yield f"{prefix}_gls_rho05", "grid"
    yield "mixed_db21_l2_202009_gls_rho05", "l2"


def collect(config_dir: Path):
    evidence, metrics, coverage_rows, sensitivities = [], [], [], []
    coverage_cache = {}
    for name, level in expected_campaign():
        path = config_dir / f"{name}.yaml"
        record = {"run": name, "level": level, "config": str(path)}
        try:
            config = load_config(path)
            if name not in coverage_cache:
                try:
                    coverage_cache[name] = coverage(config)
                except (FileNotFoundError, OSError, ValueError, KeyError) as exc:
                    coverage_cache[name] = {"status": "unavailable", "reason": str(exc)}
            record["coverage"] = coverage_cache[name]
            verified = verified_tables(config, level)
            record.update(
                status="verified",
                **{
                    key: value
                    for key, value in verified.items()
                    if key not in {"tables", "scope_sensitivities"}
                },
            )
            record["reader"] = {
                "qc": config.qc.model_dump(mode="json"),
                "time": config.time.model_dump(mode="json"),
                "weighting": config.weighting.model_dump(mode="json"),
                "collections": {key: item.collection for key, item in config.grid.streams.items()},
            }
            record["details"] = {
                key: verified["tables"][key]
                for key in (
                    "by_stream",
                    "by_surface",
                    "by_sensor",
                    "by_collection",
                    "by_ee_class",
                    "by_aod_bin",
                    "regional_statistics",
                )
            }
            for row in scope_rows(verified["tables"]):
                metrics.append({"run": name, "level": level, **row})
            for row in verified["scope_sensitivities"]:
                sensitivities.append({"run": name, "level": level, **row})
        except FileNotFoundError as exc:
            record.update(status="missing", reason=str(exc))
        except (SarbAodError, ValueError, KeyError, OSError, TypeError) as exc:
            record.update(status="invalid", reason=str(exc))
        evidence.append(record)
        print(f"{name} {level}: {record['status']}", flush=True)
    for name, row in coverage_cache.items():
        if row["status"] == "verified":
            coverage_rows.append(
                {
                    "run": name,
                    **{
                        key: row[key]
                        for key in (
                            "windows",
                            "mean_window_coverage",
                            "ever_observed_coverage",
                            "pixel_count",
                            "area_mean",
                            "p90",
                            "maximum",
                        )
                    },
                }
            )
    return json_values(
        {
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "complete": all(
                row["status"] == "verified"
                and (row["level"] != "grid" or row.get("coverage", {}).get("status") == "verified")
                for row in evidence
            ),
            "expected_reports": len(evidence),
            "verified_reports": sum(row["status"] == "verified" for row in evidence),
            "expected_grid_summaries": sum(row["level"] == "grid" for row in evidence),
            "verified_grid_summaries": sum(
                row["level"] == "grid" and row.get("coverage", {}).get("status") == "verified"
                for row in evidence
            ),
            "reports": evidence,
            "metrics": metrics,
            "coverage": coverage_rows,
            "sensitivities": sensitivities,
        }
    )


def write_tsv(path: Path, rows):
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(rows[0]), delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("config/runs"))
    parser.add_argument("--output-dir", type=Path, default=Path("docs/validation"))
    args = parser.parse_args()
    result = collect(args.config_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "evaluation_campaign.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n"
    )
    write_tsv(args.output_dir / "evaluation_campaign.tsv", result["metrics"])
    write_tsv(args.output_dir / "evaluation_campaign_coverage.tsv", result["coverage"])
    write_tsv(args.output_dir / "evaluation_campaign_sensitivity.tsv", result["sensitivities"])
    print(
        f"Verified {result['verified_reports']}/{result['expected_reports']} reports; complete={result['complete']}"
    )


if __name__ == "__main__":
    main()
