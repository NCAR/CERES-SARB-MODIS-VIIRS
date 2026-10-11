"""Collect independent daily AERONET/grid acceptance without replacing historical diagnostics."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sarb_aod.config.loader import load_config
from sarb_aod.errors import SarbAodError
from sarb_aod.evaluation.campaign import json_values, verified_tables

if __package__:
    from .collect_evaluation_campaign import expected_campaign, write_tsv
else:
    from collect_evaluation_campaign import expected_campaign, write_tsv


def expected_daily_campaign():
    """The 23 original grid variants plus the separately plotted CAM7-v2 control."""
    yield from (name for name, level in expected_campaign() if level == "grid")
    yield "modis_l2_200807_cam7_v2"


def collect(config_dir: Path):
    evidence, metrics, regional, diagnostics = [], [], [], []
    for name in expected_daily_campaign():
        path = config_dir / f"{name}.yaml"
        record = {"run": name, "level": "grid", "pairing": "daily", "config": str(path)}
        try:
            config = load_config(path)
            verified = verified_tables(config, "grid", pairing="daily")
            record.update(
                status="verified",
                **{
                    key: value
                    for key, value in verified.items()
                    if key not in {"tables", "scope_sensitivities", "pairing"}
                },
            )
            for key in ("report", "matchup"):
                artifact = Path(record[key])
                if artifact.is_relative_to(config.paths.data_root):
                    record[key] = (
                        "{data_root}/" + artifact.relative_to(config.paths.data_root).as_posix()
                    )
            record["aggregation"] = {
                "reference": "Independent UTC-day AERONET mean at each site",
                "satellite": "Daily satellite mean in that site's nearest CAM grid cell",
                "cohort": "Site-days with both independent daily means",
                "acceptance": "Equal-site global daily regional means, one comparison per day",
                "regional": "Separate daily regional comparisons, never pooled across overlapping regions",
                "diagnostics": "Pooled site-day statistics retained separately",
            }
            record["processing"] = {
                "qc": config.qc.model_dump(mode="json"),
                "weighting": config.weighting.model_dump(mode="json"),
                "collections": {key: item.collection for key, item in config.grid.streams.items()},
            }
            summary = verified["tables"]["acceptance_summary"][0]
            metrics.append(
                {
                    "run": name,
                    **summary,
                    **{
                        f"gate_{metric}": verified["gates"]["checks"][metric]["status"]
                        for metric in ("MB", "RMSE", "R")
                    },
                    "gates_passed": verified["gates"]["passed"],
                }
            )
            regional.extend(
                {"run": name, **row} for row in verified["tables"]["regional_acceptance"]
            )
            diagnostics.append({"run": name, **verified["tables"]["summary"][0]})
            record["daily_acceptance"] = summary
            record["pooled_site_day_diagnostics"] = verified["tables"]["summary"][0]
        except FileNotFoundError as exc:
            record.update(status="missing", reason=str(exc))
        except (SarbAodError, ValueError, KeyError, OSError, TypeError) as exc:
            record.update(status="invalid", reason=str(exc))
        evidence.append(record)
        print(f"{name} daily: {record['status']}", flush=True)
    return json_values(
        {
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "complete": bool(evidence) and all(row["status"] == "verified" for row in evidence),
            "expected_reports": len(evidence),
            "verified_reports": sum(row["status"] == "verified" for row in evidence),
            "acceptance_basis": "independent daily sampling, then equal-site global daily regional means",
            "historical_diagnostics": "The default three-hour and L2 campaign artifacts remain unchanged.",
            "reports": evidence,
            "metrics": metrics,
            "regional_acceptance": regional,
            "pooled_site_day_diagnostics": diagnostics,
        }
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("config/runs"))
    parser.add_argument("--output-dir", type=Path, default=Path("docs/validation"))
    args = parser.parse_args()
    result = collect(args.config_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = "daily_acceptance_campaign"
    (args.output_dir / f"{stem}.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n"
    )
    write_tsv(args.output_dir / f"{stem}.tsv", result["metrics"])
    write_tsv(args.output_dir / f"{stem}_regional.tsv", result["regional_acceptance"])
    write_tsv(
        args.output_dir / f"{stem}_site_day_diagnostics.tsv", result["pooled_site_day_diagnostics"]
    )
    print(
        f"Verified {result['verified_reports']}/{result['expected_reports']} daily reports; "
        f"complete={result['complete']}"
    )


if __name__ == "__main__":
    main()
