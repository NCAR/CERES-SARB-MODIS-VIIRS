"""Geographic context beneath regional time-series curves and site-spread bands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cartopy.crs as ccrs
import numpy as np
import pandas as pd
from matplotlib.collections import PathCollection, PolyCollection
from matplotlib.figure import Figure
from matplotlib.path import Path as MplPath
from matplotlib.transforms import Bbox

from sarb_aod.evaluation.regions import Region, get_region, region_mask
from sarb_aod.plots.maps import _map_context, _projection
from sarb_aod.plots.style import X_COLOR, configure_cartopy


def _segment_overlap(path: MplPath, box: Bbox) -> float:
    """Length of straight path segments inside a box, preserving NaN gaps."""
    vertices = np.asarray(path.vertices, dtype=float)
    start, end = vertices[:-1], vertices[1:]
    valid = np.isfinite(start).all(axis=1) & np.isfinite(end).all(axis=1)
    if path.codes is not None:
        valid &= np.asarray(path.codes)[1:] == MplPath.LINETO
    start, end = start[valid], end[valid]
    delta = end - start
    lower, upper = np.zeros(len(start)), np.ones(len(start))
    for dimension, minimum, maximum in ((0, box.x0, box.x1), (1, box.y0, box.y1)):
        moving = delta[:, dimension] != 0
        outside = (start[:, dimension] < minimum) | (start[:, dimension] > maximum)
        upper[~moving & outside] = -1
        near = np.zeros(len(start))
        far = np.ones(len(start))
        near[moving] = (minimum - start[moving, dimension]) / delta[moving, dimension]
        far[moving] = (maximum - start[moving, dimension]) / delta[moving, dimension]
        lower = np.maximum(lower, np.minimum(near, far))
        upper = np.minimum(upper, np.maximum(near, far))
    return float(np.sum(np.maximum(upper - lower, 0) * np.linalg.norm(delta, axis=1)))


def _choose_position(
    axis: Any, map_ratio: float
) -> tuple[tuple[float, float, float, float], str, dict[str, dict[str, Any]]]:
    """Score six aspect-fitted edge positions after layout without a figure draw."""
    figure = axis.figure
    engine = figure.get_layout_engine()
    if engine is not None:
        engine.execute(figure)
    renderer = figure.canvas.get_renderer()
    parent = axis.get_window_extent(renderer)
    width, height, pad = 0.34, 0.47, 0.02
    transform = axis.transAxes.inverted()
    text_boxes = []
    time_box = None
    legend = axis.get_legend()
    for artist in [legend, axis.xaxis.label, axis.yaxis.label, *axis.texts]:
        if artist is not None and artist.get_visible():
            box = artist.get_window_extent(renderer).transformed(transform)
            if box.width > 0 and box.height > 0:
                text_boxes.append(box)
                if artist is axis.xaxis.label:
                    time_box = box

    curves: list[MplPath] = []
    marker_points: list[np.ndarray] = []
    bands: list[MplPath] = []
    for line in axis.lines:
        if not line.get_visible():
            continue
        path = (line.get_transform() - axis.transAxes).transform_path(line.get_path())
        if line.get_linestyle() not in ("None", "none", "", " "):
            curves.append(path)
        if line.get_marker() not in ("None", "none", "", " ", None):
            marker_points.append(np.asarray(path.vertices))
    for collection in axis.collections:
        if not collection.get_visible():
            continue
        # Filled site-spread polygons contain areas even when all their vertices
        # lie outside a candidate; sample their interiors, not only the vertices.
        if isinstance(collection, PolyCollection):
            affine = collection.get_transform() - axis.transAxes
            bands.extend(affine.transform_path(path) for path in collection.get_paths())
        elif isinstance(collection, PathCollection):
            affine = collection.get_offset_transform() - axis.transAxes
            marker_points.append(affine.transform(collection.get_offsets()))

    scores: dict[str, dict[str, Any]] = {}
    candidates = {}
    # Keep the existing corner tie order, then consider the two centered maps.
    for position in (
        "lower right",
        "upper right",
        "lower left",
        "upper left",
        "upper center",
        "lower center",
    ):
        right, upper, centered = "right" in position, "upper" in position, "center" in position
        bottom = pad
        if position == "lower center" and time_box is not None:
            # A centered inset would otherwise always lose to the Time label.
            # Reserve its rendered height and a small gap above the label.
            bottom = max(bottom, time_box.y1 + pad)
        available_height = min(height, 1 - pad - bottom)
        if available_height <= 0:
            continue
        fitted_width = min(width, available_height * parent.height * map_ratio / parent.width)
        fitted_height = fitted_width * parent.width / (map_ratio * parent.height)
        x = (1 - fitted_width) / 2 if centered else 1 - pad - fitted_width if right else pad
        y = 1 - pad - fitted_height if upper else bottom
        box = Bbox.from_bounds(x, y, fitted_width, fitted_height)
        samples = np.linspace(0, 1, 64, endpoint=False) + 0.5 / 64
        xx, yy = np.meshgrid(x + samples * box.width, y + samples * box.height)
        points = np.column_stack([xx.ravel(), yy.ravel()])
        band_overlap = sum(float(path.contains_points(points).mean()) for path in bands)
        curve_overlap = sum(_segment_overlap(path, box) for path in curves)
        marker_overlap = sum(
            int(
                np.count_nonzero(
                    (points[:, 0] >= box.x0)
                    & (points[:, 0] <= box.x1)
                    & (points[:, 1] >= box.y0)
                    & (points[:, 1] <= box.y1)
                )
            )
            for points in marker_points
        )
        text_overlap = 0.0
        for text_box in text_boxes:
            overlap = Bbox.intersection(box, text_box)
            if overlap is not None:
                text_overlap += overlap.width * overlap.height / (text_box.width * text_box.height)
        # Both line length and marker count matter: a line can cross the map
        # without any daily markers inside it. Curves outrank translucent bands.
        score = band_overlap + 3 * curve_overlap + 0.12 * marker_overlap
        scores[position] = {
            "bounds": tuple(box.bounds),
            "band_overlap": band_overlap,
            "curve_overlap": curve_overlap,
            "marker_overlap": marker_overlap,
            "text_overlap": text_overlap,
            "score": score,
        }
        candidates[position] = (
            (1 - width) / 2 if centered else 1 - pad - width if right else pad,
            1 - pad - available_height if upper else bottom,
            width,
            available_height,
        )
    position = min(
        scores,
        key=lambda name: (
            round(scores[name]["text_overlap"], 12) > 0,
            round(scores[name]["text_overlap"], 12),
            round(scores[name]["score"], 12),
        ),
    )
    return candidates[position], position, scores


def add_regional_inset(
    figure: Figure,
    pairs: pd.DataFrame,
    region: str | Region,
    *,
    cartopy_data_dir: str | Path | None = None,
    x_var: str = "x",
    y_var: str = "y",
    bounds: tuple[float, float, float, float] | None = None,
) -> Any:
    """Show the region and finite paired AERONET sites in a transparent inset.

    ``pairs`` must describe the plotted period. Its finite paired site rows are
    filtered by region geography and activity dates, then reduced to one marker
    per station. The child axes draws below the parent's bands and curves; this
    ordering applies to the whole map, including its site markers and outline.
    The default chooses the least occupied of four corners and the upper/lower
    center positions, fully inside the plot box and clear of the Time label.
    Explicit ``bounds`` retain the caller's position with a lower-right anchor.
    """
    metadata = getattr(figure, "_sarb_plot_metadata")
    axis = metadata["data_axes"][0]
    subset = get_region(region)
    if cartopy_data_dir is not None:
        configure_cartopy(cartopy_data_dir)

    time = pd.to_datetime(pairs["time"], utc=True, errors="coerce")
    finite = np.isfinite(pairs[[x_var, y_var]].to_numpy(dtype=float)).all(axis=1)
    selected = finite & time.notna() & pairs["site"].notna()
    selected &= region_mask(subset, pairs["longitude"], pairs["latitude"], times=time.to_numpy())
    sites = pairs.loc[selected, ["site", "longitude", "latitude"]].drop_duplicates("site")

    west, east, south, north = subset.extent
    # Native PlateCarree coordinates keep a dateline-crossing rectangle compact.
    # The existing map projection centers such regions on 180 degrees.
    center = 180 if west > east else 0
    left, right = west - center, east + (360 if west > east else 0) - center
    # The region outline is also the map boundary: no surrounding geography.
    xlim = left, right
    ylim = south, north
    position = "custom"
    scores: dict[str, dict[str, Any]] = {}
    if bounds is None:
        bounds, position, scores = _choose_position(axis, (xlim[1] - xlim[0]) / (ylim[1] - ylim[0]))
    projection = _projection(subset)
    inset = axis.inset_axes(bounds, projection=projection, zorder=0)
    inset.set_in_layout(False)
    inset.set_anchor(
        {
            "lower right": "SE",
            "upper right": "NE",
            "lower left": "SW",
            "upper left": "NW",
            "upper center": "N",
            "lower center": "S",
        }.get(position, "SE")
    )
    inset.patch.set_alpha(0)
    coastline, gridliner = _map_context(inset, subset, "110m")
    coastline.set_edgecolor("0.45")
    coastline.set_linewidth(0.45)
    gridliner.left_labels = False
    gridliner.bottom_labels = False
    gridliner.collection_kwargs.update(color="0.65", linewidth=0.3, alpha=0.3)
    inset.spines["geo"].set_visible(False)

    inset.set_xlim(xlim)
    inset.set_ylim(ylim)
    (outline,) = inset.plot(
        [left, right, right, left, left],
        [south, south, north, north, south],
        transform=projection,
        color="0.5",
        linewidth=0.7,
        zorder=5,
    )
    markers = inset.scatter(
        sites["longitude"],
        sites["latitude"],
        transform=ccrs.PlateCarree(),
        s=10,
        color=X_COLOR,
        edgecolors="none",
        alpha=0.85,
        rasterized=True,
        zorder=6,
    )
    metadata["dense_artists"].append(markers)
    metadata["gridliners"].append(gridliner)
    metadata["regional_inset"] = {
        "axis": inset,
        "sites": sites.reset_index(drop=True),
        "region": subset,
        "outline": outline,
        "coastline": coastline,
        "markers": markers,
        "position": position,
        "corner": position,  # Compatibility with existing inspection inventories.
        "placement_scores": scores,
    }
    return inset
