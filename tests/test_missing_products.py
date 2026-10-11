"""A data gap must remain missing, retain available sources and resume safely."""

import json
from datetime import date

import netCDF4
import numpy as np
import pytest
from test_modis_c7 import c7_config
from test_pipeline import hdf_granule, pipeline_config

from sarb_aod.batch import grid_months
from sarb_aod.combined_product import AOD_NAME, FLAG_NAME, TOTAL_NAME
from sarb_aod.config.schema import RunConfig
from sarb_aod.contract import validate_contract_file
from sarb_aod.paths import granule_dir
from sarb_aod.pipeline import grid_day, processing_sha256


def fill_config(config):
    return config.model_copy(
        update={"grid": config.grid.model_copy(update={"missing_products": "fill"})}
    )


def test_gap_day_writes_eight_missing_files_and_records_resume(pipeline_config):
    original, _ = pipeline_config
    config = fill_config(original)
    assert processing_sha256(config) != processing_sha256(original)
    day = date(2008, 7, 2)
    with pytest.raises(ValueError, match="no primary granules"):
        grid_day(original, day)
    result = grid_day(config, day)
    assert result.source_granule_count == 0 and result.missing_products == ["MOD04_L2"]
    assert len(result.files) == 8
    for path in result.files:
        assert validate_contract_file(path) == (True, [])
        with netCDF4.Dataset(path) as ds:
            ds.set_auto_mask(False)
            assert json.loads(ds.primary_products_missing) == ["MOD04_L2"]
            assert ds.primary_granule_count == 0
            for name in ("aod_weighted_mean", "aod_uncertainty", "aod_mean_terra_dt"):
                assert np.all(ds[name][:] == -999)
            assert not np.any(ds["aod_count"][:])
    resumed = grid_day(config, day)
    assert resumed.skipped and resumed.missing_products == result.missing_products
    # A strict run cannot silently reuse fill-policy results.
    with pytest.raises(ValueError, match="no primary granules"):
        grid_day(original, day)


def test_partial_product_keeps_terra_values_and_fills_aqua(pipeline_config):
    base, _ = pipeline_config
    reference = grid_day(base, date(2008, 7, 1))
    with netCDF4.Dataset(reference.files[0]) as ds:
        ds.set_auto_mask(False)
        expected = ds["aod_weighted_mean"][:]
    raw = base.model_dump(mode="json")
    raw["stage"]["products"]["MYD04_L2"] = ["6.1"]
    raw["grid"]["streams"].update(
        {f"aqua_{a}": {"product": "MYD04_L2", "collection": "6.1"} for a in ("dt", "db")}
    )
    config = fill_config(RunConfig.model_validate(raw))
    result = grid_day(config, date(2008, 7, 1))
    assert result.missing_products == ["MYD04_L2"]
    with netCDF4.Dataset(result.files[0]) as ds:
        ds.set_auto_mask(False)
        np.testing.assert_array_equal(ds["aod_weighted_mean"][:], expected)
        assert ds["aod_count"][:].sum() == 6
        assert np.all(ds["aod_mean_aqua_dt"][:] == -999)
        assert not np.any(ds["aod_count_aqua_db"][:])


def test_gap_outputs_rebuild_when_product_arrives(pipeline_config):
    base, _ = pipeline_config
    config = fill_config(base)
    day = date(2008, 7, 1)
    directory = granule_dir(config.paths, config.load_catalog(), "MOD04_L2", "6.1", day)
    source = next(directory.glob("*.hdf"))
    saved = source.with_suffix(".saved")
    source.rename(saved)
    assert grid_day(config, day).missing_products == ["MOD04_L2"]
    saved.rename(source)
    restored = grid_day(config, day)
    assert not restored.skipped and not restored.missing_products
    with netCDF4.Dataset(restored.files[0]) as ds:
        assert ds["aod_count"][:].sum() == 6
    assert grid_day(config, day).skipped


def test_corrupt_present_granule_still_fails(pipeline_config):
    base, _ = pipeline_config
    config = fill_config(base)
    day = date(2008, 7, 2)
    directory = granule_dir(config.paths, config.load_catalog(), "MOD04_L2", "6.1", day)
    directory.mkdir(parents=True)
    (directory / "corrupt.hdf").write_bytes(b"not an HDF file")
    with pytest.raises(Exception):
        grid_day(config, day)
    assert not list(config.paths.data_root.rglob("*.nc"))


def test_interrupted_gap_rebuild_cannot_resume_stale_windows(pipeline_config, monkeypatch):
    from sarb_aod import pipeline

    sd = pytest.importorskip("pyhdf.SD")
    base, _ = pipeline_config
    config = fill_config(base)
    day = date(2008, 7, 1)
    directory = granule_dir(config.paths, config.load_catalog(), "MOD04_L2", "6.1", day)
    source = next(directory.glob("*.hdf"))
    saved = source.with_suffix(".saved")
    source.rename(saved)
    gap = grid_day(config, day)
    saved.rename(source)
    # Put all valid retrievals in the second window, which will remain stale.
    handle = sd.SD(str(source), sd.SDC.WRITE)
    field = handle.select("Scan_Start_Time")
    field[:] = field[:] + 10800
    field.endaccess()
    handle.end()
    original_write = pipeline.write_contract_file

    def interrupted(path, **kwargs):
        if path == gap.files[1]:
            raise OSError("simulated interrupted rebuild")
        return original_write(path, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(pipeline, "write_contract_file", interrupted)
        with pytest.raises(OSError, match="interrupted rebuild"):
            grid_day(config, day)
    recovered = grid_day(config, day)
    assert not recovered.skipped
    with netCDF4.Dataset(recovered.files[1]) as ds:
        assert ds["aod_count"][:].sum() == 6
        assert json.loads(ds.primary_products_missing) == []


@pytest.mark.parametrize("error", [PermissionError("denied"), OSError("I/O failure")])
def test_archive_listing_errors_are_not_data_gaps(pipeline_config, monkeypatch, error):
    from sarb_aod import pipeline

    config, _ = pipeline_config

    def failed_listing(path):
        raise error

    monkeypatch.setattr(pipeline.os, "scandir", failed_listing)
    with pytest.raises(type(error), match=str(error)):
        grid_day(fill_config(config), date(2008, 7, 1))


def test_all_missing_c7_native_product_is_missing(c7_config):
    base, path = c7_config
    path.unlink()
    config = fill_config(base)
    result = grid_day(config, base.period.start)
    for path in result.files:
        assert validate_contract_file(path) == (True, [])
        with netCDF4.Dataset(path) as ds:
            ds.set_auto_mask(False)
            assert np.all(ds[AOD_NAME][:] == -999)
            assert not np.any(ds[TOTAL_NAME][:])
            assert np.all(ds[FLAG_NAME][:] == ds[FLAG_NAME]._FillValue)


def test_monthly_metrics_count_gap_days_and_resume_them(pipeline_config):
    base, _ = pipeline_config
    config = fill_config(base)
    first = list(grid_months(config))[0]
    assert first.passed and first.output_files == 16
    for result in (first, list(grid_months(config))[0]):
        report = json.loads(result.metrics_path.read_text())
        assert report["days_with_missing_products"] == 1
        assert report["days"][1]["missing_products"] == ["MOD04_L2"]
        assert report["failed_days"] == 0
