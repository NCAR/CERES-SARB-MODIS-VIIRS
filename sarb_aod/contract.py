"""Atomic CAM7 observation files and their structural validator.

Ported from NCAR/CERES-SARB-CAM7 ``sp1_aod_pipeline/contract.py``.
Adds atomic publication, canonical companion fields, controlled clipping,
diagnostics, and strict coordinate/time validation. The original implementation
is vendored under ``tests/reference/cam7_sp1`` as a compatibility oracle.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import netCDF4 as nc
import numpy as np
from numpy.typing import ArrayLike

from . import __version__
from .combined_product import AOD_NAME, COUNT_NAMES, FLAG_NAME, TOTAL_NAME, CombinedWindow
from .grid.spec import NLAT, NLON, f09_lat, f09_lat_bnds, f09_lon, f09_lon_bnds
from .windows import TIME_CALENDAR, TIME_UNITS, WINDOW_LENGTH_SEC, cf_hours

FILL_FLOAT = np.float32(-999.0)
FILL_COUNT = np.int32(0)
NBNDS = 2
FILENAME_TEMPLATE = "MODIS_VIIRS_AOD_3hr_f09.{ymd}.{secs:05d}.nc"
PRIMARY_STD_NAME = "atmosphere_optical_thickness_due_to_ambient_aerosol_particles"
REQUIRED_GLOBAL_ATTRS = (
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
)
_MEAN_NAMES = ("aod_weighted_mean", "aod_weighted_mean_modis", "aod_weighted_mean_viirs")
_RESERVED_FIELDS = {
    "lat",
    "lon",
    "lat_bnds",
    "lon_bnds",
    "time",
    "time_bnds",
    "aod_uncertainty",
    "aod_count",
    "aod_count_clipped_negative",
    *_MEAN_NAMES,
}


def _window_start(value: datetime) -> datetime:
    if value.utcoffset() is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        value = value.replace(tzinfo=None)
    if value.hour % 3 or value.minute or value.second or value.microsecond:
        raise ValueError("window_start must be on an exact UTC three-hour boundary")
    return value


def contract_filename(window_start: datetime) -> str:
    """Default CAM7 filename; custom paths/stems are rendered by ``paths``."""
    start = _window_start(window_start)
    return FILENAME_TEMPLATE.format(ymd=start.strftime("%Y%m%d"), secs=start.hour * 3600)


def git_commit() -> str:
    """Current source revision, or ``unknown`` outside a Git checkout."""
    try:
        return subprocess.check_output(
            ["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def default_global_attrs() -> dict[str, Any]:
    """Portable baseline; callers supply actual sources, QC and merge metadata."""
    return {
        "Conventions": "CF-1.10",
        "title": "MODIS/VIIRS L2 AOD (550 nm) on the CAM f09 grid, 3-hourly",
        "institution": "NCAR",
        "source": "unspecified L2 aerosol retrievals",
        "history": f"created by sarb_aod {__version__}",
        "references": (
            "CERES-SARB-MODIS-VIIRS docs/IMPLEMENTATION_PLAN.md; CAM7 obs contract "
            "(CERES-SARB-CAM7 sp1_aod_pipeline/contract.py)"
        ),
        "qc_thresholds": "unspecified",
        "merge_method": "unspecified",
        "merge_version": f"sarb_aod-{__version__}",
        "bias_correction_method": "none",
        "bias_correction_version": "none",
        "grid": "f09 FV 0.9x1.25 (192x288)",
        "aod_wavelength_nm": np.int32(550),
        "date_created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": git_commit(),
    }


def _values(array: ArrayLike, name: str) -> np.ndarray:
    values = np.ma.asarray(array, dtype=np.float64).filled(np.nan)
    if values.shape != (NLAT, NLON):
        raise ValueError(f"{name} shape {values.shape} must be ({NLAT}, {NLON})")
    return values


def _float_field(array: ArrayLike, name: str) -> np.ndarray:
    values = _values(array, name)
    finite = np.isfinite(values) & (np.abs(values) <= np.finfo(np.float32).max)
    return np.where(finite, values, FILL_FLOAT).astype(np.float32)


def _count_field(array: ArrayLike, name: str) -> np.ndarray:
    values = _values(array, name)
    values = np.where(np.isfinite(values), values, 0)
    if np.any((values < 0) | (values > np.iinfo(np.int32).max) | (values != np.floor(values))):
        raise ValueError(f"{name} must contain nonnegative integers representable as int32")
    return values.astype(np.int32)


def write_contract_file(
    out_path: str | Path,
    *,
    window_start: datetime,
    aod_weighted_mean: ArrayLike,
    aod_uncertainty: ArrayLike,
    aod_count: ArrayLike,
    aod_weighted_mean_modis: ArrayLike | None = None,
    aod_weighted_mean_viirs: ArrayLike | None = None,
    global_attrs: Mapping[str, Any] | None = None,
    extra_fields: Mapping[str, ArrayLike] | None = None,
    combined_product: CombinedWindow | None = None,
    clip_negative_mean: bool = True,
) -> Path:
    """Write one complete f09 window to a temporary file, then rename it.

    The existing destination survives any failure before rename. AOD mean fill
    and CAM-invalid values (<= -900) remain fill when clipping; only finite,
    otherwise-valid negative estimates are clipped. Per-stream means supplied
    through ``extra_fields`` retain negatives. ``aod_count_clipped_negative``
    is a per-cell indicator for clipping of the primary combined estimate.
    """
    start = _window_start(window_start)
    center = start + timedelta(seconds=WINDOW_LENGTH_SEC // 2)
    end = start + timedelta(seconds=WINDOW_LENGTH_SEC)
    fields: dict[str, np.ndarray] = {}
    for name, array in zip(
        _MEAN_NAMES,
        (aod_weighted_mean, aod_weighted_mean_modis, aod_weighted_mean_viirs),
        strict=True,
    ):
        values = (
            np.full((NLAT, NLON), FILL_FLOAT, dtype=np.float32)
            if array is None
            else _float_field(array, name)
        )
        values[values <= -900] = FILL_FLOAT
        negative = (values > -900) & (values < 0)
        if name == "aod_weighted_mean":
            fields["aod_count_clipped_negative"] = (negative & clip_negative_mean).astype(np.int32)
        if clip_negative_mean:
            values[negative] = 0
        fields[name] = values
    fields["aod_uncertainty"] = _float_field(aod_uncertainty, "aod_uncertainty")
    fields["aod_count"] = _count_field(aod_count, "aod_count")
    for name, array in (extra_fields or {}).items():
        if name in _RESERVED_FIELDS or re.fullmatch(r"aod_[A-Za-z][A-Za-z0-9_]*", name) is None:
            raise ValueError(f"extra field {name!r} must be an unreserved aod_* variable")
        fields[name] = (
            _count_field(array, name)
            if name.startswith("aod_count_")
            else _float_field(array, name)
        )
    attrs = default_global_attrs()
    attrs.update(global_attrs or {})
    if attrs["Conventions"] != "CF-1.10":
        raise ValueError("Conventions must remain CF-1.10 for the CAM7 contract")

    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with nc.Dataset(temporary, "w", format="NETCDF4") as dataset:
            dataset.createDimension("lat", NLAT)
            dataset.createDimension("lon", NLON)
            dataset.createDimension("nbnds", NBNDS)
            for name, data, units, standard in (
                ("lat", f09_lat(), "degrees_north", "latitude"),
                ("lon", f09_lon(), "degrees_east", "longitude"),
            ):
                variable = dataset.createVariable(name, "f8", (name,))
                variable.units = units
                variable.standard_name = standard
                variable.bounds = f"{name}_bnds"
                variable[:] = data
            if combined_product is not None:
                _write_combined(dataset, combined_product)
            dataset.createVariable("lat_bnds", "f8", ("lat", "nbnds"))[:] = f09_lat_bnds()
            dataset.createVariable("lon_bnds", "f8", ("lon", "nbnds"))[:] = f09_lon_bnds()
            time = dataset.createVariable("time", "f8", ())
            time.units = TIME_UNITS
            time.calendar = TIME_CALENDAR
            time.standard_name = "time"
            time.bounds = "time_bnds"
            time[...] = cf_hours(center)
            dataset.createVariable("time_bnds", "f8", ("nbnds",))[:] = [
                cf_hours(start),
                cf_hours(end),
            ]
            descriptions = {
                "aod_weighted_mean": "Merged MODIS+VIIRS column AOD at 550 nm",
                "aod_weighted_mean_modis": "MODIS-only column AOD at 550 nm",
                "aod_weighted_mean_viirs": "VIIRS-only column AOD at 550 nm",
                "aod_uncertainty": "1-sigma uncertainty of aod_weighted_mean",
                "aod_count": "number of valid L2 pixels contributing to the cell-window",
                "aod_count_clipped_negative": "indicator of a negative primary AOD mean clipped to zero",
            }
            for name, data in fields.items():
                count = name == "aod_count" or name.startswith("aod_count_")
                variable = dataset.createVariable(
                    name,
                    "i4" if count else "f4",
                    ("lat", "lon"),
                    fill_value=FILL_COUNT if count else FILL_FLOAT,
                )
                variable.units = "1"
                variable.long_name = descriptions.get(name, name.replace("_", " "))
                if name == "aod_weighted_mean":
                    variable.standard_name = PRIMARY_STD_NAME
                variable[:] = data
            dataset.setncatts(attrs)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _write_combined(dataset: Any, product: CombinedWindow) -> None:
    if product.category_counts.shape != (3, NLAT, NLON):
        raise ValueError("combined category counts must have shape (3, 192, 288)")
    counts = np.stack([_count_field(c, "combined count") for c in product.category_counts])
    total = _count_field(counts.astype(np.int64).sum(axis=0), TOTAL_NAME)
    mean = _float_field(product.mean, AOD_NAME)
    if np.any((total > 0) & (mean <= -900)) or np.any((total == 0) & (mean != -999)):
        raise ValueError("combined product means and counts disagree")
    for name, values in [(TOTAL_NAME, total), *zip(COUNT_NAMES, counts, strict=True)]:
        v = dataset.createVariable(name, "i4", ("lat", "lon"))
        v.units = "1"
        v.long_name = name.replace("_", " ")
        v[:] = values
    v = dataset.createVariable(AOD_NAME, "f4", ("lat", "lon"), fill_value=FILL_FLOAT)
    v.units = "1"
    v.long_name = "Arithmetic mean of the supplied MODIS DT/DB combined AOD at 550 nm"
    v.cell_methods = "time: mean area: mean"
    v.ancillary_variables = f"{TOTAL_NAME} {FLAG_NAME}"
    v[:] = mean
    flags = np.where(total > 0, np.argmax(counts, axis=0), -999).astype(np.int16)
    flags[(counts > 0).sum(axis=0) > 1] = 3
    flag = dataset.createVariable(FLAG_NAME, "i2", ("lat", "lon"), fill_value=np.int16(-999))
    flag.units = "1"
    flag.long_name = "Source algorithm categories contributing to the gridded combined AOD"
    flag.flag_values = np.arange(4, dtype=np.int16)
    flag.flag_meanings = "dark_target deep_blue native_mixed multiple_source_categories"
    flag.comment = "0, 1, 2 retain source meanings when all contributing pixels share one category; 3 denotes multiple categories. Counts retain the full category distribution."
    flag.ancillary_variables = " ".join(COUNT_NAMES)
    flag[:] = flags


def _validate_combined(dataset: Any, errors: list[str]) -> None:
    names = (AOD_NAME, FLAG_NAME, TOTAL_NAME, *COUNT_NAMES)
    if not any(name in dataset.variables for name in names) and not hasattr(
        dataset, "combined_product_config"
    ):
        return
    if any(name not in dataset.variables for name in names):
        errors.append("incomplete supplied combined-product variables")
        return
    for name in names:
        expected = "f4" if name == AOD_NAME else "i2" if name == FLAG_NAME else "i4"
        if dataset[name].dimensions != ("lat", "lon") or dataset[name].dtype != np.dtype(expected):
            errors.append(f"invalid supplied combined-product layout: {name}")
            return
    counts = np.stack([np.asarray(dataset[name][:]) for name in COUNT_NAMES])
    total = np.asarray(dataset[TOTAL_NAME][:])
    mean = np.asarray(dataset[AOD_NAME][:])
    flag = np.asarray(dataset[FLAG_NAME][:])
    expected_flags = np.where(total > 0, np.argmax(counts, axis=0), -999)
    expected_flags[(counts > 0).sum(axis=0) > 1] = 3
    if np.any(counts < 0) or not np.array_equal(total, counts.astype(np.int64).sum(axis=0)):
        errors.append("combined-product category counts disagree with total")
    if not np.array_equal(flag, expected_flags):
        errors.append("combined-product flags disagree with category counts")
    if (
        not np.isfinite(mean).all()
        or np.any((total == 0) & (mean != -999))
        or np.any((total > 0) & (mean <= -900))
    ):
        errors.append("combined-product means disagree with counts")


def validate_contract_file(path: str | Path) -> tuple[bool, list[str]]:
    """Check the CAM7/SP1 layout, f09 coordinates and UTC window semantics."""
    errors: list[str] = []
    try:
        with nc.Dataset(path) as dataset:
            dataset.set_auto_mask(False)
            _validate_dataset(dataset, errors)
    except (OSError, ValueError, TypeError, IndexError, OverflowError) as exc:
        errors.append(f"cannot validate NetCDF file: {exc}")
    return not errors, errors


def _validate_dataset(dataset: Any, errors: list[str]) -> None:
    _validate_combined(dataset, errors)
    for name, length in (("lat", NLAT), ("lon", NLON), ("nbnds", NBNDS)):
        if name not in dataset.dimensions:
            errors.append(f"missing dim {name}")
        elif len(dataset.dimensions[name]) != length:
            errors.append(f"dim {name} must have length {length}")

    def check(
        name: str,
        dimensions: tuple[str, ...],
        dtype: str,
        attrs: Mapping[str, Any] | None = None,
    ) -> Any:
        if name not in dataset.variables:
            errors.append(f"missing required var {name}")
            return None
        variable = dataset.variables[name]
        if variable.dimensions != dimensions:
            errors.append(f"{name} dims {variable.dimensions} expected {dimensions}")
        if variable.dtype != np.dtype(dtype):
            errors.append(f"{name} dtype {variable.dtype} expected {dtype}")
        for attribute, expected in (attrs or {}).items():
            if getattr(variable, attribute, None) != expected:
                errors.append(f"{name}.{attribute} must equal {expected!r}")
        return variable

    for name, expected, units, standard in (
        ("lat", f09_lat(), "degrees_north", "latitude"),
        ("lon", f09_lon(), "degrees_east", "longitude"),
    ):
        variable = check(
            name,
            (name,),
            "float64",
            {"units": units, "standard_name": standard, "bounds": f"{name}_bnds"},
        )
        if variable is not None and (
            variable.shape != expected.shape
            or not np.allclose(variable[:], expected, rtol=0, atol=1e-6)
        ):
            errors.append(f"{name} centers do not match f09")
    for name, expected, dimensions in (
        ("lat_bnds", f09_lat_bnds(), ("lat", "nbnds")),
        ("lon_bnds", f09_lon_bnds(), ("lon", "nbnds")),
    ):
        variable = check(name, dimensions, "float64")
        if variable is not None and (
            variable.shape != expected.shape
            or not np.allclose(variable[:], expected, rtol=0, atol=1e-6)
        ):
            errors.append(f"{name} does not match canonical f09 bounds")
    time = check(
        "time",
        (),
        "float64",
        {
            "units": TIME_UNITS,
            "calendar": TIME_CALENDAR,
            "standard_name": "time",
            "bounds": "time_bnds",
        },
    )
    bounds = check("time_bnds", ("nbnds",), "float64")
    if time is not None and bounds is not None and time.shape == () and bounds.shape == (2,):
        center, edges = float(time[...]), np.asarray(bounds[:])
        if not np.isfinite(center) or not np.isfinite(edges).all():
            errors.append("time and time_bnds must be finite")
        else:
            if not np.isclose(edges[1] - edges[0], 3.0, rtol=0, atol=1e-9):
                errors.append("time_bnds must span exactly three hours")
            if not np.isclose(center, edges.mean(), rtol=0, atol=1e-9):
                errors.append("time must be the window center")
            if not np.isclose(edges[0] / 3, round(edges[0] / 3), rtol=0, atol=1e-9):
                errors.append("time_bnds must begin on a UTC three-hour boundary")

    for name in (*_MEAN_NAMES, "aod_uncertainty", "aod_count"):
        count = name == "aod_count"
        attrs: dict[str, Any] = {"units": "1", "_FillValue": 0 if count else -999.0}
        if name == "aod_weighted_mean":
            attrs["standard_name"] = PRIMARY_STD_NAME
        variable = check(name, ("lat", "lon"), "int32" if count else "float32", attrs)
        if variable is not None:
            values = np.asarray(variable[:])
            if not np.isfinite(values).all():
                errors.append(f"{name} contains nonfinite values instead of fill")
            if count and np.any(values < 0):
                errors.append("aod_count must be nonnegative")
    for attribute in REQUIRED_GLOBAL_ATTRS:
        if attribute not in dataset.ncattrs():
            errors.append(f"missing global attr {attribute}")
    if getattr(dataset, "Conventions", None) != "CF-1.10":
        errors.append("Conventions must equal CF-1.10")
