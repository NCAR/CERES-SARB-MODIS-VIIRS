"""Frozen obs-file contract (SPEC.md section 5). SP1 writes; SP2 reads.

Vendored from NCAR/CERES-SARB-CAM7 sp1_aod_pipeline/contract.py,
commit cd1b5d45dbf3ddfb737c3a7153b67cdf2adef254. Behavior unchanged.
"""

import os
import subprocess
from datetime import datetime, timedelta, timezone

import netCDF4 as nc
import numpy as np

from .f09_grid import NLAT, NLON, f09_lat, f09_lat_bnds, f09_lon, f09_lon_bnds

FILL_FLOAT = np.float32(-999.0)
FILL_COUNT = np.int32(0)
NBNDS = 2
WINDOW_LENGTH_SEC = 10800
WINDOW_SECONDS = [i * WINDOW_LENGTH_SEC for i in range(8)]
TIME_UNITS = "hours since 2000-01-01 00:00:00 UTC"
TIME_CALENDAR = "gregorian"
EPOCH = datetime(2000, 1, 1)
FILENAME_TEMPLATE = "MODIS_VIIRS_AOD_3hr_f09.{ymd}.{secs:05d}.nc"
PRIMARY_STD_NAME = "atmosphere_optical_thickness_due_to_ambient_aerosol_particles"
REQUIRED_GLOBAL_ATTRS = [
    "Conventions",
    "title",
    "institution",
    "source",
    "history",
    "references",
    "qc_thresholds",
    "merge_method",
    "merge_version",
    "bias_correction_method",
    "bias_correction_version",
    "grid",
    "aod_wavelength_nm",
    "date_created",
    "git_commit",
]


def contract_filename(window_start):
    secs = window_start.hour * 3600 + window_start.minute * 60 + window_start.second
    return FILENAME_TEMPLATE.format(ymd=window_start.strftime("%Y%m%d"), secs=secs)


def git_commit():
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        return (
            subprocess.check_output(
                ["git", "-C", here, "rev-parse", "HEAD"],
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def _hours(dt):
    return (dt - EPOCH).total_seconds() / 3600.0


def _prep(arr, fill, dtype):
    values = np.array(arr, dtype=np.float64)
    values = np.where(np.isfinite(values), values, float(fill))
    return values.astype(dtype)


def _default_global_attrs():
    return {
        "Conventions": "CF-1.10",
        "title": "Merged MODIS+VIIRS gridded AOD (550 nm) on f09 for CAM7 nudging",
        "institution": "NCAR",
        "source": "MODIS MOD04_L2/MYD04_L2 C6.1 (DT+DB); VIIRS AERDB_L2 NOAA-20 C2",
        "history": "created by sp1_aod_pipeline",
        "references": "CERES-SARB-CAM7 SPEC.md section 5",
        "qc_thresholds": "unset",
        "merge_method": "inverse-variance (w=1/sigma^2)",
        "merge_version": "1.0",
        "bias_correction_method": "AERONET-anchored affine VIIRS->MODIS",
        "bias_correction_version": "1.0",
        "grid": "f09 FV 0.9x1.25 (192x288)",
        "aod_wavelength_nm": np.int32(550),
        "date_created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": git_commit(),
    }


def write_contract_file(
    out_path,
    *,
    window_start,
    aod_weighted_mean,
    aod_uncertainty,
    aod_count,
    aod_weighted_mean_modis=None,
    aod_weighted_mean_viirs=None,
    global_attrs=None,
):
    center = window_start + timedelta(seconds=WINDOW_LENGTH_SEC // 2)
    end = window_start + timedelta(seconds=WINDOW_LENGTH_SEC)
    ds = nc.Dataset(out_path, "w", format="NETCDF4")
    try:
        ds.createDimension("lat", NLAT)
        ds.createDimension("lon", NLON)
        ds.createDimension("nbnds", NBNDS)
        vlat = ds.createVariable("lat", "f8", ("lat",))
        vlat.units = "degrees_north"
        vlat.standard_name = "latitude"
        vlat.bounds = "lat_bnds"
        vlat[:] = f09_lat()
        vlon = ds.createVariable("lon", "f8", ("lon",))
        vlon.units = "degrees_east"
        vlon.standard_name = "longitude"
        vlon.bounds = "lon_bnds"
        vlon[:] = f09_lon()
        ds.createVariable("lat_bnds", "f8", ("lat", "nbnds"))[:] = f09_lat_bnds()
        ds.createVariable("lon_bnds", "f8", ("lon", "nbnds"))[:] = f09_lon_bnds()
        vt = ds.createVariable("time", "f8", ())
        vt.units = TIME_UNITS
        vt.calendar = TIME_CALENDAR
        vt.standard_name = "time"
        vt.bounds = "time_bnds"
        vt[...] = _hours(center)
        ds.createVariable("time_bnds", "f8", ("nbnds",))[:] = [
            _hours(window_start),
            _hours(end),
        ]

        def mkf(name, arr, long_name, standard_name=None):
            var = ds.createVariable(name, "f4", ("lat", "lon"), fill_value=FILL_FLOAT)
            var.units = "1"
            var.long_name = long_name
            if standard_name:
                var.standard_name = standard_name
            var[:] = _prep(arr, FILL_FLOAT, np.float32)

        mkf(
            "aod_weighted_mean",
            aod_weighted_mean,
            "Merged MODIS+VIIRS column AOD at 550 nm",
            PRIMARY_STD_NAME,
        )
        mkf(
            "aod_uncertainty",
            aod_uncertainty,
            "1-sigma uncertainty of aod_weighted_mean",
        )
        vc = ds.createVariable("aod_count", "i4", ("lat", "lon"), fill_value=FILL_COUNT)
        vc.units = "1"
        vc.long_name = "number of valid L2 pixels contributing to the cell-window"
        vc[:] = _prep(aod_count, FILL_COUNT, np.int32)
        if aod_weighted_mean_modis is not None:
            mkf(
                "aod_weighted_mean_modis",
                aod_weighted_mean_modis,
                "MODIS-only column AOD at 550 nm",
            )
        if aod_weighted_mean_viirs is not None:
            mkf(
                "aod_weighted_mean_viirs",
                aod_weighted_mean_viirs,
                "VIIRS-only (bias-corrected) column AOD at 550 nm",
            )

        attrs = _default_global_attrs()
        attrs.update(global_attrs or {})
        for key, value in attrs.items():
            ds.setncattr(key, value)
    finally:
        ds.close()


def validate_contract_file(path):
    errs = []
    ds = nc.Dataset(path)
    try:
        for dim_name, expected in [("lat", NLAT), ("lon", NLON), ("nbnds", NBNDS)]:
            if dim_name not in ds.dimensions:
                errs.append(f"missing dim {dim_name}")
            elif len(ds.dimensions[dim_name]) != expected:
                errs.append(f"dim {dim_name}={len(ds.dimensions[dim_name])} expected {expected}")
        if "lat" in ds.variables and not np.allclose(ds["lat"][:], f09_lat(), atol=1e-6):
            errs.append("lat centers do not match f09")
        if "lon" in ds.variables and not np.allclose(ds["lon"][:], f09_lon(), atol=1e-6):
            errs.append("lon centers do not match f09")
        if "lat_bnds" in ds.variables and not np.allclose(
            ds["lat_bnds"][:], f09_lat_bnds(), atol=1e-6
        ):
            errs.append("lat_bnds mismatch")
        if "lon_bnds" in ds.variables and not np.allclose(
            ds["lon_bnds"][:], f09_lon_bnds(), atol=1e-6
        ):
            errs.append("lon_bnds mismatch")
        if "time" not in ds.variables:
            errs.append("missing time")
        else:
            time = ds["time"]
            if time.ndim != 0:
                errs.append("time must be scalar (0-d)")
            if getattr(time, "units", "") != TIME_UNITS:
                errs.append("time.units wrong")
            if getattr(time, "calendar", "") != TIME_CALENDAR:
                errs.append("time.calendar wrong")
            if getattr(time, "bounds", "") != "time_bnds":
                errs.append("time.bounds wrong")
        if "time_bnds" not in ds.variables or ds["time_bnds"].shape != (NBNDS,):
            errs.append("time_bnds missing/wrong shape")
        for name, dtype, fill in [
            ("aod_weighted_mean", "float32", -999.0),
            ("aod_uncertainty", "float32", -999.0),
            ("aod_count", "int32", 0),
        ]:
            if name not in ds.variables:
                errs.append(f"missing required var {name}")
                continue
            var = ds.variables[name]
            if var.dimensions != ("lat", "lon"):
                errs.append(f"{name} dims {var.dimensions} expected ('lat','lon')")
            if var.dtype.name != dtype:
                errs.append(f"{name} dtype {var.dtype.name} expected {dtype}")
            fill_value = getattr(var, "_FillValue", None)
            if fill_value is None or float(fill_value) != fill:
                errs.append(f"{name} _FillValue {fill_value} expected {fill}")
            if getattr(var, "units", "") != "1":
                errs.append(f"{name}.units expected '1'")
        if (
            "aod_weighted_mean" in ds.variables
            and getattr(ds["aod_weighted_mean"], "standard_name", "") != PRIMARY_STD_NAME
        ):
            errs.append("aod_weighted_mean.standard_name wrong")
        present = set(ds.ncattrs())
        for key in REQUIRED_GLOBAL_ATTRS:
            if key not in present:
                errs.append(f"missing global attr {key}")
        if "Conventions" in present and ds.getncattr("Conventions") != "CF-1.10":
            errs.append("Conventions != CF-1.10")
    finally:
        ds.close()
    return (len(errs) == 0, errs)
