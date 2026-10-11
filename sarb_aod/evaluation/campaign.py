"""Collect campaign evidence only from verified, complete evaluation artifacts."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any, Literal

import netCDF4
import numpy as np
import pandas as pd

from sarb_aod.config.schema import RunConfig
from sarb_aod.evaluation.regions import region_mask
from sarb_aod.evaluation.report import (
    acceptance_gates,
    build_report,
    evaluation_directory,
    load_matchups,
    matchup_signature,
)
from sarb_aod.grid.spec import load_grid
from sarb_aod.paths import output_path
from sarb_aod.pipeline import processing_sha256, require_processing_provenance
from sarb_aod.windows import window_bounds

Level = Literal["l2", "grid"]
Pairing = Literal["default", "daily", "cam7"]
SCOPES = ("global", "southern_ocean", "labor_day_2020_smoke")
METRICS = (
    "N",
    "MX",
    "MY",
    "MB",
    "RMSE",
    "R",
    "N_z",
    "z_mean",
    "z_std",
    "fraction_abs_z_lt_1",
    "fraction_abs_z_lt_2",
    "N_EE",
    "pct_ee",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_values(value: Any) -> Any:
    """Preserve full precision while representing missing metrics as JSON null."""
    if isinstance(value, dict):
        return {str(key): json_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_values(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def report_path(config: RunConfig, level: Level, *, pairing: Pairing = "default") -> Path:
    return evaluation_directory(config) / (
        f"{config.name}_{level}_{pairing}_"
        f"{config.period.start:%Y%m%d}_{config.period.end:%Y%m%d}_report.json"
    )


def verified_tables(
    config: RunConfig, level: Level, *, pairing: Pairing = "default"
) -> dict[str, Any]:
    """Validate report, configuration and matchup checksum before exposing metrics.

    Older reports rounded their tables. Recompute those tables from their verified
    matchup file, checking agreement with the original unrounded acceptance gates.
    A NetCDF without its report is incomplete, even when its data look usable.
    """
    path = report_path(config, level, pairing=pairing)
    # Evaluation can append plot metadata while collection runs. Bind parsing
    # and provenance to the same complete report snapshot.
    report_bytes = path.read_bytes()
    document = json.loads(report_bytes)
    provenance = document["provenance"]
    if any(
        provenance.get(key) != value
        for key, value in (("run", config.name), ("level", level), ("pairing", pairing))
    ):
        raise ValueError("Report run, level or pairing does not match the campaign")
    stored = RunConfig.model_validate(provenance["config"])
    if matchup_signature(stored) != matchup_signature(config):
        raise ValueError("Report scientific configuration is stale")
    if provenance.get("processing_sha256") != processing_sha256(config):
        raise ValueError("Report resolved processing configuration is stale")
    matchups = evaluation_directory(config) / (
        f"matchups_{level}_{pairing}_" f"{config.period.start:%Y%m%d}_{config.period.end:%Y%m%d}.nc"
    )
    if Path(provenance["matchup_file"]).resolve() != matchups.resolve():
        raise ValueError("Report points to an unexpected matchup file")
    digest = sha256(matchups)
    if digest != provenance.get("matchup_sha256"):
        raise ValueError("Report matchup checksum does not match")
    # Also verify NetCDF attributes, including resolved recipes. This check is
    # required even for reports that already contain unrounded tables.
    frame = (
        load_matchups(config, level)
        if pairing == "default"
        else load_matchups(config, level, pairing=pairing)
    )
    if sha256(matchups) != digest:
        raise ValueError("Matchup archive changed while loading; retry collection")
    if "tables_unrounded" in document:
        tables = document["tables_unrounded"]
        source = "tables_unrounded"
    else:
        rebuilt = build_report(frame, config, level=level, pairing=pairing)
        tables = {name: table.to_dict("records") for name, table in rebuilt.tables.items()}
        source = "verified_matchup_recalculation"
    summary = tables["summary"][0]
    if summary["N"] != document["tables"]["summary"][0]["N"]:
        raise ValueError("Unrounded table sample count disagrees with the report")
    gate_summary = tables["acceptance_summary"][0] if pairing == "daily" else summary
    if pairing == "daily" and (
        document["gates"].get("basis") != "regional_daily"
        or document["gates"].get("purpose") != "acceptance"
    ):
        raise ValueError("Daily report gate aggregation is not regional daily acceptance")
    if pairing == "daily" and gate_summary["N"] != document["tables"]["acceptance_summary"][0]["N"]:
        raise ValueError("Unrounded acceptance sample count disagrees with the report")
    for metric in ("MB", "RMSE", "R"):
        recorded = document["gates"]["checks"][metric]["value_unrounded"]
        actual = gate_summary[metric]
        if not np.isclose(
            np.nan if actual is None else actual,
            np.nan if recorded is None else recorded,
            atol=1e-13,
            rtol=1e-12,
            equal_nan=True,
        ):
            raise ValueError(f"Unrounded {metric} disagrees with the recorded gate")
    return json_values(
        {
            "report": str(path),
            "report_sha256": hashlib.sha256(report_bytes).hexdigest(),
            "matchup": str(matchups),
            "matchup_sha256": digest,
            "processing_sha256": provenance["processing_sha256"],
            "pairing": pairing,
            "metric_source": source,
            "gates": document["gates"],
            "tables": tables,
            "scope_sensitivities": scope_sensitivities(
                frame, config, level, tables, pairing=pairing
            ),
        }
    )


def scope_sensitivities(
    frame: pd.DataFrame,
    config: RunConfig,
    level: Level,
    tables: dict[str, Any],
    *,
    pairing: Pairing = "default",
) -> list[dict[str, Any]]:
    """Use archived candidate matchups for spectral and instrument sensitivities.

    Keep candidates whose configured primary x is missing: the alternative
    spectral selection may restore them. Region selection includes activity dates.
    """
    rows = []
    for scope in SCOPES:
        if scope == "global":
            sensitivities = tables["sensitivity"]
        else:
            mask = region_mask(
                scope,
                frame.longitude,
                frame.latitude,
                times=pd.to_datetime(frame.time, utc=True).dt.tz_convert(None).to_numpy(),
            )
            sensitivities = (
                build_report(frame.loc[mask], config, level=level, pairing=pairing)
                .tables["sensitivity"]
                .to_dict("records")
            )
        for values in sensitivities:
            gates = acceptance_gates(
                {
                    name: np.nan if values.get(name) is None else values[name]
                    for name in ("MB", "RMSE", "R")
                }
            )
            rows.append(
                {
                    "scope": scope,
                    **values,
                    **{
                        f"gate_{name}": gates["checks"][name]["status"]
                        for name in ("MB", "RMSE", "R")
                    },
                    "gates_passed": gates["passed"],
                    **(
                        {"acceptance_role": "diagnostic pooled site-days"}
                        if pairing == "daily"
                        else {}
                    ),
                }
            )
    return json_values(rows)


def scope_rows(tables: dict[str, Any], *, pairing: Pairing = "default") -> list[dict[str, Any]]:
    """Return explicit rows for all three scopes, including absent smoke samples."""
    rows = []
    for scope in SCOPES:
        if pairing == "daily":
            candidates = (
                tables["acceptance_summary"]
                if scope == "global"
                else [row for row in tables["regional_acceptance"] if row.get("region") == scope]
            )
        else:
            candidates = (
                tables["summary"]
                if scope == "global"
                else (
                    tables["southern_ocean"]
                    if scope == "southern_ocean"
                    else [row for row in tables["pooled_regions"] if row.get("region") == scope]
                )
            )
        metric = candidates[0] if candidates else {"N": 0, "N_z": 0, "N_EE": 0}
        gates = acceptance_gates(
            {key: np.nan if metric.get(key) is None else metric[key] for key in ("MB", "RMSE", "R")}
        )
        rows.append(
            {
                "scope": scope,
                **{name: metric.get(name) for name in METRICS},
                **{f"gate_{name}": gates["checks"][name]["status"] for name in ("MB", "RMSE", "R")},
                "gates_passed": gates["passed"],
            }
        )
    return json_values(rows)


def coverage(config: RunConfig) -> dict[str, Any]:
    """Read complete summaries, rejecting stale or incomplete underlying output.

    Coverage is the fraction of f09 cells, not an area-weighted fraction. Period
    coverage means observed at least once; mean window coverage measures sampling.
    """
    path = Path(
        config.paths.summary.format(
            data_root=config.paths.data_root,
            stem=config.output.stem,
            run=config.name,
            start=f"{config.period.start:%Y%m%d}",
            end=f"{config.period.end:%Y%m%d}",
        )
    )
    summary_bytes = path.read_bytes()
    table = pd.read_csv(io.BytesIO(summary_bytes), sep="\t")
    windows = table.loc[table.kind == "window"]
    period = table.loc[table.kind == "period"]
    expected = [window_bounds(day, index)[0] for day in config.period.days() for index in range(8)]
    if len(period) != 1 or list(windows.start) != [value.isoformat() for value in expected]:
        raise ValueError("Grid summary does not contain the complete configured period")
    if period.iloc[0].start != f"{config.period.start}/{config.period.end}":
        raise ValueError("Grid summary period does not match the configured period")
    grid = load_grid(config.grid.source)
    total_count = np.zeros(grid.shape, dtype=np.int64)
    total_sum = np.zeros(grid.shape)
    area = np.broadcast_to(np.cos(np.deg2rad(grid.lat))[:, None], grid.shape)
    values_seen = []
    stream_counts = {stream: 0 for stream in config.grid.streams}
    for start, (_, recorded) in zip(expected, windows.iterrows()):
        with netCDF4.Dataset(output_path(config, start)) as dataset:
            require_processing_provenance(dataset, config, start)
            dataset.set_auto_mask(False)
            mean = np.asarray(dataset["aod_weighted_mean"][:], dtype=float)
            count = np.asarray(dataset["aod_count"][:], dtype=np.int64)
            valid = (count > 0) & np.isfinite(mean) & (mean > -900)
            if int(count[valid].sum()) != recorded.aod_count or not np.isclose(
                valid.mean(), recorded.coverage, atol=1e-14, rtol=0
            ):
                raise ValueError("Grid summary coverage/count disagree with current outputs")
            for stream in stream_counts:
                amount = int(np.asarray(dataset[f"aod_count_{stream}"][:]).sum())
                if amount != recorded[f"aod_count_{stream}"]:
                    raise ValueError("Grid summary stream count disagrees with current outputs")
                stream_counts[stream] += amount
            total_count += np.where(valid, count, 0)
            total_sum += np.where(valid, mean * count, 0)
            values_seen.append(mean[valid])
    observed = total_count > 0
    means = np.divide(total_sum, total_count, out=np.full(grid.shape, np.nan), where=observed)
    values = np.concatenate(values_seen)
    measured = {
        "coverage": float(observed.mean()),
        "aod_count": int(total_count.sum()),
        "area_mean": (
            float(np.average(means[observed], weights=area[observed])) if observed.any() else np.nan
        ),
        "p90": float(np.percentile(values, 90)) if values.size else np.nan,
        "max": float(values.max()) if values.size else np.nan,
        **{f"aod_count_{stream}": value for stream, value in stream_counts.items()},
    }
    if any(
        not np.isclose(value, period.iloc[0][name], rtol=1e-12, atol=1e-14, equal_nan=True)
        for name, value in measured.items()
    ):
        raise ValueError("Grid summary period metrics disagree with current outputs")
    return json_values(
        {
            "status": "verified",
            "path": str(path),
            "sha256": hashlib.sha256(summary_bytes).hexdigest(),
            "windows": len(windows),
            "mean_window_coverage": float(windows.coverage.mean()),
            "ever_observed_coverage": float(period.iloc[0].coverage),
            "pixel_count": int(period.iloc[0].aod_count),
            "area_mean": float(period.iloc[0].area_mean),
            "p90": float(period.iloc[0].p90),
            "maximum": float(period.iloc[0]["max"]),
            "stream_counts": {
                name.removeprefix("aod_count_"): int(period.iloc[0][name])
                for name in table.columns
                if name.startswith("aod_count_")
            },
        }
    )
