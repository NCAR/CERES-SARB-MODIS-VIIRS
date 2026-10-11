"""Typed paired-AOD NetCDF extension; the canonical CAM product remains unfiltered."""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from typing import Any

import numpy as np

from sarb_aod.config.schema import DespeckleConfig
from sarb_aod.despeckle import DESPECKLE_VERSION, FLAG_FILL, FilteredGrid, SourceFilter
from sarb_aod.grid.spec import NLAT, NLON

PRODUCT_NAMES = ("aod_weighted_mean", "aod_weighted_mean_modis", "aod_weighted_mean_viirs")
DIAGNOSTICS = {
    "support_fraction": ("support_fraction", "f4", -999),
    "neighbor_count": ("neighbor_count", "i4", -1),
    "donor_count": ("donor_count", "i4", -1),
    "donor_fraction": ("donor_fraction", "f4", -999),
}
ACTION_MEANINGS = "unchanged isolated_masked spike_replaced spike_masked insufficient_donors_masked"
BIT_MEANINGS = "isolated_masked spike_replaced spike_masked insufficient_donors_masked"


@dataclass(frozen=True)
class DespeckleWindow:
    settings: DespeckleConfig
    products: dict[str, FilteredGrid]
    sources: dict[str, SourceFilter]


def slice_result(result: Any, index: int) -> Any:
    """Slice a leading window axis while preserving the typed filter result."""
    return type(result)(*(getattr(result, field.name)[index] for field in fields(result)))


def _write(
    dataset: Any,
    name: str,
    values: np.ndarray,
    *,
    dtype: str = "f4",
    fill: int = -999,
    **attrs: Any,
) -> None:
    values = np.asarray(values)
    if values.shape != (NLAT, NLON) or not np.isfinite(values).all():
        raise ValueError(f"invalid despeckle field {name}: shape or nonfinite data")
    if dtype.startswith("i") and (
        np.any(values != np.floor(values))
        or np.any(values < np.iinfo(dtype).min)
        or np.any(values > np.iinfo(dtype).max)
    ):
        raise ValueError(f"invalid despeckle integer field {name}")
    if dtype == "f4" and np.any(np.abs(values) > np.finfo(np.float32).max):
        raise ValueError(f"despeckle field {name} exceeds float32 range")
    v = dataset.createVariable(name, dtype, ("lat", "lon"), fill_value=fill, zlib=True, complevel=1)
    v.units = "1"
    v.long_name = name.replace("_", " ")
    v.setncatts(attrs)
    v[:] = values


def write_despeckle(dataset: Any, block: DespeckleWindow, *, clip: bool) -> None:
    if not block.settings.enabled or set(block.products) != set(PRODUCT_NAMES) or not block.sources:
        raise ValueError(
            "despeckle block requires enabled settings, all products and selected sources"
        )
    for name in (*PRODUCT_NAMES, *(f"aod_mean_{stream}" for stream in block.sources)):
        original = np.asarray(dataset[name][:])
        filtered = (
            block.products[name].mean
            if name in block.products
            else block.sources[name.removeprefix("aod_mean_")].mean
        )
        filtered = np.array(filtered, dtype=np.float64, copy=True)
        if clip and name in PRODUCT_NAMES:
            filtered[(filtered > -900) & (filtered < 0)] = 0
        for variant, values in (("unfiltered", original), ("filtered", filtered)):
            _write(
                dataset,
                f"{name}_{variant}",
                values,
                aod_wavelength_nm=550,
                processing_variant=variant,
                comment="Experimental spatial comparison; canonical CAM field remains unfiltered.",
            )
    for stream, result in block.sources.items():
        flag = f"aod_despeckle_flag_{stream}"
        _write(
            dataset,
            flag,
            result.flag,
            dtype="i1",
            fill=FLAG_FILL,
            flag_values=np.arange(5, dtype=np.int8),
            flag_meanings=ACTION_MEANINGS,
        )
        dataset[f"aod_mean_{stream}_filtered"].ancillary_variables = flag
        for name, (member, dtype, fill) in DIAGNOSTICS.items():
            _write(
                dataset,
                f"aod_despeckle_{name}_{stream}",
                getattr(result, member),
                dtype=dtype,
                fill=fill,
            )
    primary = block.products["aod_weighted_mean"]
    _write(
        dataset,
        "aod_despeckle_flags",
        primary.flags,
        dtype="i1",
        fill=FLAG_FILL,
        flag_masks=np.array([1, 2, 4, 8], dtype=np.int8),
        flag_meanings=BIT_MEANINGS,
    )
    _write(
        dataset,
        "aod_uncertainty_filtered",
        primary.sigma,
        comment="Baseline 1-sigma only for unchanged combinations; altered estimates have no assigned uncertainty.",
    )
    dataset["aod_weighted_mean_filtered"].ancillary_variables = (
        "aod_despeckle_flags aod_uncertainty_filtered"
    )
    dataset.setncatts(
        {
            "despeckle_config": block.settings.model_dump_json(),
            "despeckle_version": DESPECKLE_VERSION,
            "despeckle_streams": json.dumps(list(block.sources)),
            "despeckle_method": "source-wise geographic support and one-sided area-weighted median/MAD spike filter",
            "despeckle_geometry": "great-circle center-in-radius; finite spherical cell areas; target cell excluded",
            "despeckle_order": "original per-source three-hour means; classify once; original unflagged donors; combine; final CAM clipping",
            "despeckle_combination": "original source coefficients retained for replacements; masked sources excluded; experimental fixed-weight comparison",
            "despeckle_temporal_weighting": "original observation counts; selected variant validity; replacements are not additional observations",
            "despeckle_uncertainty": "unavailable for any altered contributing source; baseline sigma otherwise",
            "cam_aod_variant": "unfiltered",
            "despeckle_action_counts": json.dumps(
                {
                    stream: {str(i): int(np.count_nonzero(result.flag == i)) for i in range(5)}
                    for stream, result in block.sources.items()
                },
                sort_keys=True,
            ),
            "despeckle_action_counts_scope": "this three-hour window",
        }
    )
    dataset.set_auto_mask(False)
    errors: list[str] = []
    validate_despeckle(dataset, errors)
    if errors:
        raise ValueError("; ".join(errors))


def validate_despeckle(dataset: Any, errors: list[str]) -> None:
    declared = False
    try:
        declared = bool(
            json.loads(getattr(dataset, "config", "{}")).get("despeckle", {}).get("enabled")
        )
    except (ValueError, TypeError, AttributeError):
        pass  # Legacy files need not have parseable configuration metadata.
    present = any(
        name.startswith("aod_despeckle_") or name.endswith("_filtered")
        for name in dataset.variables
    )
    if not (declared or present or hasattr(dataset, "despeckle_config")):
        return
    try:
        settings = DespeckleConfig.model_validate_json(dataset.despeckle_config)
        configuration = json.loads(getattr(dataset, "config", "{}"))
        if "despeckle" in configuration and settings != DespeckleConfig.model_validate(
            configuration["despeckle"]
        ):
            raise ValueError("despeckle settings differ from the recorded run configuration")
        streams = json.loads(dataset.despeckle_streams)
        if (
            not settings.enabled
            or not isinstance(streams, list)
            or not streams
            or any(not isinstance(s, str) for s in streams)
            or len(streams) != len(set(streams))
        ):
            raise ValueError("invalid enabled settings or source list")
        if getattr(dataset, "streams", ",".join(streams)).split(",") != streams:
            raise ValueError("despeckle source list differs from primary streams")
        if (
            getattr(dataset, "cam_aod_variant", None) != "unfiltered"
            or getattr(dataset, "despeckle_version", None) != DESPECKLE_VERSION
        ):
            raise ValueError("missing or unsupported despeckle version/variant")
    except (AttributeError, ValueError, TypeError) as exc:
        errors.append(f"invalid despeckle metadata: {exc}")
        return
    required = {
        f"{name}_{variant}": ("f4", -999)
        for name in (*PRODUCT_NAMES, *(f"aod_mean_{s}" for s in streams))
        for variant in ("unfiltered", "filtered")
    }
    required.update(
        {"aod_despeckle_flags": ("i1", FLAG_FILL), "aod_uncertainty_filtered": ("f4", -999)}
    )
    for s in streams:
        required[f"aod_despeckle_flag_{s}"] = ("i1", FLAG_FILL)
        for name, (_, dtype, fill) in DIAGNOSTICS.items():
            required[f"aod_despeckle_{name}_{s}"] = (dtype, fill)
    arrays = {}
    for name, (dtype, fill) in required.items():
        if name not in dataset.variables:
            errors.append(f"missing despeckle variable {name}")
            continue
        v = dataset[name]
        if (
            v.dimensions != ("lat", "lon")
            or v.shape != (NLAT, NLON)
            or v.dtype != np.dtype(dtype)
            or getattr(v, "_FillValue", None) != fill
            or getattr(v, "units", None) != "1"
        ):
            errors.append(f"invalid despeckle layout {name}")
            continue
        values = np.asarray(v[:])
        if not np.isfinite(values).all():
            errors.append(f"nonfinite despeckle values {name}")
            continue
        arrays[name] = values
    if len(arrays) != len(required):
        return
    baseline_names = (*PRODUCT_NAMES, "aod_uncertainty", *(f"aod_mean_{s}" for s in streams))
    if (
        any(
            name not in dataset.variables or dataset[name].shape != (NLAT, NLON)
            for name in baseline_names
        )
        or "lat" not in dataset.variables
        or dataset["lat"].shape != (NLAT,)
    ):
        errors.append("missing or malformed baseline variables for despeckling")
        return
    latitude = np.asarray(dataset["lat"][:])
    inside = np.broadcast_to(
        np.any(
            [((latitude >= lo) & (latitude <= hi)) for lo, hi in settings.latitude_bands], axis=0
        )[:, None],
        (NLAT, NLON),
    )
    for name in (*PRODUCT_NAMES, *(f"aod_mean_{s}" for s in streams)):
        if name not in dataset.variables:
            errors.append(f"missing original despeckle source {name}")
            return
        original = arrays[f"{name}_unfiltered"]
        filtered = arrays[f"{name}_filtered"]
        if not np.array_equal(original, np.asarray(dataset[name][:])):
            errors.append(f"despeckle baseline differs from {name}")
        if np.any((original <= -900) & (filtered != -999)) or np.any(
            (filtered <= -900) & (filtered != -999)
        ):
            errors.append(f"despeckle created data or noncanonical fill: {name}")
        if not np.array_equal(original[~inside], filtered[~inside]):
            errors.append(f"despeckle changed data outside latitude bands: {name}")
    expected_bits = np.zeros((NLAT, NLON), dtype=np.int8)
    for s in streams:
        original = arrays[f"aod_mean_{s}_unfiltered"]
        filtered = arrays[f"aod_mean_{s}_filtered"]
        flag = arrays[f"aod_despeckle_flag_{s}"]
        if not np.isin(flag, [FLAG_FILL, 0, 1, 2, 3, 4]).all() or not np.array_equal(
            flag == FLAG_FILL, original <= -900
        ):
            errors.append(f"invalid despeckle action/fill flags: {s}")
        if (
            np.any((flag > 0) & ~inside)
            or np.any((flag == 0) & (original != filtered))
            or np.any(np.isin(flag, [1, 3, 4]) & (filtered != -999))
            or np.any((flag == 2) & (filtered <= -900))
        ):
            errors.append(f"despeckle action disagrees with source values: {s}")
        v = dataset[f"aod_despeckle_flag_{s}"]
        if (
            not np.array_equal(getattr(v, "flag_values", []), np.arange(5))
            or getattr(v, "flag_meanings", None) != ACTION_MEANINGS
        ):
            errors.append(f"invalid despeckle flag metadata: {s}")
        for name in ("support_fraction", "donor_fraction"):
            a = arrays[f"aod_despeckle_{name}_{s}"]
            if np.any((a != -999) & ((a < 0) | (a > 1))):
                errors.append(f"invalid despeckle fraction: {name} {s}")
        for name in ("neighbor_count", "donor_count"):
            if np.any(arrays[f"aod_despeckle_{name}_{s}"] < -1):
                errors.append(f"invalid despeckle neighbor count: {s}")
        for action in range(1, 5):
            expected_bits[flag == action] |= 1 << (action - 1)
    original = arrays["aod_weighted_mean_unfiltered"]
    filtered = arrays["aod_weighted_mean_filtered"]
    flag = arrays["aod_despeckle_flags"]
    expected_bits[original <= -900] = FLAG_FILL
    if not np.array_equal(flag, expected_bits):
        errors.append("aggregate despeckle flags disagree with source actions")
    if np.any((flag == 0) & (original != filtered)):
        errors.append("unflagged aggregate changed by despeckling")
    sigma = arrays["aod_uncertainty_filtered"]
    unchanged = flag == 0
    if np.any(sigma[~unchanged] != -999) or not np.array_equal(
        sigma[unchanged], np.asarray(dataset["aod_uncertainty"][:])[unchanged]
    ):
        errors.append("filtered uncertainty disagrees with change flags")
    v = dataset["aod_despeckle_flags"]
    if (
        not np.array_equal(getattr(v, "flag_masks", []), [1, 2, 4, 8])
        or getattr(v, "flag_meanings", None) != BIT_MEANINGS
    ):
        errors.append("invalid aggregate despeckle flag metadata")
