"""Reproduce the paired July maps, variant summaries and daily AERONET comparison.

Requires complete mask/mean grids and cached AERONET days. Counts are fixed
temporal weights. Outputs include own/common sampling metrics and PDF/PNG paths.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from sarb_aod.config.loader import load_config
from sarb_aod.despeckle import cell_areas
from sarb_aod.despeckle_comparison import aggregate_comparison
from sarb_aod.evaluation.calculator import quick_stats, regional_daily_series
from sarb_aod.evaluation.campaign import json_values
from sarb_aod.evaluation.report import evaluate, load_matchups
from sarb_aod.plots.despeckle import render_comparison
from sarb_aod.summarize import summarize


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("mask", "mean", "destination"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    evidence = args.destination
    evidence.mkdir(parents=True, exist_ok=True)
    pair_changes = []
    statistics = []
    regional_metrics = []
    map_metrics = []
    artifacts = []
    coverage = []
    for mode in ("mask", "mean"):
        cfg = load_config(getattr(args, mode))
        aggregate = aggregate_comparison(cfg)
        fields = aggregate.fields()
        areas = cell_areas(aggregate.grid)
        for scope, inside in [
            ("global", np.ones(aggregate.grid.shape, bool)),
            ("north65", np.broadcast_to((aggregate.grid.lat >= 65)[:, None], aggregate.grid.shape)),
        ]:
            row = {"mode": mode, "scope": scope}
            for name, values in fields.items():
                good = inside & np.isfinite(values)
                row[name] = (
                    float(np.average(values[good], weights=areas[good])) if good.any() else np.nan
                )
            map_metrics.append(row)
        for item in render_comparison(cfg, aggregate):
            artifacts.append(
                {
                    "mode": mode,
                    "name": item.name,
                    "pdf": str(item.pdf),
                    "png": str(item.png),
                    "protocol_passed": item.report["passed"],
                }
            )
        print("Plots", mode, flush=True)
        comparisons = {}
        for variant in ("unfiltered", "filtered"):
            for support in ("available", "common"):
                selected = cfg.model_copy(
                    update={
                        "evaluation": cfg.evaluation.model_copy(
                            update={"aod_variant": variant, "sample_support": support}
                        )
                    }
                )
                summary, rows = summarize(selected)
                coverage.append(
                    {
                        "mode": mode,
                        "variant": variant,
                        "support": support,
                        "path": str(summary),
                        **rows[-1],
                    }
                )
                report = evaluate(selected, level="grid", pairing="daily")
                frame = load_matchups(selected, "grid", pairing="daily")
                comparisons[variant, support] = frame
                for scope, subset in [
                    ("global", frame),
                    ("north65", frame.loc[frame.latitude >= 65]),
                ]:
                    daily, regional = regional_daily_series(subset.assign(region=scope))
                    statistics.append(
                        {
                            "mode": mode,
                            "variant": variant,
                            "support": support,
                            "scope": scope,
                            "site_days": len(daily),
                            "sites": daily.site.nunique(),
                            "days": len(regional),
                            "site_names": ",".join(sorted(daily.site.unique())),
                            **quick_stats(
                                regional.x, regional.y, min_samples=selected.evaluation.min_samples
                            ),
                        }
                    )
                regional = report.tables["regional_acceptance"].assign(
                    mode=mode, variant=variant, support=support
                )
                regional_metrics.extend(regional.to_dict("records"))
                print("Evaluated", mode, variant, support, flush=True)
            pd.DataFrame(statistics).to_csv(evidence / "aeronet_metrics.tsv", sep="\t", index=False)
        left = comparisons["unfiltered", "common"]
        right = comparisons["filtered", "common"]
        columns = ["site", "time", "x", "n_satellite", "n_satellite_windows"]
        left = (
            left.loc[np.isfinite(left.x) & np.isfinite(left.y), columns]
            .sort_values(["site", "time"])
            .reset_index(drop=True)
        )
        right = (
            right.loc[np.isfinite(right.x) & np.isfinite(right.y), columns]
            .sort_values(["site", "time"])
            .reset_index(drop=True)
        )
        pd.testing.assert_frame_equal(left, right)
        for support in ("available", "common"):
            before = comparisons["unfiltered", support]
            after = comparisons["filtered", support]
            before = before.loc[
                np.isfinite(before.x) & np.isfinite(before.y), ["site", "time", "y"]
            ]
            after = after.loc[np.isfinite(after.x) & np.isfinite(after.y), ["site", "time", "y"]]
            joined = before.merge(
                after, on=["site", "time"], suffixes=("_before", "_after"), validate="one_to_one"
            )
            difference = joined.y_after - joined.y_before
            pair_changes.append(
                {
                    "mode": mode,
                    "support": support,
                    "site_days_before": len(before),
                    "site_days_after": len(after),
                    "common_site_days": len(joined),
                    "changed_site_days": int((difference != 0).sum()),
                    "maximum_absolute_difference": float(difference.abs().max()),
                }
            )
        print("Verified common site-days and window weights", mode, flush=True)
        for name, rows in [
            ("aeronet_metrics", statistics),
            ("aeronet_pair_changes", pair_changes),
            ("regional_aeronet_metrics", regional_metrics),
            ("monthly_map_metrics", map_metrics),
            ("variant_summaries", coverage),
        ]:
            pd.DataFrame(rows).to_csv(evidence / f"{name}.tsv", sep="\t", index=False)
        (evidence / "plot_artifacts.json").write_text(
            json.dumps(json_values(artifacts), indent=2) + "\n"
        )


if __name__ == "__main__":
    main()
