"""Source-driven publication names, input identity, and legacy read compatibility."""

import json
from datetime import date

import netCDF4 as nc
import numpy as np
import pytest
import xarray as xr
from test_modis_c7 import c7_config
from test_pipeline import pipeline_config
from test_readers import hdf_granule

from sarb_aod.config.schema import DespeckleConfig, OutputConfig, RunConfig
from sarb_aod.contract import validate_contract_file
from sarb_aod.output_names import (
    LEGACY_STEM,
    grid_view,
    legacy_name,
    output_stem,
    public_name,
)
from sarb_aod.paths import granule_dir
from sarb_aod.pipeline import grid_day
from sarb_aod.plots.suites import aggregate_maps
from sarb_aod.readers import LAND, PixelBatch
from sarb_aod.readers.base import PixelCoordinates
from sarb_aod.summarize import summarize
from sarb_aod.variants import select_grid_variant
from sarb_aod.windows import day_time_edges_seconds

DAY = date(2020, 9, 8)


def _configuration(tmp_path, family):
    products = {}
    if family in {"modis", "mixed"}:
        products.update(MOD04_L2="6.1", MYD04_L2="6.1")
    if family in {"viirs", "mixed"}:
        products.update(AERDT_L2_VIIRS_NOAA20="2.1", AERDB_L2_VIIRS_NOAA20="2")
    prefixes = {
        "MOD04_L2": "terra",
        "MYD04_L2": "aqua",
        "AERDT_L2_VIIRS_NOAA20": "noaa20",
        "AERDB_L2_VIIRS_NOAA20": "noaa20",
    }
    streams = {}
    for product, collection in products.items():
        algorithms = (
            ["dt", "db"]
            if product in {"MOD04_L2", "MYD04_L2"}
            else ["dt" if product.startswith("AERDT") else "db"]
        )
        for algorithm in algorithms:
            streams[f"{prefixes[product]}_{algorithm}"] = {
                "product": product,
                "collection": collection,
            }
    return RunConfig.model_validate(
        {
            "paths": {"data_root": str(tmp_path)},
            "period": {"start": DAY, "end": DAY},
            "stage": {"products": {p: [c] for p, c in products.items()}},
            "grid": {"streams": streams, "missing_products": "fill"},
        }
    )


def _stage_inputs(config):
    paths = []
    for product, collection in config.stage.selections():
        directory = granule_dir(config.paths, config.load_catalog(), product, collection, DAY)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ("input.hdf" if product in {"MOD04_L2", "MYD04_L2"} else "input.nc")
        path.touch()
        paths.append(path)
    return paths


def _reader(path, recipes, **kwargs):
    empty = PixelCoordinates(np.array([]), np.array([]), np.array([]))
    return {
        r.stream_id: PixelBatch(
            r.stream_id,
            np.array([day_time_edges_seconds(DAY)[0] + 60]),
            np.array([0.0]),
            np.array([0.0]),
            np.array([0.1 if r.instrument == "MODIS" else 0.3]),
            np.array([0.1]),
            np.array([LAND]),
            empty,
            {"n_accepted": 1},
        )
        for r in recipes
    }


@pytest.mark.parametrize(
    "family,label",
    [
        ("modis", "MODIS-Terra-Aqua"),
        ("viirs", "VIIRS-NOAA20"),
        ("mixed", "MODIS-Terra-Aqua_VIIRS-NOAA20"),
    ],
)
def test_configured_sources_control_names_and_schema(tmp_path, monkeypatch, family, label):
    config = _configuration(tmp_path, family)
    inputs = _stage_inputs(config)
    monkeypatch.setattr("sarb_aod.pipeline.read_granule", _reader)
    assert config.output.naming == "ceres"
    assert output_stem(config) == f"CER_AOD-3hr-f09_{label}"
    result = grid_day(config, DAY)
    assert result.files[0].name == f"CER_AOD-3hr-f09_{label}.20200908.00000.nc"
    assert result.files[1].name.endswith(".10800.nc")
    for path in result.files:
        assert validate_contract_file(path) == (True, [])
        with nc.Dataset(path) as ds:
            assert all(not name.startswith("aod_") for name in ds.variables)
            assert {
                "AOD_mean",
                "AOD_uncertainty",
                "AOD_count",
                "lat",
                "time_bnds",
            } <= ds.variables.keys()
            for stream in config.grid.streams:
                assert public_name(f"aod_mean_{stream}") in ds.variables
                assert public_name(f"aod_count_{stream}") in ds.variables
            for prefix in {"terra", "aqua", "snpp", "noaa20"}:
                if not any(s.startswith(prefix + "_") for s in config.grid.streams):
                    assert not any(prefix.upper() in name.upper() for name in ds.variables)
            assert ("AOD_mean_MODIS" in ds.variables) == (family in {"modis", "mixed"})
            assert ("AOD_mean_VIIRS" in ds.variables) == (family in {"viirs", "mixed"})
            assert ds.input_file_paths.splitlines() == sorted(str(p.absolute()) for p in inputs)
            assert ds.input_file_count == len(inputs)
            assert "UTC day 2020-09-08" in ds.input_file_paths_scope
            assert "python_environment" not in ds.ncattrs()
            assert "conda" not in ds.software_versions.lower()
    with nc.Dataset(result.files[0]) as ds:
        ds.set_auto_mask(False)
        assert ds["AOD_count"][:].sum() == len(config.grid.streams)
    maps = aggregate_maps(config)
    assert set(maps.means) >= {"product", *config.grid.streams}
    assert summarize(config)[1][-1]["aod_count"] == len(config.grid.streams)
    with xr.open_dataset(result.files[0], decode_times=False) as ds:
        view = select_grid_variant(ds, config)
        assert "aod_weighted_mean" in view and "aod_count" in view
    # Cloud-free windows and empty windows retain the same configured schema.
    with nc.Dataset(result.files[0]) as first, nc.Dataset(result.files[-1]) as last:
        assert set(first.variables) == set(last.variables)
    assert grid_day(config, DAY).skipped


def test_configured_but_missing_source_is_retained(tmp_path, monkeypatch):
    config = _configuration(tmp_path, "modis")
    inputs = _stage_inputs(config)
    aqua = next(p for p in inputs if "/Aqua/" in str(p))
    aqua.unlink()
    monkeypatch.setattr("sarb_aod.pipeline.read_granule", _reader)
    result = grid_day(config, DAY)
    with nc.Dataset(result.files[0]) as ds:
        ds.set_auto_mask(False)
        assert np.all(ds["AOD_mean_Aqua_DT"][:] == -999)
        assert ds["AOD_count_Aqua_DB"][:].sum() == 0
        assert "MYD04_L2" in json.loads(ds.primary_products_missing)
        assert "AOD_mean_VIIRS" not in ds.variables
        assert ds.input_file_count == 1
    assert "MODIS-Terra-Aqua" in result.files[0].name
    for path in inputs:
        path.unlink(missing_ok=True)
    gaps = grid_day(config, DAY, overwrite=True)
    assert gaps.files == result.files
    for path in gaps.files:
        with nc.Dataset(path) as ds:
            assert ds.input_file_count == 0 and ds.input_file_paths == ""
            assert "AOD_mean_Aqua_DT" in ds.variables


def test_publication_preserves_legacy_values_and_native_diagnostic(c7_config):
    original, _ = c7_config
    legacy = grid_day(original, original.period.start)
    config = original.model_copy(update={"output": OutputConfig()})
    result = grid_day(config, config.period.start)
    with nc.Dataset(legacy.files[0]) as before, nc.Dataset(result.files[0]) as after:
        before.set_auto_mask(False)
        after.set_auto_mask(False)
        assert "AOD_mean_MODIS_COMBINED" in after.variables
        assert "AOD_algorithm_flag_MODIS_COMBINED" in after.variables
        view = grid_view(after)
        assert set(before.variables) == set(view.variables)
        for name in before.variables:
            np.testing.assert_array_equal(before[name][...], view[name][...], err_msg=name)
        for name in before.variables:
            assert legacy_name(public_name(name)) == name
    assert validate_contract_file(result.files[0]) == (True, [])


def test_filtered_publication_omits_unconfigured_sensor_and_updates_references(pipeline_config):
    base, _ = pipeline_config
    config = base.model_copy(
        update={
            "output": OutputConfig(),
            "despeckle": DespeckleConfig(enabled=True, latitude_bands=((0, 15),)),
        }
    )
    result = grid_day(config, config.period.start)
    for path in result.files:
        assert validate_contract_file(path) == (True, [])
        with nc.Dataset(path) as ds:
            assert not any("VIIRS" in name for name in ds.variables)
            assert ds["AOD_mean_filtered"].ancillary_variables == (
                "AOD_despeckle_flags AOD_uncertainty_filtered"
            )
            assert (
                ds["AOD_mean_Terra_DT_filtered"].ancillary_variables
                == "AOD_despeckle_flag_Terra_DT"
            )
    with xr.open_dataset(result.files[0], decode_times=False) as ds:
        filtered = config.model_copy(
            update={"evaluation": config.evaluation.model_copy(update={"aod_variant": "filtered"})}
        )
        assert "aod_weighted_mean" in select_grid_variant(ds, filtered)


def test_historical_stem_retains_legacy_contract():
    assert OutputConfig.model_validate({"stem": LEGACY_STEM}).naming == "legacy"
    assert OutputConfig().naming == "ceres"
    assert OutputConfig.model_validate({"stem": LEGACY_STEM, "naming": "ceres"}).naming == "ceres"


def test_unselected_algorithms_and_staged_products_are_not_written(tmp_path, monkeypatch):
    config = _configuration(tmp_path, "modis")
    inputs = _stage_inputs(config)
    config = config.model_copy(
        update={
            "grid": config.grid.model_copy(
                update={
                    "streams": {"terra_dt": config.grid.streams["terra_dt"]},
                }
            )
        }
    )
    monkeypatch.setattr("sarb_aod.pipeline.read_granule", _reader)
    result = grid_day(config, DAY)
    assert "MODIS-Terra." in result.files[0].name
    with nc.Dataset(result.files[0]) as ds:
        assert "AOD_mean_Terra_DT" in ds.variables
        assert "AOD_mean_Terra_DB" not in ds.variables
        assert not any("Aqua" in name for name in ds.variables)
        assert ds.input_file_paths.splitlines() == [str(inputs[0].absolute())]


def test_validator_rejects_unconfigured_or_missing_configured_fields(tmp_path, monkeypatch):
    config = _configuration(tmp_path, "viirs")
    _stage_inputs(config)
    monkeypatch.setattr("sarb_aod.pipeline.read_granule", _reader)
    result = grid_day(config, DAY)
    with nc.Dataset(result.files[0], "r+") as ds:
        v = ds.createVariable("AOD_mean_Terra_DT", "f4", ("lat", "lon"), fill_value=-999)
        v.units = "1"
        v[:] = -999
        ds.renameVariable("AOD_mean_NOAA20_DB", "removed_mean")
    valid, errors = validate_contract_file(result.files[0])
    assert not valid
    assert any("unconfigured stream" in error for error in errors)
    assert any("missing configured stream" in error for error in errors)


def test_cam7_compatibility_rename_keeps_other_publication_fields(tmp_path, monkeypatch):
    config = _configuration(tmp_path, "modis")
    _stage_inputs(config)
    monkeypatch.setattr("sarb_aod.pipeline.read_granule", _reader)
    result = grid_day(config, DAY)
    with nc.Dataset(result.files[0], "r+") as ds:
        ds.renameVariable("AOD_mean", "aod_weighted_mean")
    assert validate_contract_file(result.files[0]) == (True, [])
    with nc.Dataset(result.files[0]) as ds:
        assert "aod_weighted_mean" in ds.variables and "AOD_mean" not in ds.variables
        assert "AOD_mean_Terra_DT" in ds.variables
        assert "aod_weighted_mean" in grid_view(ds).variables
