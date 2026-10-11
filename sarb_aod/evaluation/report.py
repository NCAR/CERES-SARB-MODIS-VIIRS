"""Evaluation tables, unrounded VP2 gates, and reviewable input provenance.

CSV/JSON presentation follows NCAR/DAVINCI ``stats/output.py`` (Apache-2.0):
copy tables for formatting, preserve metric IDs, and export explicit missing
values. Core calculations retain full precision; rounding never affects gates.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

import numpy as np
import pandas as pd
import xarray as xr

from sarb_aod.config.loader import configuration_metadata
from sarb_aod.config.schema import RunConfig
from sarb_aod.contract import code_provenance
from sarb_aod.evaluation.calculator import (
    DEFAULT_AOD_BINS,
    MONTH_TO_METEOROLOGICAL_SEASON,
    StatisticsCalculator,
    StatisticsConfig,
    regional_daily_series,
    regional_statistics,
)
from sarb_aod.evaluation.collocate import (
    CollocationOptions,
    MatchupResult,
    build_matchups,
    flag_representativeness,
    write_matchups,
)
from sarb_aod.evaluation.metrics import STANDARD_METRICS, get_metric
from sarb_aod.evaluation.regions import REGIONS, region_mask
from sarb_aod.pipeline import config_sha256, processing_sha256, stream_recipes
from sarb_aod.provenance import runtime_global_attrs

SP1_BASELINE = {"N": 8875, "MB": 0.0195, "RMSE": 0.1464, "R": 0.8555}
_CONTEXT = ["configuration", "sensor", "stream", "collection", "aeronet_level"]
_COLLOCATION_FIELDS = (
    "radius_km",
    "time_minutes",
    "min_pixels",
    "min_aeronet",
    "min_aeronet_grid",
    "high_elevation_m",
)


@dataclass
class EvaluationReport:
    """Unrounded calculations plus their export destinations."""

    tables: dict[str, pd.DataFrame]
    gates: dict[str, Any]
    baseline: dict[str, Any]
    provenance: dict[str, Any]
    files: dict[str, Path] = field(default_factory=dict)
    matchup_path: Path | None = None
    plots: dict[str, Any] = field(default_factory=lambda: {"status": "not_requested"})


def acceptance_gates(summary: Mapping[str, float]) -> dict[str, Any]:
    """VP2 acceptance on unrounded values; undefined metrics cannot pass."""
    checks = {}
    for name, threshold, operation in (
        ("MB", 0.03, "absolute_le"),
        ("RMSE", 0.10, "le"),
        ("R", 0.8, "ge"),
    ):
        value = float(summary.get(name, np.nan))
        if not np.isfinite(value):
            status = "insufficient_data"
        else:
            accepted = (
                abs(value) <= threshold
                if operation == "absolute_le"
                else value <= threshold if operation == "le" else value >= threshold
            )
            status = "pass" if accepted else "fail"
        checks[name] = {
            "value_unrounded": value,
            "threshold": threshold,
            "comparison": operation,
            "status": status,
        }
    return {"passed": all(check["status"] == "pass" for check in checks.values()), "checks": checks}


def _prepared(frame: pd.DataFrame, config: RunConfig) -> pd.DataFrame:
    result = frame.copy()
    if not {"x", "y"} <= set(result):
        raise ValueError("Matchups require x (AERONET) and y (satellite)")
    for name in ("x", "y", "sigma_x", "sigma_y", "latitude", "longitude"):
        if name not in result:
            result[name] = np.nan
        result[name] = pd.to_numeric(result[name], errors="raise").astype(float)
    if "time" not in result:
        result["time"] = pd.NaT
    result["time"] = pd.to_datetime(result["time"], utc=True)
    for name in ("sensor", "stream", "collection", "surface", "algorithm", "aeronet_level", "site"):
        if name not in result:
            result[name] = "unknown"
    result["configuration"] = config.name
    result["qc_preset"] = config.qc.preset
    result["weighting"] = config.weighting.method
    result["rho"] = config.weighting.rho
    result["season"] = result["time"].dt.month.map(MONTH_TO_METEOROLOGICAL_SEASON)
    result["day"] = result["time"].dt.floor("D")
    bins = np.asarray(DEFAULT_AOD_BINS)
    index = np.searchsorted(bins, result["x"], side="right") - 1
    valid = np.isfinite(result["x"]) & (index >= 0) & (index < len(bins) - 1)
    result["aod_bin"] = np.where(valid, index, np.nan)
    result["ee_class"] = "not_applicable"
    for algorithm, surface, sensor, name in (
        ("dt", "land", None, "dt_land"),
        ("dt", "ocean", None, "dt_ocean"),
        ("db", "land", None, "db_land"),
        ("db", "ocean", "VIIRS", "viirs_db_water"),
    ):
        mask = result["algorithm"].eq(algorithm) & result["surface"].eq(surface)
        if sensor is not None:
            mask &= result["sensor"].eq(sensor)
        result.loc[mask, "ee_class"] = name
    result["ee_above"] = np.nan
    result["ee_below"] = np.nan
    finite = np.isfinite(result["x"]) & np.isfinite(result["y"])
    for name, envelope in config.evaluation.ee.items():
        lower = envelope.lower_intercept + envelope.lower_slope * result["x"]
        upper = envelope.upper_intercept + envelope.upper_slope * result["x"]
        mask = result["ee_class"].eq(name) & finite & (lower >= 0) & (upper >= 0)
        result.loc[mask, "ee_above"] = (
            result.loc[mask, "y"] > result.loc[mask, "x"] + upper[mask]
        ).astype(float)
        result.loc[mask, "ee_below"] = (
            result.loc[mask, "y"] < result.loc[mask, "x"] - lower[mask]
        ).astype(float)
    return result


def _summarize(
    frame: pd.DataFrame, calculator: StatisticsCalculator, groupby: list[str] | None = None
) -> pd.DataFrame:
    keys = groupby or []
    groups = frame.groupby(keys, dropna=False, observed=True, sort=True) if keys else [((), frame)]
    rows = []
    for key, subset in groups:
        labels = key if isinstance(key, tuple) else (key,)
        row = dict(zip(keys, labels, strict=True))
        row.update(
            calculator.compute_summary(
                subset, sigma_sat="sigma_y", sigma_aer="sigma_x", include_regression=True
            )
        )
        usable = subset.loc[subset["ee_above"].notna() & subset["ee_below"].notna()]
        enough = len(usable) >= calculator.config.min_samples
        above = float(usable["ee_above"].mean()) if enough else np.nan
        below = float(usable["ee_below"].mean()) if enough else np.nan
        row.update(
            N_EE=len(usable),
            pct_ee=100 * (1 - above - below),
            fraction_above_ee=above,
            fraction_below_ee=below,
        )
        rows.append(row)
    if rows:
        return pd.DataFrame(rows)
    columns = _summarize(frame, calculator).columns
    return pd.DataFrame(columns=[*keys, *columns])


def _regional_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Expand only regional memberships; global gets each original row once."""
    pieces = [frame.assign(region="global")]
    times = frame["time"].dt.tz_convert(None).to_numpy(dtype="datetime64[ns]")
    for name in REGIONS:
        if name == "global":
            continue
        mask = region_mask(name, frame["longitude"], frame["latitude"], times=times)
        if mask.any():
            pieces.append(frame.loc[mask].assign(region=name))
    return pd.concat(pieces, ignore_index=True)


def _sensitivities(
    frame: pd.DataFrame, config: RunConfig, calculator: StatisticsCalculator
) -> pd.DataFrame:
    rows = []
    for selection in ("include_extrapolated", "exclude_extrapolated"):
        alternate = frame.copy()
        if selection == "exclude_extrapolated":
            alternate["x"] = alternate.get("x_no_extrapolated", np.nan)
            alternate["sigma_x"] = alternate.get("sigma_x_no_extrapolated", np.nan)
        else:
            alternate["x"] = alternate.get(
                "x_including_extrapolated",
                frame["x"] if config.aeronet.include_extrapolated else np.nan,
            )
            alternate["sigma_x"] = alternate.get(
                "sigma_x_including_extrapolated",
                frame["sigma_x"] if config.aeronet.include_extrapolated else np.nan,
            )
        # Recompute applicable EE after changing x; limits depend on AERONET AOD.
        alternate = _prepared(alternate, config)
        original = alternate["sigma_x"].to_numpy(dtype=float)
        spread_variance = np.maximum(original**2 - config.aeronet.instrument_sigma**2, 0)
        for instrument in dict.fromkeys((config.aeronet.instrument_sigma, 0.02)):
            alternate["sigma_x"] = np.sqrt(spread_variance + instrument**2)
            row = _summarize(alternate, calculator).iloc[0].to_dict()
            row.update(spectral_selection=selection, instrument_sigma=instrument)
            rows.append(row)
    return pd.DataFrame(rows)


def build_report(
    frame: pd.DataFrame,
    config: RunConfig,
    *,
    level: Literal["l2", "grid"],
    pairing: Literal["default", "cam7", "daily"] = "default",
    provenance: Mapping[str, Any] | None = None,
) -> EvaluationReport:
    """Compute report tables without filesystem or plotting side effects."""
    if pairing == "daily" and level != "grid":
        raise ValueError("Daily pairing is defined only for gridded products")
    statistics = StatisticsConfig(
        min_samples=config.evaluation.min_samples, round_precision=config.evaluation.round_precision
    )
    calculator = StatisticsCalculator(statistics)
    candidates = _prepared(frame, config)
    if pairing == "daily":
        if "pairing" in candidates and not candidates["pairing"].eq("daily").all():
            raise ValueError("Daily reports require independent daily-pairing rows")
        if candidates.duplicated(["site", "day", "aeronet_level"]).any():
            raise ValueError("Daily reports require one independent row per site and UTC day")
    prepared = candidates.loc[np.isfinite(candidates["x"]) & np.isfinite(candidates["y"])].copy()
    regions = _regional_rows(prepared)
    summary = _summarize(prepared, calculator)
    tables = {"summary": summary}
    for name, keys in {
        "configuration": ["configuration", "qc_preset", "weighting", "rho"],
        "sensor": ["sensor"],
        "stream": ["stream"],
        "collection": ["collection"],
        "surface": ["surface"],
        "season": ["season"],
        "day": ["day"],
        "site": ["site"],
        "aod_bin": ["aod_bin"],
        "aeronet_level": ["aeronet_level"],
        "ee_class": ["ee_class"],
    }.items():
        tables[f"by_{name}"] = _summarize(prepared, calculator, keys)
    tables["pooled_regions"] = _summarize(regions, calculator, ["region"])
    tables["southern_ocean"] = _summarize(
        regions.loc[regions["region"] == "southern_ocean"], calculator
    )
    site_days, region_days = regional_daily_series(regions, groupby=_CONTEXT)
    tables["site_daily"] = site_days
    tables["regional_daily"] = region_days
    tables["regional_statistics"] = regional_statistics(
        regions, groupby=_CONTEXT, config=statistics
    )
    gates = acceptance_gates(summary.iloc[0].to_dict())
    gates.update(
        basis="individual_matchups", purpose="diagnostic", sample_count=int(summary.N.iloc[0])
    )
    if pairing == "daily":
        # A day contributes once to each applicable region, regardless of
        # its active-site count. Regions overlap, so never pool their rows.
        _, acceptance_days = regional_daily_series(regions)
        regional_acceptance = []
        for region in REGIONS:
            daily = acceptance_days.loc[acceptance_days["region"] == region]
            values = calculator.compute(daily).iloc[0].to_dict()
            checks = acceptance_gates(values)
            regional_acceptance.append(
                {
                    "region": region,
                    **values,
                    "N_region_days": len(daily),
                    "N_site_days": int(daily["active_sites"].sum()),
                    "N_pairs": int(daily["N_pairs"].sum()),
                    "mean_active_sites": float(daily["active_sites"].mean()),
                    **{f"gate_{name}": gate["status"] for name, gate in checks["checks"].items()},
                    "gates_passed": checks["passed"],
                }
            )
        tables["regional_acceptance"] = pd.DataFrame(regional_acceptance)
        tables["acceptance_summary"] = (
            tables["regional_acceptance"]
            .loc[tables["regional_acceptance"]["region"] == "global"]
            .reset_index(drop=True)
        )
        gates = acceptance_gates(tables["acceptance_summary"].iloc[0].to_dict())
        gates.update(
            basis="regional_daily",
            region="global",
            purpose="acceptance",
            sample_count=int(tables["acceptance_summary"].N.iloc[0]),
        )
    tables["sensitivity"] = _sensitivities(candidates, config, calculator)
    for flag in ("high_elevation", "multiple_sites", "coastal_site", "coastal_known"):
        if flag in prepared:
            tables[f"by_{flag}"] = _summarize(prepared, calculator, [flag])
    baseline = {
        "status": "context_only",
        "input_files_verified": False,
        "reference_period": "2008-07-01/2008-07-31",
        "reference": SP1_BASELINE,
        "reason": "CAM7 July 2008 SP1 files are unavailable; matching published statistics does not verify identical inputs.",
        "current_pairing": pairing,
    }
    if (
        level == "grid"
        and pairing == "cam7"
        and str(config.period.start) == "2008-07-01"
        and str(config.period.end) == "2008-07-31"
    ):
        baseline["status"] = "statistics_comparison_only"
        baseline["difference_from_reference"] = {
            name: float(summary.iloc[0][name]) - value for name, value in SP1_BASELINE.items()
        }
    metadata = {
        "config_sha256": config_sha256(config),
        "config": config.model_dump(mode="json"),
        "run": config.name,
        "level": level,
        "pairing": pairing,
        "difference_convention": "y - x (satellite - AERONET)",
        "global_aggregation": "original matchups, never the sum of overlapping regions",
        "regional_aggregation": "paired site-day means, then equally weighted active sites per region-day",
        "expected_error_scope": "published envelope per unambiguous algorithm/surface; merged or coastal rows without an applicable envelope are excluded from N_EE",
        "sensitivity_scope": "full candidate cohort retained before the spectral minimum-count selection; primary tables and plots use only the configured eligible pairs",
        "candidate_count": len(candidates),
        "primary_count": len(prepared),
        "metric_names": {name: get_metric(name).long_name for name in STANDARD_METRICS},
        **dict(provenance or {}),
    }
    metadata.update(
        acceptance_basis=gates["basis"],
        gate_purpose=gates["purpose"],
        summary_scope=(
            "pooled independent site-days" if pairing == "daily" else "pooled individual matchups"
        ),
    )
    if pairing == "daily":
        metadata.update(
            global_aggregation="pooled site-days in summary; equal-site global day means in acceptance_summary; no overlapping-region duplication",
            daily_pairing="independent UTC-day means joined by site and day after averaging",
            satellite_daily_mean="pixel-count-weighted mean of all available three-hour grid fields at each site",
            aeronet_daily_mean="mean of all eligible AERONET observations in the UTC day, independent of satellite observation times",
            daily_satellite_uncertainty="unavailable: no temporal covariance model is assumed",
            sensitivity_scope=metadata["sensitivity_scope"]
            + "; pooled site-day diagnostics, not regional daily acceptance",
        )
    return EvaluationReport(tables, gates, baseline, metadata)


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return None if pd.isna(value) else pd.Timestamp(value).isoformat()
    if isinstance(value, np.generic):
        return _json_value(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, Path):
        return str(value)
    return value


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_report(
    report: EvaluationReport, output_dir: Path, *, stem: str, precision: int = 4
) -> EvaluationReport:
    """Export rounded CSV tables and one strict JSON report; gates stay unrounded."""
    if not stem or Path(stem).name != stem:
        raise ValueError("Report stem must be a nonempty filename component")
    presented = {}
    for name, table in report.tables.items():
        rounded = table.copy()
        numeric = rounded.select_dtypes(include="number").columns
        rounded[numeric] = rounded[numeric].round(precision)
        target = output_dir / f"{stem}_{name}.csv"
        _atomic_text(
            target, rounded.to_csv(index=False, float_format=f"%.{precision}f", na_rep="NA")
        )
        report.files[name] = target
        presented[name] = rounded.to_dict(orient="records")
    document = {
        "provenance": report.provenance,
        "gates": report.gates,
        "baseline": report.baseline,
        "tables": presented,
        "tables_unrounded": {
            name: table.to_dict(orient="records") for name, table in report.tables.items()
        },
        "csv_files": report.files,
        "plots": report.plots,
    }
    target = output_dir / f"{stem}_report.json"
    _atomic_text(target, json.dumps(_json_value(document), allow_nan=False, indent=2) + "\n")
    report.files["report"] = target
    return report


def evaluation_directory(config: RunConfig) -> Path:
    return Path(
        config.paths.evaluation_dir.format(data_root=str(config.paths.data_root), run=config.name)
    )


def matchup_signature(config: RunConfig) -> dict[str, Any]:
    # JSON metadata sorts mapping keys. Compare scientific definitions as
    # mappings here; preserve the original processing hash for file validation.
    processing = {
        key: getattr(config, key).model_dump(mode="json")
        for key in ("grid", "qc", "uncertainty", "weighting", "time", "output")
    }
    processing["recipes"] = {
        recipe.stream_id: {
            "product": recipe.product,
            "collection": recipe.collection,
            "file_format": recipe.file_format,
            "geolocation": recipe.geolocation.model_dump(mode="json"),
            "algorithm": recipe.algorithm_spec.model_dump(mode="json"),
        }
        for recipe in stream_recipes(config)
    }
    return {
        "processing": processing,
        "period": config.period.model_dump(mode="json"),
        "aeronet": config.aeronet.model_dump(mode="json"),
        "evaluation": config.evaluation.model_dump(mode="json"),
    }


def load_matchups(
    config: RunConfig,
    level: Literal["grid", "l2"],
    *,
    pairing: Literal["default", "cam7", "daily"] = "default",
) -> pd.DataFrame:
    """Read verified matchups for the requested pairing without altering archives."""
    if pairing not in {"default", "cam7", "daily"} or (level == "l2" and pairing != "default"):
        raise ValueError("Invalid matchup level or pairing")
    path = evaluation_directory(config) / (
        f"matchups_{level}_{pairing}_{config.period.start:%Y%m%d}_{config.period.end:%Y%m%d}.nc"
    )
    if not path.is_file():
        raise FileNotFoundError(f"Missing {level} matchups: {path}; run sarb-aod evaluate first")
    with xr.open_dataset(path) as dataset:
        if dataset.attrs.get("level") != level or dataset.attrs.get("pairing") != pairing:
            raise ValueError(f"Unexpected matchup level or pairing in {path}")
        stored = RunConfig.model_validate_json(dataset.attrs.get("config", "{}"))
        if matchup_signature(stored) != matchup_signature(config):
            raise ValueError(f"Stale matchup configuration in {path}; rerun evaluate")
        fingerprint = dataset.attrs.get("processing_sha256")
        if fingerprint is None:
            # Earlier evaluations recorded this hash in report provenance;
            # accept that evidence only when the report's file checksum matches.
            report_path = (
                evaluation_directory(config)
                / f"{config.name}_{level}_{pairing}_{config.period.start:%Y%m%d}_{config.period.end:%Y%m%d}_report.json"
            )
            if report_path.is_file():
                provenance = json.loads(report_path.read_text()).get("provenance", {})
                if (
                    provenance.get("matchup_sha256")
                    == hashlib.sha256(path.read_bytes()).hexdigest()
                ):
                    fingerprint = provenance.get("processing_sha256")
        if fingerprint != processing_sha256(config):
            raise ValueError(
                f"Missing or stale matchup processing provenance in {path}; rerun evaluate"
            )
        frame = dataset.to_dataframe().reset_index(drop=True)
        frame.attrs["source_provenance"] = {
            "path": str(path.resolve()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "date_created": str(dataset.attrs.get("date_created", "unknown")),
            "git_commit": str(dataset.attrs.get("git_commit", "unknown")),
            "level": level,
            "pairing": pairing,
        }
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    # Older monthly concatenations retained object dtype after empty windows;
    # their writer emitted numeric values as strings. Preserve verified input
    # files and normalize only declared numerical matchup fields on reading.
    numeric = {
        "x",
        "y",
        "z",
        "latitude",
        "longitude",
        "elevation",
        "lat_index",
        "lon_index",
        "n_satellite",
        "n_satellite_windows",
        "n_aod500",
        "n_angstrom",
        "aeronet_extrapolated_count",
        "active_sites_in_cell",
    }
    for name in frame.columns:
        if name in numeric or name.startswith(
            ("x_", "y_", "sigma_", "n_aeronet", "aod_count_", "z_")
        ):
            frame[name] = pd.to_numeric(frame[name], errors="raise")
    for name in (
        "primary_eligible",
        "high_elevation",
        "coastal_known",
        "coastal_site",
        "multiple_sites",
    ):
        if name in frame:
            tokens = frame[name].astype(str).str.lower()
            valid = {"true": True, "false": False, "1": True, "0": False}
            if not tokens.isin(valid).all():
                raise ValueError(f"Invalid boolean values for {name} in {path}")
            frame[name] = tokens.map(valid).astype(bool)
    return frame


def evaluate(
    config: RunConfig,
    *,
    level: Literal["l2", "grid"],
    pairing: Literal["default", "cam7", "daily"] = "default",
    coastal_sites: Mapping[str, bool] | None = None,
    reuse_l2_from: RunConfig | None = None,
) -> EvaluationReport:
    """Build matchups, tables and any configured AERONET plot suites."""
    output_dir = Path(
        config.paths.evaluation_dir.format(data_root=str(config.paths.data_root), run=config.name)
    )
    options = CollocationOptions(
        **{name: getattr(config.evaluation, name) for name in _COLLOCATION_FIELDS}
    )
    coastal_provenance: dict[str, Any] = {}
    if level == "grid" and coastal_sites is None:
        l2_path = (
            output_dir
            / f"matchups_l2_default_{config.period.start:%Y%m%d}_{config.period.end:%Y%m%d}.nc"
        )
        if l2_path.is_file():
            l2 = load_matchups(config, "l2")
            primary = l2.loc[
                np.isfinite(l2["x"].to_numpy(dtype=float))
                & np.isfinite(l2["y"].to_numpy(dtype=float))
            ]
            known = primary.loc[primary["coastal_known"].astype(bool)]
            coastal_sites = known.groupby("site")["coastal_site"].first().astype(bool).to_dict()
            coastal_provenance = {
                "coastal_l2_file": str(l2_path),
                "coastal_l2_sha256": hashlib.sha256(l2_path.read_bytes()).hexdigest(),
            }
    if reuse_l2_from is not None and (level != "l2" or pairing != "default"):
        raise ValueError("L2 reuse requires level=l2 and default pairing")
    matchups = (
        reuse_l2_matchups(reuse_l2_from, config)
        if reuse_l2_from is not None
        else build_matchups(
            config,
            level=level,
            pairing=pairing,
            output_dir=output_dir,
            options=options,
            coastal_sites=coastal_sites,
        )
    )
    with xr.open_dataset(matchups.path, decode_times=False) as dataset:
        sources = {
            key: dataset.attrs[key]
            for key in ("source_files", "aeronet_cache_files", "reused_l2_file", "reused_l2_sha256")
            if key in dataset.attrs
        }
    sources.update(
        matchup_file=str(matchups.path),
        matchup_sha256=hashlib.sha256(matchups.path.read_bytes()).hexdigest(),
        processing_sha256=processing_sha256(config),
    )
    sources.update(coastal_provenance)
    report = build_report(matchups.frame, config, level=level, pairing=pairing, provenance=sources)
    report.matchup_path = matchups.path
    stem = (
        f"{config.name}_{level}_{pairing}_{config.period.start:%Y%m%d}_{config.period.end:%Y%m%d}"
    )
    write_report(report, output_dir, stem=stem, precision=config.evaluation.round_precision)
    # Lazy import keeps the statistics API usable without initializing plotting.
    from sarb_aod.paths import plot_path
    from sarb_aod.plots.aeronet_suites import AERONET_SUITES, SuiteInputs, render_aeronet_suites

    selected = [suite for suite in config.plots.suites if suite in AERONET_SUITES]
    if selected:
        try:
            if "aeronet_regional_timeseries" in selected and pairing != "daily":
                # Regional figures always use independent daily means, even
                # when the requested diagnostic is L2 or a three-hour grid.
                # Build that additional comparison once when its archive or
                # report is absent; stale existing evidence must fail clearly.
                from sarb_aod.evaluation.campaign import verified_tables

                daily_archive = output_dir / (
                    f"matchups_grid_daily_{config.period.start:%Y%m%d}_{config.period.end:%Y%m%d}.nc"
                )
                if daily_archive.exists():
                    load_matchups(config, "grid", pairing="daily")
                try:
                    verified_tables(config, "grid", pairing="daily")
                except FileNotFoundError:
                    daily_config = config.model_copy(
                        update={"plots": config.plots.model_copy(update={"suites": ["aod_maps"]})}
                    )
                    evaluate(
                        daily_config, level="grid", pairing="daily", coastal_sites=coastal_sites
                    )
            artifacts = render_aeronet_suites(
                config,
                levels=(level,),
                inputs=SuiteInputs({level: matchups.frame}),
                pairing=pairing,
                evaluation_level=level,
            )
            report.plots = {
                "status": "protocol_passed",
                "pairing": pairing,
                "evaluation_level": level,
                "visual_inspection": "pending",
                "artifacts": [
                    {"suite": item.suite, "name": item.name, "pdf": item.pdf, "png": item.png}
                    for item in artifacts
                ],
                "protocol_reports": [
                    plot_path(
                        config, suite, "protocol", config.period.start, config.period.end
                    ).with_suffix(".json")
                    for suite in dict.fromkeys(item.suite for item in artifacts)
                ],
            }
        except Exception as exc:
            report.plots = {"status": "failed", "error": str(exc)}
            write_report(report, output_dir, stem=stem, precision=config.evaluation.round_precision)
            raise
        write_report(report, output_dir, stem=stem, precision=config.evaluation.round_precision)
    return report


def reuse_l2_matchups(source: RunConfig, target: RunConfig) -> MatchupResult:
    """Reuse verified L2 pairings when only gridding weights/output options differ.

    This avoids rereading the same granules for GLS rho and count comparisons.
    Target streams may be an exact subset of the source recipes. QC, pixel
    uncertainty, time, period and AERONET/matching settings must agree exactly.
    A changed CAM7 preset requires fresh pairing. Subsets recompute the site
    representativeness flags from their retained streams.
    """
    adjusted = target.model_copy(update={"weighting": source.weighting, "output": source.output})
    source_definition = matchup_signature(source)["processing"]
    target_definition = matchup_signature(adjusted)["processing"]
    source_recipes = source_definition.pop("recipes")
    target_recipes = target_definition.pop("recipes")
    source_definition["grid"].pop("streams")
    target_definition["grid"].pop("streams")
    subset = bool(target_recipes) and set(target_recipes) <= set(source_recipes)
    same_recipes = subset and all(
        source_recipes[name] == recipe for name, recipe in target_recipes.items()
    )
    if (
        not same_recipes
        or source_definition != target_definition
        or any(
            getattr(source, key) != getattr(target, key)
            for key in ("period", "aeronet", "evaluation")
        )
    ):
        raise ValueError(
            "L2 reuse requires identical recipes, QC, time, uncertainty and AERONET matching settings"
        )
    values = load_matchups(source, "l2")
    values = values.loc[values["stream"].isin(target_recipes)].copy()
    values["run"] = target.name
    if set(target_recipes) != set(source_recipes):
        values = flag_representativeness(
            values, high_elevation_m=target.evaluation.high_elevation_m
        )
    source_dir = Path(
        source.paths.evaluation_dir.format(data_root=str(source.paths.data_root), run=source.name)
    )
    target_dir = Path(
        target.paths.evaluation_dir.format(data_root=str(target.paths.data_root), run=target.name)
    )
    filename = f"matchups_l2_default_{target.period.start:%Y%m%d}_{target.period.end:%Y%m%d}.nc"
    source_path = source_dir / filename
    if source_path.resolve() == (target_dir / filename).resolve():
        raise ValueError("L2 reuse requires distinct source and target matchup paths")
    with xr.open_dataset(source_path, decode_times=False) as dataset:
        attributes = dict(dataset.attrs)
    if (
        not source.aeronet.include_extrapolated
        and attributes.get("retains_sensitivity_candidates") != 1
    ):
        raise ValueError(
            "L2 reuse requires retained extrapolation sensitivity candidates; rerun source evaluate"
        )
    source_provenance = _json_value(dict(attributes))
    attributes.update(
        **configuration_metadata(target),
        **code_provenance(),
        **runtime_global_attrs(),
        run=target.name,
        config_sha256=config_sha256(target),
        processing_sha256=processing_sha256(target),
        reused_l2_file=str(source_path),
        reused_l2_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
        reused_l2_streams=sorted(target_recipes),
        reused_l2_provenance=source_provenance,
    )
    path = write_matchups(target_dir / filename, values, attributes=attributes)
    return MatchupResult(values, path)
