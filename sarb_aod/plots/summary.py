"""AOD validation summaries using DAVINCI scorecard/Taylor/boxplot conventions.

Adapted from NCAR/DAVINCI origin/develop plots/renderers/{scorecard,taylor,
boxplot}.py (Apache-2.0). Statistics remain unrounded until text composition.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd
from matplotlib.artist import Artist
from matplotlib.colors import Normalize, TwoSlopeNorm
from matplotlib.figure import Figure
from matplotlib.patches import Patch
from numpy.typing import ArrayLike

from sarb_aod.evaluation.calculator import binned_bias as calculate_binned_bias
from sarb_aod.plots import labeling
from sarb_aod.plots._stats import annotation_metrics
from sarb_aod.plots.labels import calculate_symmetric_limits
from sarb_aod.plots.scatter import paired_values
from sarb_aod.plots.series import _finish, _new_figure
from sarb_aod.plots.style import NCAR_PALETTE, X_COLOR, Y_COLOR


def binned_bias(
    x: ArrayLike,
    y: ArrayLike,
    *,
    edges: ArrayLike = (0, 0.05, 0.1, 0.2, 0.5, 1, 2, 5),
    source: str = "modis",
    min_samples: int = 3,
    start: Any = None,
    end: Any = None,
) -> Figure:
    table = calculate_binned_bias(x, y, edges, min_samples=min_samples)
    figure, axis = _new_figure()
    centers = (table.aod_lower + table.aod_upper) / 2
    axis.plot(centers, table.MB, marker="o", color=Y_COLOR, label=labeling.legend_label(source))
    axis.axhline(0, color=X_COLOR, linewidth=1)
    axis.set_xlabel(labeling.axis_label("AOD", "1", "aeronet"))
    axis.set_ylabel(labeling.bias_label(source, "aeronet", "1"))
    axis.legend()
    return _finish(
        figure,
        [axis],
        kind="binned_bias",
        title=labeling.title_text("AOD", "Bias"),
        start=start,
        end=end,
        binned_statistics=table,
    )


def z_histogram(
    z: ArrayLike,
    *,
    source: str = "modis",
    bins: int = 40,
    start: Any = None,
    end: Any = None,
) -> Figure:
    values = np.asarray(z, dtype=float).ravel()
    values = values[np.isfinite(values)]
    if bins < 1:
        raise ValueError("histogram bins must be positive")
    figure, axis = _new_figure()
    dense: list[Artist] = []
    if values.size:
        _, _, patches = axis.hist(
            values,
            bins=bins,
            density=True,
            color=Y_COLOR,
            alpha=0.6,
            label=labeling.legend_label(source),
            rasterized=True,
        )
        dense.extend(patches)
    coordinate = np.linspace(
        min(-4, float(values.min())) if values.size else -4,
        max(4, float(values.max())) if values.size else 4,
        501,
    )
    axis.plot(
        coordinate,
        np.exp(-0.5 * coordinate**2) / np.sqrt(2 * np.pi),
        color=X_COLOR,
        label=labeling.reference_label("unit_normal"),
    )
    stats = {
        "N_z": float(values.size),
        "z_mean": float(values.mean()) if values.size else np.nan,
        "z_std": float(values.std(ddof=1)) if values.size > 1 else np.nan,
    }
    annotation = axis.text(
        0.98,
        0.97,
        labeling.statistics_text(stats),
        transform=axis.transAxes,
        va="top",
        ha="right",
        fontsize=9,
    )
    axis.set_xlabel(labeling.axis_label("Normalized Residual", "1"))
    axis.set_ylabel(labeling.axis_label("Probability Density", "1"))
    axis.legend(loc="upper left")
    return _finish(
        figure,
        [axis],
        kind="z_histogram",
        title=labeling.title_text("AOD Residual"),
        start=start,
        end=end,
        dense=dense,
        metrics=stats,
        statistics_annotations=[annotation],
    )


def rho_sweep(
    rho: ArrayLike,
    metrics: Mapping[str, ArrayLike],
    *,
    source: str = "modis",
    start: Any = None,
    end: Any = None,
) -> Figure:
    rhos = np.asarray(rho, dtype=float)
    if (
        rhos.ndim != 1
        or not rhos.size
        or not np.isfinite(rhos).all()
        or ((rhos < 0) | (rhos > 1)).any()
    ):
        raise ValueError("rho sweep requires a finite vector in [0, 1]")
    if not metrics:
        raise ValueError("rho sweep requires at least one metric")
    order = np.argsort(rhos)
    figure, axes = _new_figure(figsize=(4 * len(metrics), 4.5), ncols=len(metrics), squeeze=False)
    for axis, (metric, raw) in zip(axes[0], metrics.items(), strict=True):
        values = np.asarray(raw, dtype=float)
        if values.shape != rhos.shape:
            raise ValueError("rho sweep metrics must match the rho vector")
        axis.plot(
            rhos[order],
            values[order],
            color=Y_COLOR,
            marker="o",
            label=labeling.legend_label(source),
        )
        target = 1 if metric in {"R", "z_std"} else (0 if metric in {"MB", "z_mean"} else None)
        if target is not None:
            axis.axhline(target, color=X_COLOR, linestyle="--", linewidth=1)
        axis.set_xlabel(labeling.axis_label("Error Correlation (ρ)", "1"))
        axis.set_ylabel(labeling.metric_label(metric))
        axis.legend(fontsize=8)
    return _finish(
        figure,
        list(axes[0]),
        kind="rho_sweep",
        title=labeling.title_text("AOD"),
        start=start,
        end=end,
        rho_values=rhos,
        sweep_metrics=metrics,
    )


def scorecard(table: pd.DataFrame, *, start: Any = None, end: Any = None) -> Figure:
    """One scale per metric preserves the units and direction of each column."""
    if table.empty or not table.columns.size:
        raise ValueError("scorecard requires at least one region and metric")
    figure, axes = _new_figure(
        figsize=(max(6, 1.8 * len(table.columns)), max(4.5, 0.42 * len(table.index) + 2)),
        ncols=len(table.columns),
        squeeze=False,
    )
    dense, colorbars, fields = [], [], []
    for index, (axis, metric) in enumerate(zip(axes[0], table.columns, strict=True)):
        values = table[metric].to_numpy(dtype=float)
        finite = values[np.isfinite(values)]
        norm: Normalize
        if metric in {"MB", "z_mean"}:
            low, high = calculate_symmetric_limits(values)
            norm, cmap = TwoSlopeNorm(vmin=low, vcenter=0, vmax=high), "RdBu_r"
        else:
            maximum = (
                1
                if metric == "R"
                else (
                    100
                    if metric == "pct_ee"
                    else max(float(np.percentile(finite, 98)), 1e-6) if finite.size else 1
                )
            )
            norm, cmap = Normalize(0, maximum), "viridis"
        artist = axis.imshow(values[:, None], aspect="auto", cmap=cmap, norm=norm, rasterized=True)
        dense.append(artist)
        if metric in {"MB", "z_mean"}:
            fields.append({"artist": artist, "kind": "bias"})
        elif metric in {"R", "RMSE", "N"}:
            fields.append(
                {
                    "artist": artist,
                    "kind": {"R": "correlation", "RMSE": "rmse", "N": "count"}[metric],
                }
            )
        axis.set_xticks([0], [labeling.metric_label(str(metric))])
        axis.set_yticks(
            np.arange(len(table.index)),
            (
                [labeling.region_label(str(region)) for region in table.index]
                if index == 0
                else [""] * len(table.index)
            ),
        )
        axis.grid(False)
        for row, value in enumerate(values):
            rgb = np.asarray(artist.cmap(norm(value))[:3])
            linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
            luminance = float(linear @ np.array([0.2126, 0.7152, 0.0722]))
            axis.text(
                0,
                row,
                labeling.number_label(
                    value, count=metric == "N", decimals=1 if metric == "pct_ee" else 3
                ),
                ha="center",
                va="center",
                fontsize=9,
                color="white" if np.isfinite(value) and luminance < 0.179 else "black",
            )
        colorbar = figure.colorbar(
            artist, ax=axis, orientation="horizontal", fraction=0.08, pad=0.08
        )
        if colorbar.solids is not None:
            colorbar.solids.set_rasterized(False)
        colorbars.append(colorbar)
    return _finish(
        figure,
        list(axes[0]),
        kind="scorecard",
        title=labeling.title_text("AOD"),
        start=start,
        end=end,
        dense=dense,
        colorbars=colorbars,
        field_artists=fields,
        scorecard_values=table,
    )


def taylor_diagram(
    reference: ArrayLike,
    products: Mapping[str, ArrayLike],
    *,
    start: Any = None,
    end: Any = None,
) -> Figure:
    """Normalized standard deviation, correlation, and centered RMS difference."""
    if not products:
        raise ValueError("Taylor diagram requires at least one product")
    stats = {}
    for name, values in products.items():
        left, right = paired_values(reference, values)
        result = annotation_metrics(left, right, ["N", "R", "STDX", "STDY"])
        if np.isfinite(result["R"]) and result["STDX"] > 0 and result["STDY"] > 0:
            stats[name] = result
    figure, axis = _new_figure(figsize=(7.5, 6), subplot_kw={"projection": "polar"})
    negative = any(value["R"] < 0 for value in stats.values())
    theta_max = np.pi if negative else np.pi / 2
    axis.set_thetamin(0)
    axis.set_thetamax(np.degrees(theta_max))
    maximum = max([1.5, *[1.2 * result["STDY"] / result["STDX"] for result in stats.values()]])
    axis.set_ylim(0, maximum)
    ticks = np.array([0, 0.2, 0.4, 0.6, 0.8, 0.9, 0.95, 0.99, 1])
    if negative:
        ticks = np.array([-1, -0.95, -0.8, -0.4, 0, 0.4, 0.8, 0.95, 1])
    axis.set_thetagrids(
        np.degrees(np.arccos(ticks)), [labeling.number_label(value, decimals=2) for value in ticks]
    )
    theta, radius = np.meshgrid(np.linspace(0, theta_max, 181), np.linspace(0, maximum, 121))
    rms = np.sqrt(np.maximum(0, 1 + radius**2 - 2 * radius * np.cos(theta)))
    contours = axis.contour(
        theta,
        radius,
        rms,
        levels=[0.25, 0.5, 0.75, 1, 1.5, 2],
        colors=X_COLOR,
        linewidths=0.5,
        linestyles=":",
    )
    contours.set_rasterized(False)
    axis.clabel(
        contours,
        inline=True,
        fontsize=7,
        fmt=lambda value: labeling.number_label(value, decimals=2),
    )
    axis.plot(
        0,
        1,
        marker="*",
        color=X_COLOR,
        markersize=12,
        linestyle="none",
        clip_on=False,
        label=labeling.legend_label("aeronet"),
    )
    for index, (name, result) in enumerate(stats.items()):
        axis.plot(
            np.arccos(np.clip(result["R"], -1, 1)),
            result["STDY"] / result["STDX"],
            marker="o",
            color=NCAR_PALETTE[index % len(NCAR_PALETTE)],
            linestyle="none",
            label=labeling.legend_label(name),
        )
    axis.set_xlabel(labeling.axis_label("Normalized Standard Deviation", "1"), labelpad=25)
    axis.set_ylabel(labeling.axis_label("Correlation", "1"), labelpad=35)
    axis.legend(loc="upper right", bbox_to_anchor=(1.28, 1.0), fontsize=8)
    return _finish(
        figure,
        [axis],
        kind="taylor",
        title=labeling.title_text("AOD"),
        start=start,
        end=end,
        metrics=stats,
        line_contours=[contours],
    )


def regional_boxplots(
    regions: Mapping[str, Mapping[str, ArrayLike]],
    *,
    start: Any = None,
    end: Any = None,
) -> Figure:
    if not regions:
        raise ValueError("regional boxplots require at least one region")
    sources = list(dict.fromkeys(source for values in regions.values() for source in values))
    if "aeronet" in sources:
        sources = ["aeronet", *[source for source in sources if source != "aeronet"]]
    if not sources:
        raise ValueError("regional boxplots require at least one source")
    labels = [labeling.region_label(region) for region in regions]
    crowded = len(regions) > 5
    # Long geographic names need both horizontal spacing and room below the
    # axes; rotating alone can still leave overlapping rendered text bounds.
    figure, axis = _new_figure(
        figsize=(max(8, (1.4 if crowded else 1.2) * len(regions)), 6.5 if crowded else 5.5)
    )
    width = 0.8 / len(sources)
    dense, handles = [], []
    for index, source in enumerate(sources):
        data = []
        for values in regions.values():
            array = np.asarray(values.get(source, []), dtype=float).ravel()
            data.append(array[np.isfinite(array)])
        color = (
            X_COLOR
            if source == "aeronet"
            else NCAR_PALETTE[(index - int("aeronet" in sources)) % len(NCAR_PALETTE)]
        )
        positions = np.arange(len(regions)) + (index - (len(sources) - 1) / 2) * width
        boxes = axis.boxplot(
            data, positions=positions, widths=width * 0.85, patch_artist=True, manage_ticks=False
        )
        for patch in boxes["boxes"]:
            patch.set_facecolor(color)
            patch.set_alpha(0.6)
        for median in boxes["medians"]:
            median.set_color("black")
        for flier in boxes["fliers"]:
            flier.set_rasterized(True)
            dense.append(flier)
        handles.append(Patch(facecolor=color, alpha=0.6, label=labeling.legend_label(source)))
    axis.set_xticks(
        np.arange(len(regions)),
        labels,
        rotation=60 if crowded else 35,
        ha="right",
    )
    axis.set_ylabel(labeling.axis_label("AOD", "1"))
    axis.legend(handles=handles, loc="upper left")
    return _finish(
        figure,
        [axis],
        kind="regional_boxplots",
        title=labeling.title_text("AOD"),
        start=start,
        end=end,
        dense=dense,
    )
