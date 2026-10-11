"""Same-file baseline preservation, typed filter provenance and atomic output."""

import hashlib
import json
from datetime import date, datetime, timezone

import netCDF4 as nc
import numpy as np
import pytest
from test_pipeline import pipeline_config
from test_readers import hdf_granule

from sarb_aod import contract, pipeline
from sarb_aod.config.schema import DespeckleConfig, WeightingConfig
from sarb_aod.contract import validate_contract_file
from sarb_aod.despeckle import FLAG_FILL, REPLACED
from sarb_aod.grid.spec import load_grid
from sarb_aod.pipeline import grid_day
from sarb_aod.readers import PixelBatch, PixelCoordinates

DAY = date(2008, 7, 1)


def enabled(config, **kwargs):
    return config.model_copy(
        update={"despeckle": DespeckleConfig(enabled=True, latitude_bands=((0, 15),), **kwargs)}
    )


@pytest.mark.parametrize("method", ["count", "gls"])
@pytest.mark.parametrize("noop", [False, True])
def test_paired_outputs_preserve_all_original_fields(pipeline_config, method, noop):
    cfg, _ = pipeline_config
    cfg = cfg.model_copy(update={"weighting": WeightingConfig(method=method)})
    baseline = grid_day(cfg, DAY)
    with nc.Dataset(baseline.files[0]) as ds:
        ds.set_auto_mask(False)
        originals = {name: np.array(ds[name][...]) for name in ds.variables}
    cfg = enabled(cfg, aod_min=10 if noop else 0.5)
    result = grid_day(cfg, DAY)
    assert not result.skipped and len(result.files) == 8
    for path in result.files:
        assert validate_contract_file(path) == (True, [])
    with nc.Dataset(result.files[0]) as ds:
        ds.set_auto_mask(False)
        for name, values in originals.items():
            np.testing.assert_array_equal(ds[name][...], values, err_msg=name)
        for name in (
            "aod_weighted_mean",
            "aod_weighted_mean_modis",
            "aod_weighted_mean_viirs",
            "aod_mean_terra_dt",
            "aod_mean_terra_db",
        ):
            np.testing.assert_array_equal(ds[f"{name}_unfiltered"][:], ds[name][:])
            if noop:
                np.testing.assert_array_equal(ds[f"{name}_filtered"][:], ds[name][:])
        primary = ds["aod_weighted_mean"][:]
        filtered = ds["aod_weighted_mean_filtered"][:]
        flags = ds["aod_despeckle_flags"][:]
        assert np.all(filtered[primary <= -900] == -999)
        assert np.all(flags[primary <= -900] == FLAG_FILL)
        if not noop:
            assert np.any((primary > -900) & (filtered == -999))
            assert np.any((primary > filtered) & (filtered > -900))
            assert np.all(ds["aod_uncertainty_filtered"][:][flags > 0] == -999)
        assert ds.cam_aod_variant == "unfiltered"
        assert json.loads(ds.despeckle_config) == cfg.despeckle.model_dump(mode="json")
        assert ds["aod_despeckle_flag_terra_dt"].dtype == np.dtype("int8")
        assert ds["aod_despeckle_flag_terra_dt"]._FillValue == FLAG_FILL
        assert ds["aod_despeckle_neighbor_count_terra_dt"]._FillValue == -1
    assert grid_day(cfg, DAY).skipped


def test_disabled_has_no_extension_and_changed_settings_invalidate_resume(pipeline_config):
    cfg, _ = pipeline_config
    result = grid_day(cfg, DAY)
    with nc.Dataset(result.files[0]) as ds:
        assert "aod_weighted_mean_filtered" not in ds.variables
    cfg = enabled(cfg)
    grid_day(cfg, DAY)
    changed = cfg.model_copy(
        update={"despeckle": cfg.despeckle.model_copy(update={"radius_km": 100})}
    )
    assert not grid_day(changed, DAY).skipped


def test_dense_spike_gets_replaced_and_uncertainty_is_unavailable(pipeline_config, monkeypatch):
    cfg, _ = pipeline_config
    grid = load_grid()
    rows, cols = np.meshgrid(np.arange(168, 173), np.arange(10, 25), indexing="ij")
    lat, lon = grid.lat[rows.ravel()], grid.lon[cols.ravel()]
    center = (rows.ravel() == 170) & (cols.ravel() == 17)

    def read(path, recipes, **kwargs):
        batches = {}
        for recipe in recipes:
            x = np.full(lat.size, 0.1)
            if recipe.stream_id == "terra_dt":
                x[center] = 5
            batches[recipe.stream_id] = PixelBatch(
                recipe.stream_id,
                np.full(lat.size, datetime(2008, 7, 1, tzinfo=timezone.utc).timestamp() + 1),
                lat,
                lon,
                x,
                np.full(lat.size, 0.1),
                np.zeros(lat.size, dtype=np.uint8),
                PixelCoordinates(*(np.array([]) for _ in range(3))),
                {},
            )
        return batches

    monkeypatch.setattr(pipeline, "read_granule", read)
    cfg = cfg.model_copy(
        update={"despeckle": DespeckleConfig(enabled=True, latitude_bands=((65, 90),))}
    )
    result = grid_day(cfg, DAY)
    with nc.Dataset(result.files[0]) as ds:
        ds.set_auto_mask(False)
        assert ds["aod_weighted_mean"][170, 17] == pytest.approx(2.55)
        assert ds["aod_weighted_mean_filtered"][170, 17] == pytest.approx(0.1)
        assert ds["aod_despeckle_flag_terra_dt"][170, 17] == REPLACED
        assert ds["aod_despeckle_flags"][170, 17] == 2
        assert ds["aod_uncertainty_filtered"][170, 17] == -999
        assert ds["aod_count"][170, 17] == 2


@pytest.mark.parametrize(
    "mutation",
    ["missing_field", "missing_metadata", "baseline", "flag", "sigma", "band_config", "units"],
)
def test_corrupt_extension_is_rejected_and_repaired(pipeline_config, mutation):
    cfg, _ = pipeline_config
    cfg = enabled(cfg)
    result = grid_day(cfg, DAY)
    path = result.files[0]
    with nc.Dataset(path, "a") as ds:
        if mutation == "missing_field":
            ds.renameVariable("aod_weighted_mean_filtered", "removed_field")
        elif mutation == "missing_metadata":
            ds.delncattr("despeckle_config")
        elif mutation == "baseline":
            ds["aod_weighted_mean_unfiltered"][0, 0] = 0.2
        elif mutation == "flag":
            ds["aod_despeckle_flag_terra_dt"][0, 0] = 2
        elif mutation == "sigma":
            ds["aod_uncertainty_filtered"][:] = 0
        elif mutation == "units":
            ds["aod_despeckle_flags"].units = "km"
        else:
            settings = json.loads(ds.despeckle_config)
            settings["latitude_bands"] = [[65, 90]]
            ds.despeckle_config = json.dumps(settings)
    assert not validate_contract_file(path)[0]
    assert not grid_day(cfg, DAY).skipped
    assert validate_contract_file(path) == (True, [])


def test_extension_failure_preserves_existing_file_atomically(pipeline_config, monkeypatch):
    cfg, _ = pipeline_config
    baseline = grid_day(cfg, DAY)
    path = baseline.files[0]
    original = hashlib.sha256(path.read_bytes()).hexdigest()

    def fail(*args, **kwargs):
        raise ValueError("injected extension failure")

    monkeypatch.setattr(contract, "write_despeckle", fail)
    with pytest.raises(ValueError, match="injected extension failure"):
        grid_day(enabled(cfg), DAY)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original
    assert not list(path.parent.glob("*.tmp"))


def test_invalid_extension_values_never_replace_existing_file(pipeline_config, monkeypatch):
    from dataclasses import replace

    cfg, _ = pipeline_config
    baseline = grid_day(cfg, DAY)
    path = baseline.files[0]
    original = hashlib.sha256(path.read_bytes()).hexdigest()
    actual = pipeline.combine_filtered

    def inconsistent(*args, **kwargs):
        result = actual(*args, **kwargs)
        values = result.mean.copy()
        values[:, 0, 0] = 0.2  # Invented value in an originally missing cell.
        return replace(result, mean=values)

    monkeypatch.setattr(pipeline, "combine_filtered", inconsistent)
    with pytest.raises(ValueError, match="created data"):
        grid_day(enabled(cfg), DAY)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original
    assert not list(path.parent.glob("*.tmp"))
