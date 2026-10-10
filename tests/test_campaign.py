"""Campaign evidence requires complete matching artifacts and full precision."""

import hashlib
import json
from datetime import date
from pathlib import Path

import netCDF4
import numpy as np
import pandas as pd
import pytest
from test_pipeline import hdf_granule, pipeline_config

from sarb_aod.config.schema import Period, QCConfig, RunConfig
from sarb_aod.evaluation.campaign import (
    coverage,
    report_path,
    scope_rows,
    scope_sensitivities,
    verified_tables,
)
from sarb_aod.evaluation.collocate import write_matchups
from sarb_aod.evaluation.report import build_report, evaluation_directory, write_report
from sarb_aod.pipeline import processing_sha256


@pytest.fixture
def campaign(tmp_path):
    config = RunConfig.model_validate(
        {
            "name": "test_campaign",
            "paths": {"data_root": str(tmp_path)},
            "period": {"start": "2008-07-01", "end": "2008-07-01"},
            "stage": {"products": {"MOD04_L2": ["6.1"]}},
            "grid": {"streams": {"terra_dt": {"product": "MOD04_L2", "collection": "6.1"}}},
        }
    )
    frame = pd.DataFrame(
        {
            "x": [0.1, 0.2, 0.3],
            "y": [0.102345, 0.223456, 0.312345],
            "sigma_x": [0.01] * 3,
            "sigma_y": [0.020123] * 3,
            "time": pd.date_range("2008-07-01", periods=3, freq="3h", tz="UTC"),
            "latitude": [-50.0] * 3,
            "longitude": [0.0] * 3,
            "site": ["A"] * 3,
            "sensor": ["MODIS"] * 3,
            "stream": ["terra_dt"] * 3,
            "collection": ["6.1"] * 3,
            "surface": ["ocean"] * 3,
            "algorithm": ["dt"] * 3,
        }
    )
    directory = evaluation_directory(config)
    path = write_matchups(
        directory / "matchups_grid_default_20080701_20080701.nc",
        frame,
        attributes={
            "config": config.model_dump(mode="json"),
            "level": "grid",
            "pairing": "default",
            "processing_sha256": processing_sha256(config),
        },
    )
    result = build_report(
        frame,
        config,
        level="grid",
        provenance={
            "matchup_file": str(path),
            "matchup_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "processing_sha256": processing_sha256(config),
        },
    )
    write_report(
        result, directory, stem="test_campaign_grid_default_20080701_20080701", precision=4
    )
    return config, path, result


@pytest.mark.parametrize("legacy", [False, True])
def test_verified_report_preserves_unrounded_uncertainty(campaign, legacy):
    config, _, result = campaign
    path = report_path(config, "grid")
    if legacy:
        document = json.loads(path.read_text())
        del document["tables_unrounded"]
        path.write_text(json.dumps(document))
    actual = verified_tables(config, "grid")
    value = actual["tables"]["summary"][0]["z_std"]
    assert value == pytest.approx(result.tables["summary"].iloc[0].z_std, abs=1e-14)
    assert abs(value - round(value, 4)) > 1e-7
    assert actual["metric_source"] == (
        "verified_matchup_recalculation" if legacy else "tables_unrounded"
    )
    rows = scope_rows(actual["tables"])
    assert [row["N"] for row in rows] == [3, 3, 0]
    assert rows[2]["gate_R"] == "insufficient_data"


def test_netcdf_alone_does_not_count_as_completed(campaign):
    config, path, _ = campaign
    report_path(config, "grid").unlink()
    assert path.exists()
    with pytest.raises(FileNotFoundError):
        verified_tables(config, "grid")


def test_checksum_changed_after_report_is_rejected(campaign):
    config, path, _ = campaign
    with netCDF4.Dataset(path, "a") as dataset:
        dataset.setncattr("new_attribute", "changed")
    with pytest.raises(ValueError, match="checksum"):
        verified_tables(config, "grid")


def test_report_digest_matches_parsed_snapshot_during_plot_update(campaign, monkeypatch):
    from sarb_aod.evaluation import campaign as module

    config, _, _ = campaign
    path = report_path(config, "grid")
    original = path.read_bytes()
    load = module.load_matchups

    def update_plot_metadata(config, level):
        document = json.loads(path.read_text())
        document["plots"] = {"status": "new plot metadata"}
        path.write_text(json.dumps(document))
        return load(config, level)

    monkeypatch.setattr(module, "load_matchups", update_plot_metadata)
    result = verified_tables(config, "grid")
    assert result["report_sha256"] == hashlib.sha256(original).hexdigest()
    assert result["report_sha256"] != hashlib.sha256(path.read_bytes()).hexdigest()


def test_atomic_matchup_replacement_during_load_is_rejected(campaign, monkeypatch):
    from sarb_aod.evaluation import campaign as module

    config, path, _ = campaign
    original_load = module.load_matchups

    def replace_matchups(config, level):
        replacement = path.with_suffix(".replacement.nc")
        replacement.write_bytes(path.read_bytes())
        with netCDF4.Dataset(replacement, "a") as dataset:
            dataset["y"][:] += 0.1
        replacement.replace(path)
        return original_load(config, level)

    monkeypatch.setattr(module, "load_matchups", replace_matchups)
    with pytest.raises(ValueError, match="changed while loading"):
        verified_tables(config, "grid")


def test_changed_qc_is_rejected(campaign):
    config, _, _ = campaign
    changed = config.model_copy(update={"qc": QCConfig(preset="cam7_v2")})
    with pytest.raises(ValueError, match="scientific configuration"):
        verified_tables(changed, "grid")


def test_changed_resolved_catalog_is_rejected(campaign, monkeypatch):
    config, _, _ = campaign
    monkeypatch.setattr("sarb_aod.evaluation.campaign.processing_sha256", lambda _: "changed-map")
    with pytest.raises(ValueError, match="resolved processing"):
        verified_tables(config, "grid")


def test_full_precision_table_must_agree_with_recorded_gate(campaign):
    config, _, _ = campaign
    path = report_path(config, "grid")
    document = json.loads(path.read_text())
    document["tables_unrounded"]["summary"][0]["MB"] += 0.001
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="recorded gate"):
        verified_tables(config, "grid")


def test_campaign_expected_inventory_is_unique_and_includes_controls():
    from scripts.collect_evaluation_campaign import expected_campaign

    entries = list(expected_campaign())
    assert len(entries) == len(set(entries)) == 32
    assert ("mixed_db21_l2_202009_gls_rho05", "l2") in entries
    assert ("modis_l2_200807_best_ocean", "l2") in entries
    assert ("mixed_l2_202009_gls_rho1", "grid") in entries


def test_scope_sensitivities_keep_candidates_and_respect_smoke_dates(campaign):
    config, _, _ = campaign
    values = pd.DataFrame(
        {
            "x": [np.nan, 1.0, 2.0],
            "y": [0.4, 1.1, 2.2],
            "sigma_x": [0.01] * 3,
            "sigma_y": [0.1] * 3,
            "x_including_extrapolated": [0.5, 1.0, 2.0],
            "sigma_x_including_extrapolated": [0.01] * 3,
            "x_no_extrapolated": [np.nan, 0.9, 1.9],
            "sigma_x_no_extrapolated": [np.nan, 0.01, 0.01],
            "time": pd.to_datetime(["2020-09-09", "2020-09-10", "2020-09-15"], utc=True),
            "longitude": [-120] * 3,
            "latitude": [40] * 3,
        }
    )
    report = build_report(values, config, level="grid")
    result = pd.DataFrame(
        scope_sensitivities(
            values,
            config,
            "grid",
            {name: table.to_dict("records") for name, table in report.tables.items()},
        )
    )
    selected = result.loc[result.instrument_sigma == config.aeronet.instrument_sigma].set_index(
        ["scope", "spectral_selection"]
    )
    assert selected.loc[("global", "include_extrapolated"), "N"] == 3
    assert selected.loc[("global", "exclude_extrapolated"), "N"] == 2
    assert selected.loc[("labor_day_2020_smoke", "include_extrapolated"), "N"] == 2
    assert selected.loc[("labor_day_2020_smoke", "exclude_extrapolated"), "N"] == 1
    assert selected.loc[("southern_ocean", "include_extrapolated"), "N"] == 0


def test_bad_config_is_recorded_without_discarding_valid_reports(campaign, tmp_path, monkeypatch):
    from scripts import collect_evaluation_campaign as collector

    config, _, _ = campaign
    directory = tmp_path / "configs"
    directory.mkdir()
    (directory / "broken.yaml").write_text("period: [unclosed")
    (directory / "test_campaign.yaml").write_text(config.model_dump_json())
    monkeypatch.setattr(
        collector,
        "expected_campaign",
        lambda: iter(
            [
                ("broken", "grid"),
                ("test_campaign", "grid"),
            ]
        ),
    )
    monkeypatch.setattr(
        collector, "coverage", lambda _: {"status": "unavailable", "reason": "not requested"}
    )
    result = collector.collect(directory)
    assert [row["status"] for row in result["reports"]] == ["invalid", "verified"]
    assert result["verified_reports"] == 1 and not result["complete"]
    assert len(result["metrics"]) == 3


def test_missing_coverage_keeps_campaign_incomplete_despite_verified_report(
    campaign, tmp_path, monkeypatch
):
    from scripts import collect_evaluation_campaign as collector

    config, _, _ = campaign
    (tmp_path / "test_campaign.yaml").write_text(config.model_dump_json())
    monkeypatch.setattr(collector, "expected_campaign", lambda: iter([("test_campaign", "grid")]))
    monkeypatch.setattr(
        collector, "coverage", lambda _: {"status": "unavailable", "reason": "stale summary"}
    )
    result = collector.collect(tmp_path)
    assert result["verified_reports"] == result["expected_reports"] == 1
    assert result["verified_grid_summaries"] == 0 and result["expected_grid_summaries"] == 1
    assert not result["complete"]


@pytest.mark.parametrize("corrupt", ["missing_window", "window_count", "period_mean"])
def test_coverage_verifies_summary_against_current_outputs(pipeline_config, corrupt):
    from sarb_aod.batch import grid_months

    config, _ = pipeline_config
    config = config.model_copy(
        update={"period": Period(start=date(2008, 7, 1), end=date(2008, 7, 1))}
    )
    result = list(grid_months(config))[0]
    assert result.passed
    actual = coverage(config)
    assert actual["windows"] == 8 and actual["status"] == "verified"
    table = pd.read_csv(result.summary_path, sep="\t")
    if corrupt == "missing_window":
        table = table.iloc[1:]
    elif corrupt == "window_count":
        table.loc[0, "aod_count"] += 1
    else:
        table.loc[table.kind == "period", "area_mean"] += 0.1
    table.to_csv(result.summary_path, sep="\t", index=False)
    with pytest.raises(ValueError, match="Grid summary"):
        coverage(config)


def test_summary_digest_matches_parsed_snapshot_during_replacement(pipeline_config, monkeypatch):
    from sarb_aod.batch import grid_months
    from sarb_aod.evaluation import campaign as module

    config, _ = pipeline_config
    config = config.model_copy(
        update={"period": Period(start=date(2008, 7, 1), end=date(2008, 7, 1))}
    )
    result = list(grid_months(config))[0]
    path = result.summary_path
    original = path.read_bytes()
    read_csv = module.pd.read_csv

    def replace_summary(*args, **kwargs):
        table = read_csv(*args, **kwargs)
        replacement = path.with_suffix(".replacement.tsv")
        replacement.write_bytes(original + b"changed after parsing\n")
        replacement.replace(path)
        return table

    monkeypatch.setattr(module.pd, "read_csv", replace_summary)
    actual = coverage(config)
    assert actual["sha256"] == hashlib.sha256(original).hexdigest()
    assert actual["sha256"] != hashlib.sha256(path.read_bytes()).hexdigest()
