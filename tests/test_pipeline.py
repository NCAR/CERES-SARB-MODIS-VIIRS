import shutil
from datetime import date
from pathlib import Path

import netCDF4
import numpy as np
import pytest
import yaml
from pydantic import ValidationError
from test_readers import hdf_granule

from sarb_aod.config.loader import load_config
from sarb_aod.config.schema import RunConfig, WeightingConfig
from sarb_aod.contract import validate_contract_file
from sarb_aod.paths import granule_dir
from sarb_aod.pipeline import config_sha256, grid_day, grid_period


@pytest.fixture
def pipeline_config(tmp_path, hdf_granule):
    path = tmp_path / "run.yaml"
    raw = {
        "paths": {"data_root": str(tmp_path / "data")},
        "period": {"start": "2008-07-01", "end": "2008-07-02"},
        "stage": {"products": {"MOD04_L2": ["6.1"]}},
        "grid": {
            "streams": {
                "terra_dt": {"product": "MOD04_L2", "collection": "6.1"},
                "terra_db": {"product": "MOD04_L2", "collection": "6.1"},
            }
        },
    }
    path.write_text(yaml.safe_dump(raw))
    config = load_config(path)
    dest = granule_dir(config.paths, config.load_catalog(), "MOD04_L2", "6.1", date(2008, 7, 1))
    dest.mkdir(parents=True)
    shutil.copyfile(hdf_granule, dest / "MOD04.synthetic.hdf")
    return config, path


def test_full_day_contract_and_idempotent_rerun(pipeline_config):
    config, _ = pipeline_config
    day = date(2008, 7, 1)
    result = grid_day(config, day)
    assert len(result.files) == 8 and result.source_granule_count == 1 and not result.skipped
    assert all(validate_contract_file(path) == (True, []) for path in result.files)
    with netCDF4.Dataset(result.files[0]) as dataset:
        dataset.set_auto_mask(False)
        assert dataset["aod_count"][:].sum() == 6
        assert dataset["aod_count_negative"][:].sum() == 1
        assert dataset["aod_count_sigma_missing"][:].sum() == 1
        assert np.any(dataset["aod_mean_terra_dt"][:] == np.float32(-0.05))
        assert dataset["aod_weighted_mean_viirs"][:].max() == -999
        assert dataset.config_sha256 == config_sha256(config)
    for path in result.files[1:]:
        with netCDF4.Dataset(path) as dataset:
            dataset.set_auto_mask(False)
            assert dataset["aod_count"][:].sum() == 0
    mtimes = [p.stat().st_mtime_ns for p in result.files]
    assert grid_day(config, day).skipped
    assert [p.stat().st_mtime_ns for p in result.files] == mtimes
    result.files[4].write_bytes(b"broken")
    assert not grid_day(config, day).skipped
    assert validate_contract_file(result.files[4])[0]


def test_changed_configuration_recomputes(pipeline_config):
    config, _ = pipeline_config
    grid_day(config, date(2008, 7, 1))
    config = config.model_copy(update={"weighting": WeightingConfig(method="count")})
    result = grid_day(config, date(2008, 7, 1))
    assert not result.skipped
    with netCDF4.Dataset(result.files[0]) as dataset:
        assert dataset.weighting_method == "count"


@pytest.mark.parametrize("workers", [1, 2])
def test_failed_day_preserves_successes(pipeline_config, workers):
    config, _ = pipeline_config
    results = grid_period(config, workers=workers)
    assert len(results) == 2
    assert results[0].error is None and len(results[0].files) == 8
    assert results[1].error and "no primary granules" in results[1].error


def test_missing_edge_directory_recorded(pipeline_config):
    config, _ = pipeline_config
    config = config.model_copy(
        update={"grid": config.grid.model_copy(update={"edge_granules": True})}
    )
    result = grid_day(config, date(2008, 7, 1))
    with netCDF4.Dataset(result.files[0]) as dataset:
        assert "2008-06-30" in dataset.edge_granules_missing


@pytest.mark.parametrize(
    "streams",
    [
        {"aqua_dt": {"product": "MOD04_L2", "collection": "6.1"}},
        {"terra_bad": {"product": "MOD04_L2", "collection": "6.1"}},
        {"terra_dt": {"product": "MOD04_L2", "collection": "7"}},
    ],
)
def test_invalid_stream_selection(pipeline_config, streams):
    config, _ = pipeline_config
    raw = config.model_dump(mode="json")
    raw["grid"]["streams"] = streams
    with pytest.raises((ValueError, KeyError)):
        RunConfig.model_validate(raw)


def test_stream_collection_must_be_staged(pipeline_config):
    config, _ = pipeline_config
    raw = config.model_dump(mode="json")
    raw["stage"]["products"] = {"MYD04_L2": ["6.1"]}
    with pytest.raises(ValidationError, match="stage.products"):
        RunConfig.model_validate(raw)


def test_two_collections_cannot_double_count_a_product(pipeline_config, tmp_path):
    config, _ = pipeline_config
    catalog = config.load_catalog().model_dump(mode="json")
    catalog["products"]["MOD04_L2"]["collections"]["7"] = catalog["products"]["MOD04_L2"][
        "collections"
    ]["6.1"]
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml.safe_dump(catalog))
    raw = config.model_dump(mode="json")
    raw["catalog"] = str(path)
    raw["stage"]["products"]["MOD04_L2"].append("7")
    raw["grid"]["streams"]["terra_db"]["collection"] = "7"
    with pytest.raises(ValidationError, match="same collection"):
        RunConfig.model_validate(raw)


def test_cli_failure_reporting_and_validation(pipeline_config):
    from typer.testing import CliRunner

    from sarb_aod.cli import app

    config, path = pipeline_config
    runner = CliRunner()
    result = runner.invoke(app, ["grid", "-c", str(path)])
    assert result.exit_code == 1, result.output
    assert "2008-07-01: 8 files" in result.output
    assert "2008-07-02: FAILED" in result.output
    result = runner.invoke(app, ["grid", "-c", str(path), "--day", "2008-07-01"])
    assert result.exit_code == 0 and "already valid" in result.output
    result = runner.invoke(app, ["validate", str(config.paths.data_root / "CAM7")])
    assert result.exit_code == 0 and "8/8 files valid" in result.output
    result = runner.invoke(app, ["show-config", "-c", str(path)])
    assert result.exit_code == 0 and "aod_obs_path" in result.output


def test_summarize_counts_and_area_mean(pipeline_config):
    from sarb_aod.config.schema import Period
    from sarb_aod.summarize import summarize

    config, _ = pipeline_config
    config = config.model_copy(
        update={"period": Period(start=date(2008, 7, 1), end=date(2008, 7, 1))}
    )
    grid_day(config, date(2008, 7, 1))
    path, rows = summarize(config)
    assert path.is_file() and len(rows) == 9
    assert rows[0]["aod_count"] == 6 and rows[-1]["aod_count"] == 6
    assert rows[-1]["aod_count_terra_dt"] == 4 and rows[-1]["aod_count_terra_db"] == 2
    assert 0 <= rows[-1]["area_mean"] <= 5
    assert rows[-1]["p999"] <= 5
    assert all(row["coverage"] == 0 for row in rows[1:8])


def test_catalog_recipe_changes_invalidate_resume(pipeline_config, tmp_path):
    config, _ = pipeline_config
    catalog = config.load_catalog().model_dump(mode="json")
    path = tmp_path / "custom_catalog.yaml"
    path.write_text(yaml.safe_dump(catalog))
    config = config.model_copy(update={"catalog": path})
    grid_day(config, date(2008, 7, 1))
    catalog["products"]["MOD04_L2"]["collections"]["6.1"]["algorithms"]["dt"]["surfaces"]["ocean"][
        "aod"
    ]["name"] = "Effective_Optical_Depth_Best_Ocean"
    path.write_text(yaml.safe_dump(catalog))
    assert not grid_day(config, date(2008, 7, 1)).skipped


def test_summary_rejects_stale_processing_definition(pipeline_config):
    from sarb_aod.summarize import summarize

    config, _ = pipeline_config
    grid_day(config, date(2008, 7, 1))
    changed = config.model_copy(update={"weighting": WeightingConfig(method="count")})
    with pytest.raises(ValueError, match="processing provenance"):
        summarize(changed)


def test_valid_file_under_wrong_window_name_is_repaired(pipeline_config):
    config, _ = pipeline_config
    result = grid_day(config, date(2008, 7, 1))
    shutil.copyfile(result.files[1], result.files[0])
    assert validate_contract_file(result.files[0])[0]
    assert not grid_day(config, date(2008, 7, 1)).skipped
    with netCDF4.Dataset(result.files[0]) as dataset:
        dataset.set_auto_mask(False)
        assert dataset["aod_count"][:].sum() == 6
