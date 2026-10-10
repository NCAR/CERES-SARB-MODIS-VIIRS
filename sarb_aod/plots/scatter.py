"""Density scatter adapted from DAVINCI plots/renderers/scatter.py (Apache-2.0)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
from matplotlib.axes import Axes
from matplotlib.colors import LogNorm
from matplotlib.figure import Figure
from numpy.typing import ArrayLike

from sarb_aod.evaluation.metrics import DEFAULT_EE, EEEnvelope, expected_error, regression
from sarb_aod.plots import labeling
from sarb_aod.plots._stats import annotation_metrics
from sarb_aod.plots.series import _finish, _new_figure
from sarb_aod.plots.style import NCAR_ACCENT, NCAR_SECONDARY, X_COLOR, get_density_cmap


def paired_values(x: ArrayLike, y: ArrayLike) -> tuple[np.ndarray, np.ndarray]:
    left, right = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if left.shape != right.shape:
        raise ValueError("paired plot arrays must have identical shapes")
    valid = np.isfinite(left) & np.isfinite(right)
    return left[valid], right[valid]


def density_scatter(
    x: ArrayLike,
    y: ArrayLike,
    *,
    source: str = "modis",
    reference: str = "aeronet",
    envelope: str | EEEnvelope | None = "dt_land",
    start: Any = None,
    end: Any = None,
    bins: int = 50,
) -> Figure:
    """Render finite pairs with unrounded registry metrics and OLS/RMA fits."""
    figure, axis = _new_figure(figsize=(7.2, 6))
    record = _draw_scatter(
        figure, axis, x, y, source=source, reference=reference, envelope=envelope, bins=bins
    )
    return _finish(
        figure,
        [axis],
        kind="density_scatter",
        title=labeling.title_text("AOD"),
        start=start,
        end=end,
        **record,
    )


def density_scatter_panels(
    pairs: Mapping[str, tuple[ArrayLike, ArrayLike]],
    *,
    source: str = "modis",
    reference: str = "aeronet",
    envelope: str | EEEnvelope | None = None,
    start: Any = None,
    end: Any = None,
    bins: int = 50,
    columns: int = 2,
) -> Figure:
    """Regional small multiples; omit an EE envelope for mixed retrieval families."""
    if not pairs or columns < 1:
        raise ValueError("scatter panels require pairs and a positive column count")
    columns = min(columns, len(pairs))
    rows = (len(pairs) + columns - 1) // columns
    figure, axes = _new_figure(
        figsize=(7.2 * columns, 6 * rows), nrows=rows, ncols=columns, squeeze=False
    )
    records, headings, data_axes = [], [], []
    for axis, (region, (left, right)) in zip(axes.ravel(), pairs.items(), strict=False):
        records.append(
            _draw_scatter(
                figure,
                axis,
                left,
                right,
                source=source,
                reference=reference,
                envelope=envelope,
                bins=bins,
            )
        )
        headings.append(axis.set_title(labeling.title_for_labeled_subset("AOD", region)[0]))
        data_axes.append(axis)
    for axis in axes.ravel()[len(pairs) :]:
        figure.delaxes(axis)
    combined = {
        key: [artist for record in records for artist in record[key]]
        for key in ("dense", "colorbars", "statistics_annotations")
    }
    result = _finish(
        figure,
        data_axes,
        kind="density_scatter_panels",
        title=labeling.title_text("AOD"),
        start=start,
        end=end,
        panel_metrics={
            region: record["metrics"] for region, record in zip(pairs, records, strict=True)
        },
        **combined,
    )
    getattr(result, "_sarb_plot_metadata")["titles"].extend(headings)
    return result


def _draw_scatter(
    figure: Figure,
    axis: Axes,
    x: ArrayLike,
    y: ArrayLike,
    *,
    source: str,
    reference: str,
    envelope: str | EEEnvelope | None,
    bins: int,
) -> dict[str, Any]:
    if bins < 2:
        raise ValueError("density bins must be at least two")
    left, right = paired_values(x, y)
    dense, colorbars = [], []
    lower = min(0.0, float(min(left.min(), right.min()))) if left.size else 0.0
    upper = max(0.1, float(max(left.max(), right.max()))) if left.size else 1.0
    span = max(upper - lower, 0.1)
    limits = (lower - 0.025 * span, upper + 0.025 * span)
    if left.size:
        counts, x_edges, y_edges = np.histogram2d(left, right, bins=bins, range=[limits, limits])
        ix = np.clip(np.searchsorted(x_edges, left, side="right") - 1, 0, bins - 1)
        iy = np.clip(np.searchsorted(y_edges, right, side="right") - 1, 0, bins - 1)
        density = counts[ix, iy]
        order = np.argsort(density)
        points = axis.scatter(
            left[order],
            right[order],
            c=density[order],
            s=9,
            edgecolors="none",
            cmap=get_density_cmap(),
            norm=LogNorm() if density.max() / density.min() > 10 else None,
            rasterized=True,
        )
        dense.append(points)
        colorbar = figure.colorbar(
            points, ax=axis, label=labeling.axis_label("Pair Count", "1"), shrink=0.85
        )
        if colorbar.solids is not None:
            colorbar.solids.set_rasterized(False)
        colorbars.append(colorbar)
    else:
        axis.text(
            0.5, 0.5, labeling.reference_label("no_data"), transform=axis.transAxes, ha="center"
        )
    coordinate = np.linspace(*limits, 200)
    axis.plot(
        coordinate,
        coordinate,
        color=X_COLOR,
        linestyle="--",
        linewidth=1,
        label=labeling.reference_label("one_to_one"),
    )
    spec = DEFAULT_EE[envelope] if isinstance(envelope, str) else envelope
    if spec is not None:
        lower_ee = spec.lower_intercept + spec.lower_slope * coordinate
        upper_ee = spec.upper_intercept + spec.upper_slope * coordinate
        axis.plot(
            coordinate,
            np.where(lower_ee >= 0, coordinate - lower_ee, np.nan),
            color=X_COLOR,
            linestyle=":",
            label=labeling.reference_label("expected_error"),
        )
        axis.plot(
            coordinate,
            np.where(upper_ee >= 0, coordinate + upper_ee, np.nan),
            color=X_COLOR,
            linestyle=":",
        )
    fits = regression(left, right)
    for method, color in (("ols", NCAR_ACCENT), ("rma", NCAR_SECONDARY)):
        slope, intercept = fits[f"{method}_slope"], fits[f"{method}_intercept"]
        if np.isfinite(slope) and np.isfinite(intercept):
            axis.plot(
                coordinate,
                slope * coordinate + intercept,
                color=color,
                label=labeling.regression_label(method, slope, intercept),
            )
    stats = annotation_metrics(left, right, ["N", "MB", "RMSE", "R"])
    if envelope is not None:
        stats["pct_ee"] = expected_error(left, right, envelope)["pct_ee"]
    annotation = axis.text(
        0.97,
        0.03,
        labeling.statistics_text(stats),
        transform=axis.transAxes,
        ha="right",
        va="bottom",
        fontsize=9,
        bbox={"facecolor": "white", "alpha": 0.9, "edgecolor": "none"},
    )
    axis.legend(loc="upper left", fontsize=8)
    axis.set(xlim=limits, ylim=limits, aspect="equal")
    axis.set_xlabel(labeling.axis_label("AOD", "1", reference))
    axis.set_ylabel(labeling.axis_label("AOD", "1", source))
    return dict(
        dense=dense,
        colorbars=colorbars,
        metrics=stats,
        regression=fits,
        statistics_annotations=[annotation],
    )
