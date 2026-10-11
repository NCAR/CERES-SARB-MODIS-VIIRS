"""Report aggregation, uncertainty sensitivity, precise gates, and CLI exports."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml
from typer.testing import CliRunner

from sarb_aod.cli import app
from sarb_aod.config.schema import EvaluationConfig, PathsConfig, RunConfig
from sarb_aod.evaluation import report
from sarb_aod.evaluation.collocate import MatchupResult, write_matchups
from sarb_aod.evaluation.report import (
    acceptance_gates,
    build_report,
    evaluate,
    load_matchups,
    matchup_signature,
    reuse_l2_matchups,
    write_report,
)
from sarb_aod.pipeline import processing_sha256


def config(tmp_path: Path, **evaluation) -> RunConfig:
    return RunConfig.model_validate(
        {
            "name": "test_run",
            "paths": {"data_root": str(tmp_path)},
            "period": {"start": "2008-07-01", "end": "2008-07-31"},
            "stage": {"products": {"MOD04_L2": ["6.1"]}},
            "grid": {"streams": {"terra_dt": {"product": "MOD04_L2", "collection": "6.1"}}},
            "evaluation": evaluation,
        }
    )


def frame(x=(0.1, 0.2, 0.3), y=None):
    left = np.asarray(x)
    return pd.DataFrame(
        {
            "x": left,
            "y": left + 0.01 if y is None else y,
            "sigma_x": np.full(len(left), 0.01),
            "sigma_y": np.full(len(left), 0.1),
            "x_no_extrapolated": left,
            "sigma_x_no_extrapolated": np.full(len(left), 0.01),
            "time": pd.date_range("2008-07-01", periods=len(left), freq="3h", tz="UTC"),
            "latitude": np.zeros(len(left)),
            "longitude": np.full(len(left), 10.0),
            "site": ["A"] * len(left),
            "sensor": ["MODIS"] * len(left),
            "stream": ["terra_dt"] * len(left),
            "collection": ["6.1"] * len(left),
            "surface": ["land"] * len(left),
            "algorithm": ["dt"] * len(left),
            "aeronet_level": ["AOD20"] * len(left),
        }
    )


def test_gates_use_unrounded_metrics_before_export(tmp_path):
    values = frame(y=np.array([0.1, 0.2, 0.3]) + 0.03004)
    result = build_report(values, config(tmp_path), level="grid")
    assert result.tables["summary"].iloc[0].MB == pytest.approx(0.03004)
    assert result.gates["checks"]["MB"]["status"] == "fail"
    assert result.gates["checks"]["RMSE"]["status"] == "pass"
    assert not result.gates["passed"]
    write_report(result, tmp_path, stem="test", precision=4)
    csv = pd.read_csv(result.files["summary"])
    assert csv.MB.iloc[0] == 0.03
    document = json.loads(result.files["report"].read_text())
    assert document["gates"]["checks"]["MB"]["value_unrounded"] == pytest.approx(0.03004)
    assert document["tables"]["summary"][0]["MB"] == 0.03
    assert document["tables_unrounded"]["summary"][0]["MB"] == pytest.approx(0.03004)
    assert result.tables["summary"].iloc[0].MB == pytest.approx(0.03004)


def test_acceptance_inclusive_boundaries_and_missing_values():
    assert acceptance_gates({"MB": -0.03, "RMSE": 0.1, "R": 0.8})["passed"]
    assert not acceptance_gates({"MB": 0, "RMSE": 0.1, "R": 0.79999})["passed"]
    result = acceptance_gates({"MB": np.nan, "RMSE": np.nan, "R": np.nan})
    assert not result["passed"]
    assert all(item["status"] == "insufficient_data" for item in result["checks"].values())


def test_global_is_not_sum_of_overlapping_regions(tmp_path):
    result = build_report(frame(), config(tmp_path), level="l2")
    regions = result.tables["pooled_regions"].set_index("region")
    assert set(regions.index) >= {
        "global",
        "north_africa",
        "southern_africa",
        "south_atlantic_ocean",
    }
    assert regions.loc["global", "N"] == 3
    assert result.tables["summary"].N.iloc[0] == 3
    assert regions.loc["north_africa", "N"] == 3
    assert result.tables["southern_ocean"].N.iloc[0] == 0


def test_regional_daily_aggregation_weights_sites_equally(tmp_path):
    values = frame(x=[0.1, 0.1, 0.1, 1.0])
    values["site"] = ["A", "A", "A", "B"]
    result = build_report(values, config(tmp_path, min_samples=1), level="grid")
    assert result.tables["summary"].MX.iloc[0] == pytest.approx(0.325)
    daily = result.tables["regional_daily"].query("region == 'global'")
    assert daily.x.iloc[0] == pytest.approx(0.55)
    regional = result.tables["regional_statistics"].query("region == 'global'").iloc[0]
    assert regional.N == regional.N_region_days == 1
    assert regional.N_site_days == regional.mean_active_sites == 2
    assert regional.N_pairs == 4


def test_daily_acceptance_uses_region_days_and_retains_site_day_diagnostics(tmp_path):
    values = frame(x=[0.1, 0.3, 0.2, 0.4, 0.3, 0.5], y=[0.3, 0.1, 0.4, 0.2, 0.5, 0.3])
    values["site"] = ["A", "B"] * 3
    values["time"] = pd.date_range("2008-07-01", periods=3, freq="D", tz="UTC").repeat(2)
    values["sigma_y"] = np.nan
    result = build_report(values, config(tmp_path), level="grid", pairing="daily")
    assert result.tables["summary"].N.iloc[0] == 6
    assert result.tables["summary"].RMSE.iloc[0] == pytest.approx(0.2)
    assert result.tables["summary"].N_z.iloc[0] == 0
    daily = result.tables["acceptance_summary"].iloc[0]
    assert daily.N == 3 and daily.N_site_days == 6
    assert daily.RMSE == pytest.approx(0)
    assert result.gates["passed"]
    assert result.gates["basis"] == "regional_daily"
    assert result.gates["purpose"] == "acceptance" and result.gates["sample_count"] == 3
    assert result.gates["checks"]["RMSE"]["value_unrounded"] == pytest.approx(0)
    regions = result.tables["regional_acceptance"].set_index("region")
    assert regions.loc["global", "gates_passed"]
    assert regions.loc["southern_ocean", "N"] == 0
    assert regions.loc["southern_ocean", "gate_R"] == "insufficient_data"
    assert "independent UTC-day" in result.provenance["daily_pairing"]
    assert "no temporal covariance" in result.provenance["daily_satellite_uncertainty"]


def test_daily_gates_preserve_unrounded_precision_and_minimum_day_count(tmp_path):
    values = frame(y=np.array([0.1, 0.2, 0.3]) + 0.03004)
    values["time"] = pd.date_range("2008-07-01", periods=3, freq="D", tz="UTC")
    result = build_report(values, config(tmp_path), level="grid", pairing="daily")
    assert result.gates["checks"]["MB"]["status"] == "fail"
    assert result.gates["checks"]["MB"]["value_unrounded"] == pytest.approx(0.03004)
    for sample in (values.iloc[:2], values.iloc[:0]):
        limited = build_report(sample, config(tmp_path), level="grid", pairing="daily")
        assert not limited.gates["passed"]
        assert limited.gates["sample_count"] == len(sample)
        assert limited.gates["checks"]["RMSE"]["status"] == "insufficient_data"
    with pytest.raises(ValueError, match="gridded"):
        build_report(values, config(tmp_path), level="l2", pairing="daily")
    with pytest.raises(ValueError, match="one independent row"):
        build_report(frame(), config(tmp_path), level="grid", pairing="daily")
    with pytest.raises(ValueError, match="daily-pairing"):
        build_report(
            values.assign(pairing="default"), config(tmp_path), level="grid", pairing="daily"
        )


def test_daily_matchup_reader_selects_distinct_verified_archive(tmp_path):
    cfg = config(tmp_path)
    paths = []
    for pairing in ("default", "daily"):
        values = frame()
        values["y"] += 1 if pairing == "daily" else 0
        path = tmp_path / f"evaluation/{cfg.name}/matchups_grid_{pairing}_20080701_20080731.nc"
        write_matchups(
            path,
            values,
            attributes={
                "level": "grid",
                "pairing": pairing,
                "config": cfg.model_dump(mode="json"),
                "processing_sha256": processing_sha256(cfg),
            },
        )
        paths.append(path)
    original = paths[0].read_bytes()
    daily = load_matchups(cfg, "grid", pairing="daily")
    legacy = load_matchups(cfg, "grid")
    np.testing.assert_allclose(daily.y - legacy.y, 1)
    assert paths[0].read_bytes() == original
    with pytest.raises(ValueError, match="Invalid matchup"):
        load_matchups(cfg, "l2", pairing="daily")


def test_asymmetric_ocean_expected_errors_and_ambiguous_surface_exclusion(tmp_path):
    values = frame(x=[0.5, 0.5, 0.5], y=[0.43, 0.59, 0.5901])
    values["surface"] = "ocean"
    result = build_report(values, config(tmp_path), level="l2")
    summary = result.tables["summary"].iloc[0]
    assert summary.N_EE == 3
    assert summary.pct_ee == pytest.approx(200 / 3)
    assert summary.fraction_above_ee == pytest.approx(1 / 3)
    assert summary.fraction_below_ee == 0
    values["surface"] = "coastal"
    coastal = build_report(values, config(tmp_path), level="l2").tables["summary"].iloc[0]
    assert coastal.N_EE == 0 and np.isnan(coastal.pct_ee)


def test_sensitivity_changes_sigma_without_changing_pairs(tmp_path):
    values = frame(y=[0.09, 0.2, 0.31])
    values["sigma_y"] = 0.01
    values.loc[1, "x_no_extrapolated"] = np.nan
    result = build_report(values, config(tmp_path), level="l2")
    sensitivity = result.tables["sensitivity"]
    configured = sensitivity.query("spectral_selection == 'include_extrapolated'").set_index(
        "instrument_sigma"
    )
    assert configured.loc[0.01, "z_std"] == pytest.approx(1 / np.sqrt(2))
    assert configured.loc[0.02, "z_std"] == pytest.approx(1 / np.sqrt(5))
    assert configured.N.eq(3).all()
    excluded = sensitivity.query("spectral_selection == 'exclude_extrapolated'")
    assert excluded.N.eq(2).all() and excluded.N_z.eq(2).all()
    assert excluded.z_std.isna().all()  # min_samples=3 still applies to diagnostics


def test_baseline_is_context_until_sp1_files_are_available(tmp_path):
    cfg = config(tmp_path)
    default = build_report(frame(), cfg, level="grid")
    assert default.baseline["status"] == "context_only"
    assert default.baseline["reference"]["N"] == 8875
    assert not default.baseline["input_files_verified"]
    cam7 = build_report(frame(), cfg, level="grid", pairing="cam7")
    assert cam7.baseline["status"] == "statistics_comparison_only"
    assert cam7.baseline["difference_from_reference"]["N"] == 3 - 8875


def test_sensitivity_preserves_including_values_when_default_excludes(tmp_path):
    cfg = config(tmp_path)
    cfg = cfg.model_copy(
        update={"aeronet": cfg.aeronet.model_copy(update={"include_extrapolated": False})}
    )
    values = frame()
    values["x_including_extrapolated"] = values["x"] + 0.1
    values["sigma_x_including_extrapolated"] = 0.01
    result = build_report(values, cfg, level="l2")
    selected = (
        result.tables["sensitivity"]
        .query("instrument_sigma == 0.01")
        .set_index("spectral_selection")
    )
    assert selected.loc["include_extrapolated", "MB"] == pytest.approx(-0.09)
    assert selected.loc["exclude_extrapolated", "MB"] == pytest.approx(0.01)


def test_empty_report_is_explicit_and_strict_json_has_no_nan(tmp_path):
    result = build_report(frame().iloc[:0], config(tmp_path), level="grid")
    assert result.tables["summary"].N.iloc[0] == 0
    assert not result.gates["passed"]
    write_report(result, tmp_path, stem="empty")
    text = result.files["report"].read_text()
    assert "NaN" not in text and "Infinity" not in text
    document = json.loads(text)
    assert document["tables"]["summary"][0]["MB"] is None
    assert document["gates"]["checks"]["R"]["status"] == "insufficient_data"
    assert document["plots"]["status"] == "not_requested"


def test_config_envelopes_are_independent_and_paths_are_validated(tmp_path):
    cfg = config(tmp_path)
    assert cfg.evaluation.min_samples == 3 and cfg.evaluation.round_precision == 4
    assert cfg.evaluation.ee["dt_ocean"].upper_intercept == 0.04
    assert cfg.uncertainty.envelopes["modis_dt"]["ocean"][0] == 0.03
    assert cfg.paths.evaluation_dir == "{data_root}/evaluation/{run}"
    for kwargs in (
        {"min_samples": 0},
        {"round_precision": 16},
        {"ee": {}},
        {"radius_km": 0},
        {"min_aeronet": True},
    ):
        with pytest.raises(ValueError):
            EvaluationConfig.model_validate(kwargs)
    with pytest.raises(ValueError, match="unknown placeholder"):
        PathsConfig(data_root=tmp_path, evaluation_dir="{data_root}/{unknown}")


def test_evaluate_exports_provenance_and_cli_reports_actual_gate_status(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    values = frame()
    calls = []

    def matchups(config, *, output_dir, options, **kwargs):
        calls.append((output_dir, options, kwargs))
        path = write_matchups(
            output_dir / "matchups.nc",
            values,
            attributes={"source_files": ["granule.hdf"], "aeronet_cache_files": ["AERONET.csv"]},
        )
        return MatchupResult(values, path)

    monkeypatch.setattr(report, "build_matchups", matchups)
    result = evaluate(cfg, level="l2")
    assert result.matchup_path.is_file()
    assert len(result.provenance["matchup_sha256"]) == 64
    assert result.provenance["config_sha256"]
    assert calls[0][0] == tmp_path / "evaluation/test_run"
    assert calls[0][1].min_aeronet == 2
    run = tmp_path / "run.yaml"
    run.write_text(yaml.safe_dump(cfg.model_dump(mode="json")))
    runner = CliRunner()
    accepted = runner.invoke(app, ["evaluate", "-c", str(run), "--level", "l2"])
    assert accepted.exit_code == 0, accepted.output
    assert "MB: pass" in accepted.output and "report:" in accepted.output
    values["y"] = values["x"] + 0.2
    failed = runner.invoke(app, ["evaluate", "-c", str(run), "--level", "l2"])
    assert failed.exit_code == 1 and "MB: fail" in failed.output
    invalid = runner.invoke(app, ["evaluate", "-c", str(run), "--level", "bad"])
    assert invalid.exit_code == 2


def test_cli_defaults_grid_to_daily_acceptance_and_keeps_explicit_diagnostics(
    tmp_path, monkeypatch
):
    cfg = config(tmp_path)
    values = frame()
    values["time"] = pd.date_range("2008-07-01", periods=3, freq="D", tz="UTC")
    called = []

    def evaluated(config, *, level, pairing):
        called.append((level, pairing))
        result = build_report(values, config, level=level, pairing=pairing)
        result.files["report"] = tmp_path / f"{pairing}_report.json"
        return result

    monkeypatch.setattr(report, "evaluate", evaluated)
    path = tmp_path / "run.yaml"
    path.write_text(yaml.safe_dump(cfg.model_dump(mode="json")))
    runner = CliRunner()
    daily = runner.invoke(app, ["evaluate", "-c", str(path)])
    assert daily.exit_code == 0, daily.output
    assert called[-1] == ("grid", "daily")
    assert "global daily means: N=3" in daily.output
    diagnostic = runner.invoke(app, ["evaluate", "-c", str(path), "--pairing", "default"])
    assert diagnostic.exit_code == 0, diagnostic.output
    assert called[-1] == ("grid", "default")
    assert "individual matchups (diagnostic)" in diagnostic.output
    invalid = runner.invoke(
        app, ["evaluate", "-c", str(path), "--level", "l2", "--pairing", "daily"]
    )
    assert invalid.exit_code == 2


def test_diagnostic_regional_plots_build_missing_daily_evaluation_without_recursion(
    tmp_path, monkeypatch
):
    from sarb_aod.evaluation import campaign
    from sarb_aod.plots import aeronet_suites

    cfg = config(tmp_path)
    cfg = cfg.model_copy(
        update={"plots": cfg.plots.model_copy(update={"suites": ["aeronet_regional_timeseries"]})}
    )
    built = []

    def matchups(run, *, output_dir, level, pairing, **kwargs):
        built.append((level, pairing, list(run.plots.suites)))
        values = frame()
        values["time"] = pd.date_range("2008-07-01", periods=3, freq="D", tz="UTC")
        values["pairing"] = pairing
        path = write_matchups(
            output_dir / f"matchups_{level}_{pairing}_20080701_20080731.nc",
            values,
            attributes={
                "source_files": [],
                "aeronet_cache_files": [],
                "config": run.model_dump(mode="json"),
                "processing_sha256": processing_sha256(run),
                "level": level,
                "pairing": pairing,
            },
        )
        return MatchupResult(values, path)

    def missing(*args, **kwargs):
        raise FileNotFoundError("Daily evaluation does not exist yet")

    rendered = []

    def render(run, **kwargs):
        rendered.append(kwargs["pairing"])
        daily = load_matchups(run, "grid", pairing="daily")
        assert len(daily) == 3
        assert (
            tmp_path / "evaluation/test_run/test_run_grid_daily_20080701_20080731_report.json"
        ).is_file()
        return []

    monkeypatch.setattr(report, "build_matchups", matchups)
    monkeypatch.setattr(campaign, "verified_tables", missing)
    monkeypatch.setattr(aeronet_suites, "render_aeronet_suites", render)
    result = evaluate(cfg, level="l2", coastal_sites={})
    assert built == [
        ("l2", "default", ["aeronet_regional_timeseries"]),
        ("grid", "daily", ["aod_maps"]),
    ]
    assert rendered == ["default"]
    assert result.plots["status"] == "protocol_passed"

    def stale(*args, **kwargs):
        raise ValueError("Daily scientific configuration is stale")

    monkeypatch.setattr(campaign, "verified_tables", stale)
    built.clear()
    with pytest.raises(ValueError, match="stale"):
        evaluate(cfg, level="l2", coastal_sites={})
    assert len(built) == 1 and built[0][0] == "l2"


def test_evaluate_links_verified_plots_and_records_rendering_failures(tmp_path, monkeypatch):
    from sarb_aod.plots import aeronet_suites

    cfg = config(tmp_path)
    cfg = cfg.model_copy(
        update={"plots": cfg.plots.model_copy(update={"suites": ["aeronet_scatter"]})}
    )

    def matchups(config, *, output_dir, **kwargs):
        values = frame()
        path = write_matchups(output_dir / "matchups.nc", values)
        return MatchupResult(values, path)

    monkeypatch.setattr(report, "build_matchups", matchups)
    result = evaluate(cfg, level="grid")
    document = json.loads(result.files["report"].read_text())
    assert document["plots"]["status"] == "protocol_passed"
    assert document["plots"]["visual_inspection"] == "pending"
    assert len(document["plots"]["artifacts"]) == 1
    for suffix in ("pdf", "png"):
        assert Path(document["plots"]["artifacts"][0][suffix]).is_file()
    assert json.loads(Path(document["plots"]["protocol_reports"][0]).read_text())["passed"]

    def fail(*args, **kwargs):
        raise ValueError("missing matchup evidence")

    monkeypatch.setattr(aeronet_suites, "render_aeronet_suites", fail)
    with pytest.raises(ValueError, match="missing matchup evidence"):
        evaluate(cfg, level="grid")
    document = json.loads(result.files["report"].read_text())
    assert document["plots"] == {"status": "failed", "error": "missing matchup evidence"}


def test_grid_evaluation_propagates_compatible_l2_coastal_evidence(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    directory = tmp_path / "evaluation/test_run"
    values = frame().assign(run=cfg.name, coastal_known=True, coastal_site=True)
    l2_path = directory / "matchups_l2_default_20080701_20080731.nc"
    write_matchups(
        l2_path,
        values,
        attributes={
            "level": "l2",
            "pairing": "default",
            "processing_sha256": processing_sha256(cfg),
            "config": cfg.model_dump(mode="json"),
        },
    )
    calls = []

    def matchups(config, *, output_dir, coastal_sites, **kwargs):
        calls.append(coastal_sites)
        path = write_matchups(output_dir / "matched.nc", values)
        return MatchupResult(values, path)

    monkeypatch.setattr(report, "build_matchups", matchups)
    result = evaluate(cfg, level="grid")
    assert calls == [{"A": True}]
    assert result.provenance["coastal_l2_file"] == str(l2_path)
    assert len(result.provenance["coastal_l2_sha256"]) == 64
    cfg = cfg.model_copy(
        update={"aeronet": cfg.aeronet.model_copy(update={"include_extrapolated": False})}
    )
    with pytest.raises(ValueError, match="Stale matchup"):
        evaluate(cfg, level="grid")


def test_l2_reuse_ignores_grid_weights_but_rejects_changed_retrieval(tmp_path):
    source = config(tmp_path)
    directory = tmp_path / "evaluation/test_run"
    values = frame().assign(run=source.name)
    source_path = directory / "matchups_l2_default_20080701_20080731.nc"
    write_matchups(
        source_path,
        values,
        attributes={
            "level": "l2",
            "pairing": "default",
            "processing_sha256": processing_sha256(source),
            "config": source.model_dump(mode="json"),
        },
    )
    target = source.model_copy(
        update={
            "name": "count",
            "weighting": source.weighting.model_copy(update={"method": "count"}),
        }
    )
    result = reuse_l2_matchups(source, target)
    assert result.frame.run.eq("count").all()
    pd.testing.assert_series_equal(result.frame.x, values.x)
    assert result.path.parent == tmp_path / "evaluation/count"
    import xarray as xr

    with xr.open_dataset(result.path) as dataset:
        assert dataset.attrs["reused_l2_file"] == str(source_path)
        assert len(dataset.attrs["reused_l2_sha256"]) == 64
    changed = target.model_copy(update={"qc": target.qc.model_copy(update={"preset": "cam7_v2"})})
    with pytest.raises(ValueError, match="identical recipes"):
        reuse_l2_matchups(source, changed)
    with pytest.raises(ValueError, match="distinct source"):
        reuse_l2_matchups(source, source)


@pytest.mark.parametrize("retained", [False, True])
def test_l2_reuse_requires_sensitivity_candidates_when_extrapolation_excluded(tmp_path, retained):
    source = config(tmp_path)
    source = source.model_copy(
        update={"aeronet": source.aeronet.model_copy(update={"include_extrapolated": False})}
    )
    attributes = {
        "level": "l2",
        "pairing": "default",
        "config": source.model_dump(mode="json"),
        "processing_sha256": processing_sha256(source),
    }
    if retained:
        attributes["retains_sensitivity_candidates"] = 1
    write_matchups(
        tmp_path / "evaluation/test_run/matchups_l2_default_20080701_20080731.nc",
        frame(),
        attributes=attributes,
    )
    target = source.model_copy(update={"name": "target"})
    if retained:
        assert len(reuse_l2_matchups(source, target).frame) == 3
    else:
        with pytest.raises(ValueError, match="requires retained extrapolation sensitivity"):
            reuse_l2_matchups(source, target)


def test_sorted_json_metadata_preserves_order_independent_matchup_compatibility(tmp_path):
    original = config(tmp_path).model_dump(mode="json")
    original["stage"]["products"] = {"MOD04_L2": ["6.1"], "MYD04_L2": ["6.1"]}
    original["grid"]["streams"] = {
        "terra_dt": {"product": "MOD04_L2", "collection": "6.1"},
        "aqua_dt": {"product": "MYD04_L2", "collection": "6.1"},
    }
    cfg = RunConfig.model_validate(original)
    stored = RunConfig.model_validate_json(json.dumps(original, sort_keys=True))
    assert list(cfg.grid.streams) != list(stored.grid.streams)
    assert processing_sha256(cfg) != processing_sha256(stored)
    assert matchup_signature(cfg) == matchup_signature(stored)
    path = tmp_path / "evaluation/test_run/matchups_l2_default_20080701_20080731.nc"
    write_matchups(
        path,
        frame(),
        attributes={
            "level": "l2",
            "pairing": "default",
            "config": original,
            "processing_sha256": processing_sha256(cfg),
        },
    )
    assert len(load_matchups(cfg, "l2")) == 3


def test_l2_reuse_exact_stream_subset_recomputes_site_representativeness(tmp_path):
    from sarb_aod.evaluation.collocate import flag_representativeness

    raw = config(tmp_path).model_dump(mode="json")
    raw["grid"]["streams"]["terra_db"] = {"product": "MOD04_L2", "collection": "6.1"}
    source = RunConfig.model_validate(raw)
    values = frame(x=[0.1, 0.2, 0.3, 0.4]).assign(
        run=source.name,
        level="l2",
        elevation=0.0,
        lat_index=0,
        lon_index=0,
        window_start=pd.Timestamp("2008-07-01", tz="UTC"),
        site=["A", "A", "A", "B"],
        stream=["terra_dt", "terra_db", "terra_db", "terra_db"],
        surface=["land", "coastal", "coastal", "land"],
    )
    values = flag_representativeness(values)
    assert values.loc[values.site == "A", "coastal_site"].all()
    assert values.active_sites_in_cell.eq(2).all()
    path = tmp_path / "evaluation/test_run/matchups_l2_default_20080701_20080731.nc"
    write_matchups(
        path,
        values,
        attributes={
            "level": "l2",
            "pairing": "default",
            "config": source.model_dump(mode="json"),
            "processing_sha256": processing_sha256(source),
        },
    )
    target = source.model_copy(
        update={
            "name": "dt_only",
            "grid": source.grid.model_copy(
                update={"streams": {"terra_dt": source.grid.streams["terra_dt"]}}
            ),
        }
    )
    result = reuse_l2_matchups(source, target)
    assert list(result.frame.stream) == ["terra_dt"]
    assert result.frame.coastal_known.all() and not result.frame.coastal_site.any()
    assert result.frame.active_sites_in_cell.eq(1).all()
    assert not result.frame.multiple_sites.any()


def test_verified_legacy_numeric_strings_are_normalized_for_regional_statistics(tmp_path):
    from sarb_aod.evaluation.calculator import regional_daily_series

    cfg = config(tmp_path)
    values = frame().assign(region="global")
    for name in ("x", "y", "sigma_x", "sigma_y", "latitude", "longitude"):
        values[name] = values[name].astype(str)
    path = tmp_path / "evaluation/test_run/matchups_grid_default_20080701_20080731.nc"
    write_matchups(
        path,
        values,
        attributes={
            "level": "grid",
            "pairing": "default",
            "config": cfg.model_dump(mode="json"),
            "processing_sha256": processing_sha256(cfg),
        },
    )
    loaded = load_matchups(cfg, "grid")
    _, regional = regional_daily_series(loaded)
    assert regional.x.iloc[0] == pytest.approx(0.2)
    assert regional.y.iloc[0] == pytest.approx(0.21)
    assert loaded.collection.eq("6.1").all()


@pytest.mark.parametrize("invalid", [False, True])
def test_verified_legacy_boolean_strings_are_strictly_normalized(tmp_path, invalid):
    cfg = config(tmp_path)
    values = frame().assign(primary_eligible=["True", "False", "bad" if invalid else "False"])
    path = tmp_path / "evaluation/test_run/matchups_grid_default_20080701_20080731.nc"
    write_matchups(
        path,
        values,
        attributes={
            "level": "grid",
            "pairing": "default",
            "config": cfg.model_dump(mode="json"),
            "processing_sha256": processing_sha256(cfg),
        },
    )
    if invalid:
        with pytest.raises(ValueError, match="Invalid boolean values for primary_eligible"):
            load_matchups(cfg, "grid")
    else:
        loaded = load_matchups(cfg, "grid")
        assert loaded.primary_eligible.dtype == bool
        assert loaded.primary_eligible.tolist() == [True, False, False]
