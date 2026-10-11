"""Explicit grid comparison identity; L2 retrievals have no filtered variant."""

from pathlib import Path

import numpy as np
import xarray as xr

from sarb_aod.config.schema import RunConfig


def comparison_tag(config: RunConfig, level: str = "grid") -> str:
    if level == "l2":
        return ""
    variant = config.evaluation.aod_variant
    support = config.evaluation.sample_support
    return "" if (variant, support) == ("unfiltered", "available") else f"_{variant}_{support}"


def evaluation_directory(config: RunConfig, level: str = "grid") -> Path:
    root = Path(
        config.paths.evaluation_dir.format(data_root=config.paths.data_root, run=config.name)
    )
    tag = comparison_tag(config, level)
    return root / tag.lstrip("_") if tag else root


def select_grid_variant(dataset: xr.Dataset, config: RunConfig) -> xr.Dataset:
    """Return a shallow view with fixed original counts and explicit validity.

    Common support intersects window validity *before* daily averaging. Thus both
    variants have identical temporal weights and identical eligible site-days.
    """
    variant = config.evaluation.aod_variant
    common = config.evaluation.sample_support == "common"
    if variant == "unfiltered" and not common:
        return dataset
    if not config.despeckle.enabled:
        raise ValueError("Filtered/common-sample evaluation requires enabled despeckling")
    baseline = dataset["aod_weighted_mean_unfiltered"]
    filtered = dataset["aod_weighted_mean_filtered"]
    field = filtered if variant == "filtered" else baseline
    if common:
        valid = (
            np.isfinite(baseline) & (baseline > -900) & np.isfinite(filtered) & (filtered > -900)
        )
        field = field.where(valid)
    result = dataset.assign(aod_weighted_mean=field)
    if variant == "filtered":
        result = result.assign(aod_uncertainty=dataset["aod_uncertainty_filtered"])
    return result


def read_grid_field(dataset: object, config: RunConfig, name: str) -> np.ndarray:
    """NetCDF field selection for plotting and summaries, preserving raw counts."""
    from typing import Any, cast

    ds = cast(Any, dataset)
    original_name = name
    aod = name.startswith(("aod_weighted_mean", "aod_mean_"))
    selected = config.evaluation.aod_variant == "filtered"
    common = config.evaluation.sample_support == "common"
    if (selected or common) and not config.despeckle.enabled:
        raise ValueError("Filtered/common-sample evaluation requires enabled despeckling")
    if selected and (aod or name == "aod_uncertainty"):
        name += "_filtered"
    values = np.asarray(ds[name][:], dtype=float)
    if aod and common:
        before = np.asarray(ds[original_name + "_unfiltered"][:], dtype=float)
        after = np.asarray(ds[original_name + "_filtered"][:], dtype=float)
        values = np.where(
            np.isfinite(before) & (before > -900) & np.isfinite(after) & (after > -900),
            values,
            np.nan,
        )
    return values
