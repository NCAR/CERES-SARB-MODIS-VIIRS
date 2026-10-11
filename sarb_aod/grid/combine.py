"""Combine per-stream sufficient statistics into CAM7 aerosol estimates.

Count weighting follows NCAR/CERES-SARB-CAM7
``scripts/prepare_modis_era5_grid.py``. GLS uses the within-stream correlated
error model in ``docs/IMPLEMENTATION_PLAN.md`` Task 1.7; independent streams
are combined by inverse variance, with optional Birge uncertainty inflation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np

from .kernels import Accumulators

FILL_FLOAT = -999.0


@dataclass(frozen=True)
class CombinedGrid:
    """Combined arrays in ``(window, lat, lon)`` order.

    ``stream_mean`` and ``stream_count`` retain a stream axis at position 1,
    ordered by ``stream_indices``. Means are deliberately not clipped at zero;
    clipping belongs to the output writer. ``stream_weight`` exposes the original
    source coefficients (N for count, 1/V for GLS), zero for inestimable means.

    ``count`` is zero wherever no product can be estimated, including cells
    below ``min_pixels`` and GLS cells with no usable sigma. In contrast,
    ``stream_count`` and ``count_*`` diagnostics retain raw accepted/rejected
    counts, even in missing-product cells, so missing uncertainty and QC losses
    remain visible. Floating diagnostics use -999 when undefined.
    """

    mean: np.ndarray
    sigma: np.ndarray
    count: np.ndarray
    n_effective: np.ndarray
    chi2_per_dof: np.ndarray
    stream_mean: np.ndarray
    stream_count: np.ndarray
    stream_weight: np.ndarray
    stream_indices: tuple[int, ...]
    count_negative: np.ndarray
    count_sigma_missing: np.ndarray
    count_rejected_high: np.ndarray


def combine(
    acc: Accumulators,
    *,
    method: Literal["gls", "count"] = "gls",
    rho: float = 0.5,
    birge: bool = True,
    min_pixels: int = 1,
    single_pixel_sigma: Literal["floor", "zero"] = "floor",
    sigma_floor: float = 0.01,
    stream_indices: Sequence[int] | None = None,
) -> CombinedGrid:
    """Estimate AOD and uncertainty without mutating any input accumulators.

    Select streams to make instrument-only companion estimates; an empty
    selection returns fill estimates and zero counts. ``min_pixels`` uses all
    accepted pixels, including those without sigma. Such pixels contribute to
    count weighting and raw counts, but never GLS weighted sums.
    """
    if method not in ("gls", "count"):
        raise ValueError("weighting.method must be 'gls' or 'count'")
    if not np.isfinite(rho) or not 0 <= rho <= 1:
        raise ValueError("weighting.rho must be finite and in [0, 1]")
    if (
        isinstance(min_pixels, bool)
        or not isinstance(min_pixels, (int, np.integer))
        or min_pixels < 1
    ):
        raise ValueError("weighting.min_pixels must be a positive integer")
    if single_pixel_sigma not in ("floor", "zero"):
        raise ValueError("weighting.single_pixel_sigma must be 'floor' or 'zero'")
    if not np.isfinite(sigma_floor) or sigma_floor <= 0:
        raise ValueError("sigma_floor must be finite and positive")
    if acc.n.ndim != 4 or any(array.shape != acc.n.shape for array in acc.arrays()):
        raise ValueError("accumulators must have a common (window, stream, lat, lon) shape")
    if acc.n_rejected_high.shape != acc.n.shape:
        raise ValueError("rejected-high counts must have the same accumulator shape")

    indices = tuple(range(acc.n.shape[1])) if stream_indices is None else tuple(stream_indices)
    if any(
        isinstance(index, bool)
        or not isinstance(index, (int, np.integer))
        or not 0 <= index < acc.n.shape[1]
        for index in indices
    ) or len(set(indices)) != len(indices):
        raise ValueError("stream_indices must contain unique valid integer stream indices")
    selection = slice(None) if stream_indices is None else np.asarray(indices, dtype=np.int64)

    n = acc.n[:, selection]
    n_total = n.sum(axis=1, dtype=np.int64)
    shape = n_total.shape
    mean = np.full(shape, FILL_FLOAT)
    sigma = np.full(shape, FILL_FLOAT)
    n_effective = np.full(shape, FILL_FLOAT)
    chi2_per_dof = np.full(shape, FILL_FLOAT)
    stream_mean = np.full(n.shape, FILL_FLOAT)
    enough_pixels = n_total >= min_pixels

    if method == "count":
        weights = n.astype(np.float64)
        sum_x = acc.sum_x[:, selection]
        total_x = sum_x.sum(axis=1)
        total_x2 = acc.sum_x2[:, selection].sum(axis=1)
        np.divide(sum_x, n, out=stream_mean, where=n > 0)
        valid = enough_pixels & np.isfinite(total_x) & np.isfinite(total_x2)
        np.divide(total_x, n_total, out=mean, where=valid)
        multiple = valid & (n_total > 1)
        # Pooled sample variance / N is the standard-error variance, not the
        # variance of equally weighted stream means. Roundoff can make the
        # centered sum of squares slightly negative for constant samples.
        centered_sum = total_x2[multiple] - total_x[multiple] * mean[multiple]
        sigma[multiple] = np.sqrt(
            np.maximum(centered_sum, 0.0) / (n_total[multiple] - 1) / n_total[multiple]
        )
        sigma[valid & (n_total == 1)] = sigma_floor if single_pixel_sigma == "floor" else 0.0
        n_effective[valid] = n_total[valid]
    else:
        sum_w = acc.sum_w[:, selection]
        sum_wx = acc.sum_wx[:, selection]
        sum_inv_sigma = acc.sum_inv_sigma[:, selection]
        usable = (
            (n > 0)
            & (sum_w > 0)
            & (sum_inv_sigma > 0)
            & np.isfinite(sum_w)
            & np.isfinite(sum_wx)
            & np.isfinite(sum_inv_sigma)
        )
        np.divide(sum_wx, sum_w, out=stream_mean, where=usable)
        mean_pixel_sigma = np.zeros(n.shape)
        np.divide(sum_inv_sigma, sum_w, out=mean_pixel_sigma, where=usable)
        variance = np.zeros(n.shape)
        np.divide(1.0 - rho, sum_w, out=variance, where=usable)
        variance += rho * mean_pixel_sigma**2
        usable &= (variance > 0) & np.isfinite(variance)
        stream_mean[~usable] = FILL_FLOAT
        weights = np.zeros(n.shape)
        np.divide(1.0, variance, out=weights, where=usable)
        total_weight = weights.sum(axis=1)
        valid = enough_pixels & (total_weight > 0) & np.isfinite(total_weight)
        weighted_x = (weights * np.where(usable, stream_mean, 0.0)).sum(axis=1)
        np.divide(weighted_x, total_weight, out=mean, where=valid)
        sigma[valid] = np.sqrt(1.0 / total_weight[valid])
        n_effective[valid] = (mean_pixel_sigma**2 * weights).sum(axis=1)[valid]
        stream_count = usable.sum(axis=1)
        residual = np.where(usable & valid[:, None], stream_mean - mean[:, None], 0.0)
        chi2 = (weights * residual**2).sum(axis=1)
        multiple = valid & (stream_count >= 2)
        np.divide(chi2, stream_count - 1, out=chi2_per_dof, where=multiple)
        if birge:
            inflate = multiple & (chi2_per_dof > 1)
            sigma[inflate] *= np.sqrt(chi2_per_dof[inflate])

    stream_mean[~np.broadcast_to(enough_pixels[:, None], n.shape)] = FILL_FLOAT
    return CombinedGrid(
        mean=mean,
        sigma=sigma,
        count=np.where(valid, n_total, 0),
        n_effective=n_effective,
        chi2_per_dof=chi2_per_dof,
        stream_mean=stream_mean,
        stream_count=n.copy(),
        stream_weight=np.where(np.isfinite(stream_mean) & (stream_mean > -900), weights, 0),
        stream_indices=indices,
        count_negative=acc.n_neg[:, selection].sum(axis=1, dtype=np.int64),
        count_sigma_missing=acc.n_sigma_missing[:, selection].sum(axis=1, dtype=np.int64),
        count_rejected_high=acc.n_rejected_high[:, selection].sum(axis=1, dtype=np.int64),
    )
