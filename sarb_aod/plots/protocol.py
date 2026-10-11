"""Publication plot checks adapted from NCAR/DAVINCI's AOD science protocol.

Source: origin/develop plots/renderers/aod_correction_science.py (Apache-2.0),
``validate_rendered_figures`` and ``_layout_failures``. This version also checks
axes annotations, cartopy grid labels, colorbars, and unregistered dense layers.
Validation draws the canvas and runs before delivery files are saved.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from matplotlib.artist import Artist
from matplotlib.collections import PathCollection, PolyCollection, QuadMesh
from matplotlib.colors import BoundaryNorm, TwoSlopeNorm
from matplotlib.container import BarContainer
from matplotlib.contour import ContourSet
from matplotlib.figure import Figure
from matplotlib.image import AxesImage
from matplotlib.text import Text
from matplotlib.transforms import Bbox

from sarb_aod.errors import SarbAodError
from sarb_aod.plots.labeling import title_violations
from sarb_aod.plots.style import aod_colorbar_ticks, geosit_aod_levels

PROTOCOL = "sarb_aod.publication.v1"


class PlotProtocolError(SarbAodError):
    def __init__(self, report: dict[str, Any]):
        self.report = report
        super().__init__("Plot protocol failed: " + "; ".join(report["failures"]))


def _unique(artists: Sequence[Any]) -> list[Any]:
    return list({id(artist): artist for artist in artists}.values())


def _visible(artist: Any) -> bool:
    return bool(
        artist.get_visible() and (not isinstance(artist, Text) or artist.get_text().strip())
    )


def _overlap(left: Bbox, right: Bbox, *, tolerance: float = 0.5) -> bool:
    return bool(
        min(left.x1, right.x1) - max(left.x0, right.x0) > tolerance
        and min(left.y1, right.y1) - max(left.y0, right.y0) > tolerance
    )


def _layout_failures(figure: Figure, metadata: dict[str, Any]) -> list[str]:
    """Check visible rendered text and legend boxes in display pixels."""
    figure.canvas.draw()
    renderer = figure.canvas.get_renderer()  # type: ignore[attr-defined]
    items: list[tuple[str, Any]] = [("figure text", text) for text in figure.texts]
    for index, axis in enumerate(figure.axes):
        items.extend(
            (f"axis {index} {role}", artist)
            for role, artist in (
                ("title", axis.title),
                ("left title", getattr(axis, "_left_title")),
                ("right title", getattr(axis, "_right_title")),
                ("x label", axis.xaxis.label),
                ("y label", axis.yaxis.label),
            )
        )
        items.extend((f"axis {index} annotation", text) for text in axis.texts)
        legend = axis.get_legend()
        if legend is not None:
            items.append((f"axis {index} legend", legend))
        for direction, coordinate in (("x", axis.xaxis), ("y", axis.yaxis)):
            lower, upper = sorted(coordinate.get_view_interval())
            for tick in [*coordinate.get_major_ticks(), *coordinate.get_minor_ticks()]:
                if lower - 1e-10 <= tick.get_loc() <= upper + 1e-10:
                    items.extend(
                        (f"axis {index} {direction} tick", text)
                        for text in (tick.label1, tick.label2)
                    )
            items.append((f"axis {index} {direction} offset", coordinate.get_offset_text()))
    items.extend(("figure legend", legend) for legend in figure.legends)
    for gridliner in metadata.get("gridliners", []):
        items.extend(("map grid label", text) for text in gridliner.label_artists)
    seen: set[int] = set()
    boxes: list[tuple[str, Bbox]] = []
    failures = []
    for role, artist in items:
        if id(artist) in seen or not _visible(artist):
            continue
        seen.add(id(artist))
        box = artist.get_window_extent(renderer)
        if not np.isfinite(box.extents).all():
            failures.append(f"{role} has nonfinite text position")
            continue
        if box.width <= 0 or box.height <= 0:
            continue
        description = f"{role} {artist.get_text()!r}" if isinstance(artist, Text) else role
        boxes.append((description, box))
        if (
            box.x0 < figure.bbox.x0 - 1
            or box.y0 < figure.bbox.y0 - 1
            or box.x1 > figure.bbox.x1 + 1
            or box.y1 > figure.bbox.y1 + 1
        ):
            failures.append(f"{description} is clipped by the figure")
    for index, (left_role, left) in enumerate(boxes):
        for right_role, right in boxes[index + 1 :]:
            if _overlap(left, right):
                failures.append(f"{left_role} overlaps {right_role}")
    return failures


def _dense_and_vector_failures(figure: Figure, metadata: dict[str, Any]) -> list[str]:
    failures = []
    colorbar_axes = {id(colorbar.ax) for colorbar in metadata.get("colorbars", [])}
    registered = list(metadata.get("dense_artists", []))
    for prefix in ("_sarb", "_davinci"):
        registered.extend(getattr(figure, f"{prefix}_dense_artists", []))
    discovered: list[Artist] = []
    for axis in figure.axes:
        if axis.get_rasterized() or axis.xaxis.get_rasterized() or axis.yaxis.get_rasterized():
            failures.append("axes must remain vector")
        if any(spine.get_rasterized() for spine in axis.spines.values()):
            failures.append("axes must remain vector")
        if id(axis) in colorbar_axes:
            continue
        for artist in axis.get_children():
            if isinstance(artist, ContourSet):
                if artist.filled:
                    discovered.append(artist)
                elif artist.get_rasterized():
                    failures.append("line ContourSet must remain vector")
            elif isinstance(artist, (QuadMesh, PolyCollection, PathCollection, AxesImage)):
                discovered.append(artist)
        for container in axis.containers:
            if isinstance(container, BarContainer):
                discovered.extend(container.patches)
    for artist in _unique([*registered, *discovered]):
        if artist.get_rasterized() is not True:
            failures.append("dense artist is not rasterized")
    for text in figure.findobj(match=Text):
        if _visible(text) and text.get_rasterized():
            failures.append("text must remain vector")
    for colorbar in metadata.get("colorbars", []):
        if colorbar.solids is not None and colorbar.solids.get_rasterized():
            failures.append("colorbar must remain vector")
    return list(dict.fromkeys(failures))


def _scale_failures(figure: Figure, metadata: dict[str, Any]) -> list[str]:
    failures = []
    absolute = list(metadata.get("absolute_aod_artists", []))
    for prefix in ("_sarb", "_davinci"):
        absolute.extend(getattr(figure, f"{prefix}_absolute_aod_artists", []))
    fields = list(metadata.get("field_artists", []))
    absolute.extend(item["artist"] for item in fields if item["kind"] == "aod")
    absolute = _unique(absolute)
    boundaries = []
    for artist in absolute:
        norm = artist.norm
        if not isinstance(norm, BoundaryNorm):
            failures.append("absolute AOD requires BoundaryNorm")
            continue
        levels = np.asarray(norm.boundaries, dtype=float)
        boundaries.append(levels)
        if artist.cmap.name != "turbo":
            failures.append("absolute AOD requires turbo")
        if norm.extend != "max":
            failures.append("absolute AOD BoundaryNorm requires extend=max")
        if (
            levels.size < 2
            or not np.isfinite(levels).all()
            or levels[0] != 0
            or levels[-1] > 1
            or np.any(np.diff(levels) <= 0)
        ):
            failures.append("absolute AOD levels must increase from zero through at most one")
            continue
        # A generated endpoint such as 0.30000000000000004 must not round
        # upward to the next interval when checking an existing scale.
        template = geosit_aod_levels([1.0])
        expected = template[template <= levels[-1] + 1e-12]
        values: np.ndarray = np.asarray(
            np.ma.asarray(artist.get_array(), dtype=float).filled(np.nan)
        )
        no_positive = not np.any(np.isfinite(values) & (values > 0))
        fallback = np.linspace(0, 1, 11)
        exact = levels.shape == expected.shape and np.allclose(levels, expected, rtol=0, atol=1e-12)
        empty_fallback = (
            no_positive and levels.shape == fallback.shape and np.array_equal(levels, fallback)
        )
        if not exact and not empty_fallback:
            failures.append("absolute AOD does not use GEOSIT levels")
    if boundaries and any(not np.array_equal(levels, boundaries[0]) for levels in boundaries[1:]):
        failures.append("absolute AOD panels do not share one scale")
    for item in fields:
        artist, kind = item["artist"], item["kind"]
        norm, cmap = artist.norm, artist.cmap.name
        if kind in {"bias", "difference"}:
            if (
                not isinstance(norm, TwoSlopeNorm)
                or norm.vcenter != 0
                or norm.vmin is None
                or norm.vmax is None
                or not np.isclose(norm.vmin, -norm.vmax)
            ):
                failures.append("signed fields require symmetric TwoSlopeNorm centered on zero")
            if cmap != "RdBu_r":
                failures.append("signed fields require RdBu_r")
        elif kind in {"fraction", "correlation"}:
            if norm.vmin != 0 or norm.vmax != 1:
                failures.append(f"{kind} requires fixed [0, 1] limits")
            if cmap != ("gray_r" if kind == "fraction" else "viridis"):
                failures.append(f"{kind} uses the wrong colormap")
        elif kind in {"count", "uncertainty", "chi2", "rmse"}:
            if norm.vmin != 0 or norm.vmax is None or norm.vmax <= 0 or cmap != "viridis":
                failures.append(f"{kind} requires viridis with nonnegative limits")
    absolute_ids = {id(artist) for artist in absolute}
    for colorbar in metadata.get("colorbars", []):
        if id(colorbar.mappable) not in absolute_ids:
            continue
        if colorbar.extend != "max":
            failures.append("AOD colorbar requires extend=max")
        if colorbar.ax.get_xlabel() or colorbar.ax.get_ylabel():
            failures.append("dimensionless AOD colorbar must have no label")
        if isinstance(colorbar.mappable.norm, BoundaryNorm):
            ticks = aod_colorbar_ticks(colorbar.mappable.norm.boundaries)
            actual = np.asarray(colorbar.get_ticks())
            if actual.shape != ticks.shape or not np.allclose(actual, ticks):
                failures.append("AOD colorbar must use sparse GEOSIT ticks")
    return list(dict.fromkeys(failures))


def _text_failures(figure: Figure, metadata: dict[str, Any]) -> list[str]:
    failures = []
    titles = list(metadata.get("titles", []))
    if getattr(figure, "_suptitle", None) is not None:
        titles.append(getattr(figure, "_suptitle"))
    for axis in figure.axes:
        titles.extend((axis.title, getattr(axis, "_left_title"), getattr(axis, "_right_title")))
    visible_titles = [title for title in _unique(titles) if _visible(title)]
    if not visible_titles:
        failures.append("figure requires a visible title")
    for title in visible_titles:
        failures.extend(f"title contains {reason}" for reason in title_violations(title.get_text()))
    raw = re.compile(
        r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b|\b(?:noaa20|modis|viirs|aeronet|snpp)\b"
    )
    # Literal paths/revisions belong in the explicitly registered provenance
    # panel. They still undergo vector, clipping and overlap checks.
    provenance_text = {id(text) for text in metadata.get("provenance_text", [])}
    for text in figure.findobj(match=Text):
        if not _visible(text):
            continue
        if id(text) in provenance_text:
            continue
        value = text.get_text()
        if raw.search(value):
            failures.append(f"text exposes a raw key: {value!r}")
        if re.search(r"\b[xy]\b", value, re.IGNORECASE):
            failures.append(f"text exposes pairing-axis placeholders: {value!r}")
    for axis in metadata.get("date_axes", []):
        rotation = 20 if metadata.get("kind") == "regional_time_series" else 45
        if any(
            not np.isclose(tick.get_rotation(), rotation)
            for tick in axis.get_xticklabels()
            if _visible(tick)
        ):
            failures.append(f"date tick labels must rotate {rotation} degrees")
    return list(dict.fromkeys(failures))


def validate_rendered_figures(
    figures: Mapping[str, Figure] | Sequence[tuple[str | None, Figure]],
    *,
    report_path: str | Path | None = None,
    expected_figures: Sequence[str] | None = None,
    raise_on_failure: bool = True,
) -> dict[str, Any]:
    """Draw, validate, optionally write JSON, and fail before publication on errors."""
    pairs = list(figures.items()) if isinstance(figures, Mapping) else list(figures)
    names = [
        str(name) if name is not None else f"figure_{index + 1}"
        for index, (name, _) in enumerate(pairs)
    ]
    failures = []
    checks: dict[str, Any] = {}
    if not pairs:
        failures.append("suite contains no figures")
    if len(set(names)) != len(names):
        failures.append("suite figure labels must be unique")
    if expected_figures is not None and names != list(expected_figures):
        failures.append(f"suite figures {names!r} differ from expected {list(expected_figures)!r}")
    for name, (_, figure) in zip(names, pairs, strict=True):
        figure.canvas.draw()
        metadata = getattr(figure, "_sarb_plot_metadata", {})
        groups = {
            "metadata": [] if metadata else ["renderer did not register plot metadata"],
            "rasterization": _dense_and_vector_failures(figure, metadata),
            "field_scales": _scale_failures(figure, metadata),
            "text": _text_failures(figure, metadata),
            "layout": _layout_failures(figure, metadata),
        }
        figure_failures = [
            f"{rule}: {failure}" for rule, messages in groups.items() for failure in messages
        ]
        checks[name] = {
            "passed": not figure_failures,
            "checks": {rule: not messages for rule, messages in groups.items()},
            "failures": figure_failures,
        }
        failures.extend(f"{name}: {failure}" for failure in figure_failures)
    report = {
        "protocol": PROTOCOL,
        "passed": not failures,
        "figures": names,
        "checks": checks,
        "failures": failures,
    }
    if report_path is not None:
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    if failures and raise_on_failure:
        raise PlotProtocolError(report)
    return report
