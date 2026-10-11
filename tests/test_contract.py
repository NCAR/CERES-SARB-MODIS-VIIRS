"""CAM7 oracle compatibility, atomic writes and contract failure cases."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import netCDF4 as nc
import numpy as np
import pytest
from reference.cam7_sp1 import contract as cam7

from sarb_aod import contract as c
from sarb_aod.grid.spec import NLAT, NLON


def _fields() -> dict[str, np.ndarray]:
    mean = np.full((NLAT, NLON), -999.0)
    sigma = np.full((NLAT, NLON), -999.0)
    count = np.zeros((NLAT, NLON), dtype=np.int64)
    mean[10, 20], sigma[10, 20], count[10, 20] = 0.42, 0.05, 7
    return {"aod_weighted_mean": mean, "aod_uncertainty": sigma, "aod_count": count}


def _write(path: Path, **kwargs: object) -> Path:
    inputs = {
        "window_start": datetime(2008, 7, 1, 3),
        **_fields(),
        "global_attrs": {"source": "synthetic MODIS C6.1 Terra DT", "qc_thresholds": "QA=3"},
        **kwargs,
    }
    return c.write_contract_file(path, **inputs)


def test_reference_structure_attributes_values_and_validator(tmp_path: Path) -> None:
    ours, reference = tmp_path / "ours.nc", tmp_path / "reference.nc"
    inputs = _fields()
    inputs["aod_weighted_mean_modis"] = inputs["aod_weighted_mean"].copy()
    inputs["aod_weighted_mean_viirs"] = inputs["aod_weighted_mean"].copy()
    c.write_contract_file(ours, window_start=datetime(2008, 7, 1, 3), **inputs)
    cam7.write_contract_file(reference, window_start=datetime(2008, 7, 1, 3), **inputs)
    assert cam7.validate_contract_file(ours) == (True, [])
    assert c.validate_contract_file(ours) == (True, [])
    assert c.validate_contract_file(reference) == (True, [])
    with nc.Dataset(ours) as actual, nc.Dataset(reference) as expected:
        actual.set_auto_mask(False)
        expected.set_auto_mask(False)
        assert actual.data_model == "NETCDF4"
        assert {name: len(dim) for name, dim in actual.dimensions.items()} == {
            name: len(dim) for name, dim in expected.dimensions.items()
        }
        for name in (
            "lat",
            "lon",
            "lat_bnds",
            "lon_bnds",
            "time",
            "time_bnds",
            "aod_weighted_mean",
            "aod_uncertainty",
            "aod_count",
            "aod_weighted_mean_modis",
            "aod_weighted_mean_viirs",
        ):
            observed, oracle = actual[name], expected[name]
            assert observed.dtype == oracle.dtype, name
            assert observed.dimensions == oracle.dimensions, name
            for attr in ("_FillValue", "units", "standard_name", "bounds", "calendar"):
                assert getattr(observed, attr, None) == getattr(oracle, attr, None), (name, attr)
            np.testing.assert_array_equal(observed[...], oracle[...], err_msg=name)
        assert set(c.REQUIRED_GLOBAL_ATTRS) == set(cam7.REQUIRED_GLOBAL_ATTRS)
        assert len(c.REQUIRED_GLOBAL_ATTRS) == 15
        assert set(c.REQUIRED_GLOBAL_ATTRS) <= set(actual.ncattrs())
        assert actual.Conventions == "CF-1.10"
        assert actual.bias_correction_method == "none"
        assert actual.bias_correction_version == "none"
        assert actual.aod_wavelength_nm == 550
        for key, value in c.code_provenance().items():
            assert actual.getncattr(key) == value
        assert json.loads(actual.compute_platform)["os_name"]
        assert json.loads(actual.python_environment)["packages"]


def test_roundtrip_center_and_absent_companions(tmp_path: Path) -> None:
    path = _write(tmp_path / "nested" / "test.nc", window_start=datetime(2000, 1, 1))
    with nc.Dataset(path) as dataset:
        dataset.set_auto_mask(False)
        assert dataset["time"].shape == ()
        assert dataset["time"][...] == 1.5
        np.testing.assert_array_equal(dataset["time_bnds"][:], [0.0, 3.0])
        assert dataset["aod_weighted_mean"][10, 20] == np.float32(0.42)
        assert dataset["aod_uncertainty"][10, 20] == np.float32(0.05)
        assert dataset["aod_count"][10, 20] == 7
        for name in ("aod_weighted_mean_modis", "aod_weighted_mean_viirs"):
            assert np.all(dataset[name][:] == -999.0)
        assert dataset.source == "synthetic MODIS C6.1 Terra DT"


@pytest.mark.parametrize("clip", [True, False])
def test_clipping_preserves_fill_and_stream_means(tmp_path: Path, clip: bool) -> None:
    inputs = _fields()
    mean = inputs["aod_weighted_mean"]
    mean[0, :5] = [-0.02, np.nan, np.inf, -999, -1000]
    inputs["aod_uncertainty"][0, 0] = 0.03
    original = mean.copy()
    path = _write(
        tmp_path / "clipping.nc",
        **inputs,
        aod_weighted_mean_modis=mean,
        aod_weighted_mean_viirs=mean,
        extra_fields={"aod_mean_terra_dt": mean, "aod_count_terra_dt": inputs["aod_count"]},
        clip_negative_mean=clip,
    )
    with nc.Dataset(path) as dataset:
        dataset.set_auto_mask(False)
        for name in ("aod_weighted_mean", "aod_weighted_mean_modis", "aod_weighted_mean_viirs"):
            assert dataset[name][0, 0] == np.float32(0 if clip else -0.02)
            np.testing.assert_array_equal(dataset[name][0, 1:5], [-999] * 4)
        assert dataset["aod_mean_terra_dt"][0, 0] == np.float32(-0.02)
        assert dataset["aod_uncertainty"][0, 0] == np.float32(0.03)
        assert dataset["aod_count_clipped_negative"].dtype == np.int32
        assert dataset["aod_count_clipped_negative"][0, 0] == int(clip)
        assert dataset["aod_count_clipped_negative"][:].sum() == int(clip)
        assert dataset["aod_count_terra_dt"].dtype == np.int32
    np.testing.assert_array_equal(mean, original)


def test_masked_and_nonfinite_inputs_become_fill(tmp_path: Path) -> None:
    inputs = _fields()
    masked = np.ma.array(inputs["aod_weighted_mean"], mask=False)
    masked.mask[10, 20] = True
    inputs["aod_weighted_mean"] = masked
    inputs["aod_uncertainty"][0, :4] = [np.nan, np.inf, -np.inf, 1e100]
    inputs["aod_count"] = inputs["aod_count"].astype(float)
    inputs["aod_count"][0, :3] = [np.nan, np.inf, -np.inf]
    with np.errstate(all="raise"):
        path = _write(tmp_path / "masked.nc", **inputs)
    with nc.Dataset(path) as dataset:
        dataset.set_auto_mask(False)
        assert dataset["aod_weighted_mean"][10, 20] == -999
        np.testing.assert_array_equal(dataset["aod_uncertainty"][0, :4], [-999] * 4)
        np.testing.assert_array_equal(dataset["aod_count"][0, :3], [0] * 3)


def test_utc_window_start_and_default_filenames(tmp_path: Path) -> None:
    local = datetime(2008, 7, 1, 23, tzinfo=timezone(timedelta(hours=-4)))
    assert c.contract_filename(local) == "MODIS_VIIRS_AOD_3hr_f09.20080702.10800.nc"
    assert c.contract_filename(datetime(2008, 7, 1)).endswith("20080701.00000.nc")
    path = _write(tmp_path / "aware.nc", window_start=local)
    with nc.Dataset(path) as dataset:
        center = nc.num2date(dataset["time"][...], c.TIME_UNITS, c.TIME_CALENDAR)
        assert (center.year, center.month, center.day, center.hour, center.minute) == (
            2008,
            7,
            2,
            4,
            30,
        )


@pytest.mark.parametrize("start", [datetime(2008, 7, 1, 1), datetime(2008, 7, 1, 0, 0, 0, 1)])
def test_writer_rejects_misaligned_window(tmp_path: Path, start: datetime) -> None:
    with pytest.raises(ValueError, match="UTC three-hour boundary"):
        _write(tmp_path / "bad.nc", window_start=start)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "name", ["aod_weighted_mean", "aod_uncertainty", "aod_count", "aod_weighted_mean_modis"]
)
def test_writer_rejects_wrong_shapes(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="shape"):
        _write(tmp_path / "bad.nc", **{name: np.zeros((NLON, NLAT))})
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("value", [-1, 0.5, 2**31])
def test_writer_rejects_invalid_counts(tmp_path: Path, value: float) -> None:
    count = np.zeros((NLAT, NLON))
    count[0, 0] = value
    with pytest.raises(ValueError, match="nonnegative integers"):
        _write(tmp_path / "bad.nc", aod_count=count)


@pytest.mark.parametrize(
    "name", ["aod_weighted_mean", "aod_count_clipped_negative", "other", "aod_bad/name"]
)
def test_writer_rejects_extra_field_collisions_or_invalid_names(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="unreserved"):
        _write(tmp_path / "bad.nc", extra_fields={name: np.zeros((NLAT, NLON))})


def test_failed_rename_preserves_existing_file_and_removes_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path / "existing.nc")
    original = path.read_bytes()

    def failed_replace(source: Path, target: Path) -> None:
        assert source.parent == target.parent
        assert c.validate_contract_file(source) == (True, [])
        raise OSError("simulated publication failure")

    monkeypatch.setattr(c.os, "replace", failed_replace)
    with pytest.raises(OSError, match="publication failure"):
        _write(path)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_failed_netcdf_write_preserves_existing_file_and_removes_temporary(tmp_path: Path) -> None:
    path = _write(tmp_path / "existing.nc")
    original = path.read_bytes()
    with pytest.raises(TypeError):
        _write(path, global_attrs={"source": {"invalid": "NetCDF attribute type"}})
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize(
    "fault,fragment",
    [
        ("grid", "lat centers"),
        ("grid_precision", "lon centers"),
        ("bounds", "lat_bnds"),
        ("missing_lat", "missing required var lat"),
        ("missing_time", "missing required var time"),
        ("time_center", "window center"),
        ("time_length", "span exactly three hours"),
        ("time_alignment", "UTC three-hour boundary"),
        ("time_nan", "must be finite"),
        ("time_nonscalar", "time dims"),
        ("fill", "_FillValue"),
        ("field_dimensions", "dims"),
        ("field_dtype", "dtype"),
        ("nonfinite", "nonfinite values"),
        ("negative_count", "nonnegative"),
        ("missing_global", "missing global attr source"),
        ("conventions", "Conventions"),
        ("missing_companion", "aod_weighted_mean_viirs"),
    ],
)
def test_validator_rejects_corrupted_contract(tmp_path: Path, fault: str, fragment: str) -> None:
    path = _write(tmp_path / "corrupt.nc")
    with nc.Dataset(path, "r+") as dataset:
        if fault == "grid":
            dataset["lat"][0] = -89.9
        elif fault == "grid_precision":
            dataset["lon"][-1] += 1e-5  # Reject what default np.allclose rtol would accept.
        elif fault == "bounds":
            dataset["lat_bnds"][0, 0] = -90.5
        elif fault in ("missing_lat", "missing_time"):
            name = fault.removeprefix("missing_")
            dataset.renameVariable(name, f"removed_{name}")
        elif fault == "time_center":
            dataset["time"][...] += 0.5
        elif fault == "time_length":
            dataset["time_bnds"][1] += 1
        elif fault == "time_alignment":
            dataset["time_bnds"][:] += 0.5
            dataset["time"][...] += 0.5
        elif fault == "time_nan":
            dataset["time"][...] = np.nan
        elif fault == "time_nonscalar":
            dataset.renameVariable("time", "removed_time")
            dataset.createVariable("time", "f8", ("nbnds",))[:] = [1.5, 1.5]
        elif fault in ("fill", "field_dimensions", "field_dtype"):
            dataset.renameVariable("aod_weighted_mean", "removed_mean")
            variable = dataset.createVariable(
                "aod_weighted_mean",
                "f8" if fault == "field_dtype" else "f4",
                ("lon", "lat") if fault == "field_dimensions" else ("lat", "lon"),
                fill_value=-998 if fault == "fill" else -999,
            )
            variable.units = "1"
            variable.standard_name = c.PRIMARY_STD_NAME
            variable[:] = 0.1
        elif fault == "nonfinite":
            dataset["aod_uncertainty"][0, 0] = np.inf
        elif fault == "negative_count":
            dataset["aod_count"][0, 0] = -1
        elif fault == "missing_global":
            dataset.delncattr("source")
        elif fault == "conventions":
            dataset.Conventions = "CF-1.0"
        else:
            dataset.renameVariable("aod_weighted_mean_viirs", "removed_viirs")
    ok, errors = c.validate_contract_file(path)
    assert not ok
    assert any(fragment in error for error in errors), errors


def test_validator_reports_missing_or_non_netcdf_files(tmp_path: Path) -> None:
    ok, errors = c.validate_contract_file(tmp_path / "missing.nc")
    assert not ok and errors
    path = tmp_path / "text.nc"
    path.write_text("not NetCDF")
    ok, errors = c.validate_contract_file(path)
    assert not ok and errors
