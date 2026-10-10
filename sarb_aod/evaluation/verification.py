"""Reproducible retrieval and grid diagnostics for bounded science checks."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
from numpy.typing import ArrayLike


def box_mask(lon: ArrayLike, lat: ArrayLike, bounds: Sequence[float]) -> np.ndarray:
    """Inclusive geographic box, allowing wrapped longitudes and dateline crossing."""
    west, east, south, north = bounds
    if not (-180 <= west <= 180 and -180 <= east <= 180 and -90 <= south <= north <= 90):
        raise ValueError("box must use valid geographic bounds")
    longitude, latitude = np.broadcast_arrays(
        np.asarray(lon, dtype=float), np.asarray(lat, dtype=float)
    )
    longitude = (longitude + 180) % 360 - 180
    longitudinal = (
        (longitude >= west) & (longitude <= east)
        if west <= east
        else (longitude >= west) | (longitude <= east)
    )
    return (
        np.isfinite(longitude)
        & np.isfinite(latitude)
        & longitudinal
        & (latitude >= south)
        & (latitude <= north)
    )


def pixel_statistics(
    chunks: Sequence[ArrayLike], *, aod_max: float, rejected_high: int = 0
) -> dict[str, Any]:
    """Exact quantiles of accepted retrievals, with separate above-cap rejection.

    ``n_at_or_above_max`` includes accepted values on the inclusive endpoint
    and QA-passing, product-valid values strictly above the configured cap.
    Product fill and native valid-range exclusions are never resurrected.
    """
    values = (
        np.concatenate([np.asarray(chunk, dtype=float).ravel() for chunk in chunks])
        if chunks
        else np.empty(0)
    )
    values = values[np.isfinite(values)]
    if not np.isfinite(aod_max) or rejected_high < 0 or np.any(values > aod_max):
        raise ValueError("accepted AOD must respect a finite configured maximum")
    return {
        "n_qc_passed": int(values.size),
        "p50": float(np.percentile(values, 50)) if values.size else None,
        "p90": float(np.percentile(values, 90)) if values.size else None,
        "maximum": float(values.max()) if values.size else None,
        "n_at_or_above_max": int(np.count_nonzero(values >= aod_max)) + rejected_high,
        "n_rejected_high": rejected_high,
    }


def grid_statistics(
    mean: ArrayLike,
    modis: ArrayLike,
    viirs: ArrayLike,
    chi2: ArrayLike,
    rejected_high: ArrayLike,
    mask: ArrayLike,
    *,
    tolerance: float = 1e-6,
) -> dict[str, Any]:
    """Cellwise mixed-product convexity and smoke disagreement diagnostics."""
    arrays: list[np.ndarray] = [
        np.asarray(np.ma.asarray(value, dtype=float).filled(np.nan))
        for value in (mean, modis, viirs, chi2, rejected_high)
    ]
    selected = np.asarray(mask, dtype=bool)
    if any(value.shape != selected.shape for value in arrays):
        raise ValueError("grid fields and smoke mask must have identical shapes")
    if not np.isfinite(tolerance) or tolerance < 0:
        raise ValueError("convexity tolerance must be finite and nonnegative")
    mixed, left, right, disagreement, high = arrays
    comparable = np.isfinite(left) & np.isfinite(right) & (left > -900) & (right > -900)
    excursion = np.maximum(np.minimum(left, right) - mixed, mixed - np.maximum(left, right))
    valid_mixed = np.isfinite(mixed) & (mixed > -900)
    outside = comparable & (~valid_mixed | (excursion > tolerance))
    valid_chi2 = selected & np.isfinite(disagreement) & (disagreement >= 0)
    return {
        "n_companion_cells_global": int(np.count_nonzero(comparable)),
        "n_outside_companions_global": int(np.count_nonzero(outside)),
        "n_missing_mixed_global": int(np.count_nonzero(comparable & ~valid_mixed)),
        "n_companion_cells_box": int(np.count_nonzero(comparable & selected)),
        "n_outside_companions_box": int(np.count_nonzero(outside & selected)),
        "maximum_excursion": (
            max(0.0, float(excursion[comparable & valid_mixed].max()))
            if np.any(comparable & valid_mixed)
            else 0.0
        ),
        "n_chi2_gt_one_box": int(np.count_nonzero(valid_chi2 & (disagreement > 1))),
        "maximum_chi2_box": float(disagreement[valid_chi2].max()) if np.any(valid_chi2) else None,
        "n_rejected_high_global": int(np.nansum(np.where(high >= 0, high, 0))),
        "n_rejected_high_box": int(np.nansum(np.where(selected & (high >= 0), high, 0))),
    }
