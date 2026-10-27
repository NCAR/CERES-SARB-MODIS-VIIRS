"""Regional daily comparison with provenance, geographic context and paired means.

The optional context field follows NCAR/DAVINCI's AOD map practice (Apache-2.0):
GEOS-IT levels on turbo, with AERONET site means drawn on the same scale.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import cartopy.crs as ccrs
import numpy as np
import pandas as pd
from cartopy.io import shapereader
from matplotlib import patheffects
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.transforms import Bbox, ScaledTranslation

from sarb_aod.config.schema import RunConfig
from sarb_aod.contract import REPOSITORY_URL, git_commit
from sarb_aod.evaluation.regions import Region, get_region, spatial_mask
from sarb_aod.grid.spec import GridSpec
from sarb_aod.paths import cartopy_data_dir
from sarb_aod.plots import labeling
from sarb_aod.plots.maps import (
    _colorbar,
    _map_context,
    _projection,
    _quantity,
    _title,
    area_mean,
    draw_spatial_field,
    field_scale,
)
from sarb_aod.plots.style import NCAR_ACCENT, Y_COLOR, configure_cartopy


@dataclass(frozen=True)
class RegionalField:
    """Count-weighted satellite AOD over the region's active configured days."""

    grid: GridSpec
    values: np.ndarray
    windows: int
    provenance: Mapping[str, list[str]]
    start: date
    end: date


# A white line with a black edge stays legible over every turbo color.
_OUTLINE_EFFECTS = [patheffects.Stroke(linewidth=3.2, foreground="black"), patheffects.Normal()]


def context_extent(region: Region, padding: float) -> tuple[float, float, float, float]:
    """Padded native PlateCarree limits, including compact dateline regions."""
    west, east, south, north = region.extent
    center = 180 if west > east else 0
    left, right = west - center, east + (360 if west > east else 0) - center
    lon_pad = max(5, (right - left) * padding)
    lat_pad = max(5, (north - south) * padding)
    left, right = max(-180, left - lon_pad), min(180, right + lon_pad)
    # Broad latitude bands also need enough surrounding geography to read.
    span = min(180, max(north - south + 2 * lat_pad, (right - left) / 1.8))
    lower = max(-90, min((south + north - span) / 2, 90 - span))
    return left, right, lower, lower + span


def _cities(directory: Path) -> tuple[pd.DataFrame, dict[str, str]]:
    """Read an explicitly prefetched Natural Earth city layer; never download."""
    path = directory / "shapefiles/natural_earth/cultural/ne_110m_populated_places.shp"
    if not path.is_file():
        raise FileNotFoundError(
            f"Missing city map asset: {path}; explicitly prefetch Natural Earth's "
            "110m cultural populated_places layer with sarb-aod plot --prefetch-maps"
        )
    reader = shapereader.Reader(path)
    try:
        rows = [
            {
                "name": str(record.attributes["NAME"]),
                "longitude": float(record.geometry.x),
                "latitude": float(record.geometry.y),
                "population": float(record.attributes["POP_MAX"]),
            }
            for record in reader.records()
        ]
    finally:
        reader.close()
    # Names/populations live in DBF; identify all three necessary components.
    checksums = {
        path.with_suffix(suffix)
        .name: hashlib.sha256(path.with_suffix(suffix).read_bytes())
        .hexdigest()
        for suffix in (".shp", ".shx", ".dbf")
    }
    return pd.DataFrame(rows), checksums


def _label_cities(axis: Any, candidates: pd.DataFrame, limit: int) -> pd.DataFrame:
    """Place labels inside the map without colliding with earlier city labels."""
    figure = axis.figure
    figure.canvas.draw()
    renderer = figure.canvas.get_renderer()
    bounds = axis.get_window_extent(renderer).padded(-4)
    occupied: list[Bbox] = []
    chosen = []
    for row in candidates.itertuples():
        text = axis.annotate(
            labeling.city_label(row.name),
            (row.longitude, row.latitude),
            xycoords=ccrs.PlateCarree()._as_mpl_transform(axis),
            xytext=(5, 4),
            textcoords="offset points",
            fontsize=8,
            color="black",
            zorder=8,
            clip_on=True,
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.8, "pad": 0.2},
        )
        text.set_in_layout(False)
        placed = False
        for dx, dy, ha, va in (
            (5, 4, "left", "bottom"),
            (-5, 4, "right", "bottom"),
            (5, -4, "left", "top"),
            (-5, -4, "right", "top"),
            (0, 6, "center", "bottom"),
            (0, -6, "center", "top"),
        ):
            text.set_position((dx, dy))
            text.set(ha=ha, va=va)
            box = text.get_window_extent(renderer).padded(2)
            if (
                bounds.contains(box.x0, box.y0)
                and bounds.contains(box.x1, box.y1)
                and not any(box.overlaps(other) for other in occupied)
            ):
                occupied.append(box)
                placed = True
                break
        if placed:
            chosen.append(row.Index)
            if len(chosen) == limit:
                break
        else:
            text.remove()
    return candidates.loc[chosen].reset_index(drop=True)


def _colorbar_locator(parent: Any, *, bins: int) -> Any:
    """Figure-fraction bounds 0.33 in below the drawn map, within its grid cell."""

    def locate(axis: Any, renderer: Any) -> Bbox:
        box = parent.get_position()
        cell = parent.get_subplotspec().get_position(parent.figure)
        width_in, height_in = parent.figure.get_size_inches()
        width = min(max(box.width, 0.24 * bins / width_in), cell.width)
        left = min(max((box.x0 + box.x1 - width) / 2, cell.x0), cell.x1 - width)
        height = 0.11 / height_in
        top = box.y0 - 0.33 / height_in
        return Bbox.from_bounds(left, top - height, width, height)

    return locate


def _draw_field(
    figure: Figure,
    axis: Any,
    config: RunConfig,
    field: RegionalField,
    sites: pd.DataFrame | None,
    region: Region,
    extent: tuple[float, float, float, float],
    source: str,
) -> tuple[Any, Any]:
    """Satellite field and any AERONET site means on one GEOS-IT scale, below the outline."""
    metadata = getattr(figure, "_sarb_plot_metadata")
    grid = field.grid
    center = 180 if region.extent[0] > region.extent[1] else 0
    native = (grid.lon - center + 180) % 360 - 180
    visible = field.values[
        np.ix_(
            (grid.lat >= extent[2]) & (grid.lat <= extent[3]),
            (native >= extent[0]) & (native <= extent[1]),
        )
    ]
    site_values = sites["aeronet"].to_numpy() if sites is not None else np.empty(0)
    scale = field_scale(np.concatenate([visible.ravel(), site_values]))
    mesh = draw_spatial_field(
        axis,
        field.values,
        grid.lat,
        grid.lon,
        plot_type=config.plots.aod_fill,
        scale=scale,
        lat_bnds=grid.lat_bnds,
        lon_bnds=grid.lon_bnds,
    )
    markers = None
    if sites is not None:
        markers = axis.scatter(
            sites.longitude,
            sites.latitude,
            c=sites.aeronet,
            cmap=scale.cmap,
            norm=scale.norm,
            transform=ccrs.PlateCarree(),
            s=36,
            edgecolors="black",
            linewidths=0.7,
            rasterized=True,
            zorder=6,
        )
    # A layout-managed colorbar would re-anchor the north-anchored map or
    # leave a gap. This child axis follows the drawn map below its longitude
    # labels, widening when GEOS-IT bins need room for distinct tick labels;
    # constrained layout reserves its space within the map's margins.
    cax = axis.inset_axes((0, 0, 1, 1))
    cax.set_axes_locator(_colorbar_locator(axis, bins=scale.levels.size - 1))
    colorbar = _colorbar(figure, mesh, axis, scale, None, cax=cax)
    colorbar.ax.tick_params(labelsize=7.5)
    mean = area_mean(field.values, grid.lat, grid.lon, region)
    heading, detail = _title(
        axis,
        labeling.sourced_title(labeling.legend_label(source), _quantity("aod", None)),
        labeling.map_subtitle(
            field.start,
            field.end,
            mean=mean,
            sites=len(sites) if sites is not None else None,
            decimals=3,
        ),
    )
    heading.set_fontsize(12)
    dense = [mesh, getattr(mesh, "_wrapped_collection_fix", None), markers]
    dense = [artist for artist in dense if artist is not None]
    metadata["dense_artists"].extend(dense)
    metadata.setdefault("field_artists", []).extend(
        {"artist": artist, "kind": "aod"} for artist in dense
    )
    metadata.setdefault("colorbars", []).append(colorbar)
    metadata["titles"].append(heading)
    metadata["subtitles"].append(detail)
    metadata["provenance"].update(
        map_start=field.start.isoformat(),
        map_end=field.end.isoformat(),
        map_mean=mean,
        map_windows=field.windows,
        grid_created_utc=list(field.provenance.get("grid_created_utc", [])),
        processing_commits=list(field.provenance.get("processing_commits", [])),
    )
    return markers, colorbar


def _draw_map(
    figure: Figure,
    axis: Any,
    config: RunConfig,
    pairs: pd.DataFrame | None,
    region: Region,
    field: RegionalField | None,
    source: str,
) -> None:
    """Context map; without AERONET pairs, it shows the satellite field alone."""
    metadata = getattr(figure, "_sarb_plot_metadata")
    if pairs is None and field is None:
        raise ValueError("A regional map without AERONET sites requires an AOD field")
    projection = _projection(region)
    coastline, gridliner = _map_context(axis, region, config.plots.coastline_resolution)
    extent = context_extent(region, config.plots.regional_map_padding)
    axis.set_xlim(extent[:2])
    axis.set_ylim(extent[2:])
    gridliner.xlabel_style = {"size": 8}
    gridliner.ylabel_style = {"size": 8}
    gridliner.xlocator.set_params(nbins=5)
    gridliner.ylocator.set_params(nbins=4)
    west, east, south, north = region.extent
    center = 180 if west > east else 0
    left, right = west - center, east + (360 if west > east else 0) - center
    outline_style: dict[str, Any] = (
        {"color": "white", "linewidth": 1.6, "path_effects": _OUTLINE_EFFECTS}
        if field is not None
        else {"color": NCAR_ACCENT, "linewidth": 1.5}
    )
    (outline,) = axis.plot(
        [left, right, right, left, left],
        [south, south, north, north, south],
        transform=projection,
        zorder=5,
        **outline_style,
    )
    sites = None
    if pairs is not None:
        finite = np.isfinite(pairs[["x", "y"]].to_numpy(dtype=float)).all(axis=1)
        sites = (
            pairs.loc[finite]
            .groupby("site", observed=True, sort=False)
            .agg(
                longitude=("longitude", "first"),
                latitude=("latitude", "first"),
                aeronet=("x", "mean"),
            )
            .reset_index()
        )
    colorbar = None
    handles = []
    if field is not None:
        markers, colorbar = _draw_field(figure, axis, config, field, sites, region, extent, source)
        site_handle = Line2D(
            [],
            [],
            marker="o",
            linestyle="none",
            markerfacecolor="white",
            markeredgecolor="black",
            markersize=6,
            label=labeling.title_text("AERONET Site Mean"),
        )
    else:
        assert sites is not None
        markers = axis.scatter(
            sites.longitude,
            sites.latitude,
            transform=ccrs.PlateCarree(),
            s=12,
            color=Y_COLOR,
            edgecolors="none",
            rasterized=True,
            zorder=6,
        )
        site_handle = Line2D(
            [],
            [],
            marker="o",
            linestyle="none",
            color=Y_COLOR,
            markersize=np.sqrt(12),
            label=labeling.legend_label("aeronet"),
        )
        metadata["dense_artists"].append(markers)
    if sites is not None:
        handles.append(site_handle)
    handles.append(Line2D([], [], label=labeling.title_text("Analysis Region"), **outline_style))
    city_frame = pd.DataFrame(columns=["name", "longitude", "latitude", "population"])
    checksums: dict[str, str] = {}
    if config.plots.regional_city_limit:
        city_frame, checksums = _cities(cartopy_data_dir(config))
        native = projection.transform_points(
            ccrs.PlateCarree(), city_frame.longitude.to_numpy(), city_frame.latitude.to_numpy()
        )
        in_view = (
            (native[:, 0] >= extent[0])
            & (native[:, 0] <= extent[1])
            & (native[:, 1] >= extent[2])
            & (native[:, 1] <= extent[3])
        )
        city_frame = city_frame.loc[in_view & (city_frame.population >= 1_000_000)].copy()
        city_frame["in_region"] = spatial_mask(region, city_frame.longitude, city_frame.latitude)
        city_frame = city_frame.sort_values(
            ["in_region", "population", "name"], ascending=[False, False, True]
        )
        handles.append(
            Line2D(
                [],
                [],
                marker="o",
                linestyle="none",
                color="0.7",
                markeredgecolor="black" if field is not None else "0.7",
                markeredgewidth=0.5,
                markersize=np.sqrt(20),
                label=labeling.title_text("Major Cities"),
            )
        )
    if colorbar is None:
        axis.legend(
            handles=handles,
            loc="upper center",
            bbox_to_anchor=(0.5, -0.08),
            ncols=2,
            fontsize=8,
            frameon=False,
        )
        title = axis.set_title(labeling.title_text("Regional Context"), y=1.0, fontsize=12, pad=10)
        metadata["titles"].append(title)
    else:
        # Beneath the colorbar's tick labels, where it covers no data. A
        # physical offset is independent of the colorbar's height.
        colorbar.ax.legend(
            handles=handles,
            loc="upper center",
            bbox_to_anchor=(0.5, 0),
            bbox_transform=colorbar.ax.transAxes
            + ScaledTranslation(0, -22 / 72, figure.dpi_scale_trans),
            ncols=3,
            fontsize=8,
            frameon=False,
        )
    metadata["gridliners"].append(gridliner)
    if not city_frame.empty:
        city_frame = _label_cities(axis, city_frame, config.plots.regional_city_limit)
        cities = axis.scatter(
            city_frame.longitude,
            city_frame.latitude,
            transform=ccrs.PlateCarree(),
            s=20,
            color="0.7",
            edgecolors="black" if field is not None else "none",
            linewidths=0.5,
            rasterized=True,
            zorder=7,
        )
        metadata["dense_artists"].append(cities)
    metadata["regional_map"] = {
        "axis": axis,
        "sites": sites,
        "cities": city_frame,
        "region": region,
        "extent": extent,
        "outline": outline,
        "coastline": coastline,
        "markers": markers,
    }
    metadata["provenance"].update(
        city_dataset="Natural Earth 110m Populated Places",
        city_dataset_url="https://www.naturalearthdata.com/downloads/110m-cultural-vectors/110m-populated-places/",
        city_dataset_checksums=checksums,
        cities=city_frame.to_dict(orient="records"),
        aeronet_sites=sites.to_dict(orient="records") if sites is not None else [],
        map_extent=list(extent),
        analysis_extent=list(region.extent),
    )


def _draw_scatter(figure: Figure, axis: Any, source: str) -> None:
    metadata = getattr(figure, "_sarb_plot_metadata")
    daily = metadata["regional_daily"]
    finite = np.isfinite(daily[["x", "y"]].to_numpy(dtype=float)).all(axis=1)
    paired = daily.loc[finite]
    values = paired[["x", "y"]].to_numpy(dtype=float)
    lower = min(0, float(values.min())) if values.size else 0
    upper = max(0.1, float(values.max())) if values.size else 1
    margin = 0.06 * (upper - lower)
    limits = (lower - margin, upper + margin)
    axis.plot(
        limits, limits, "--", color="0.5", linewidth=1, label=labeling.reference_label("one_to_one")
    )
    markers = axis.scatter(paired.x, paired.y, s=25, color=Y_COLOR, alpha=0.85, rasterized=True)
    if paired.empty:
        axis.text(
            0.5,
            0.45,
            labeling.reference_label("no_data"),
            transform=axis.transAxes,
            ha="center",
            va="center",
            fontsize=10,
        )
    axis.set(xlim=limits, ylim=limits, aspect="equal")
    axis.set_xlabel(labeling.axis_label("AOD 550 nm", "1", "aeronet"), fontsize=10)
    axis.set_ylabel(labeling.axis_label("AOD 550 nm", "1", source), fontsize=10)
    axis.tick_params(labelsize=9)
    axis.legend(loc="lower right", fontsize=8)
    heading = axis.set_title(labeling.title_text("AOD"), fontsize=12, pad=10)
    stats = axis.text(
        0.03,
        0.97,
        labeling.regional_statistics_text(metadata["metrics"]).replace(" | ", "\n"),
        transform=axis.transAxes,
        ha="left",
        va="top",
        fontsize=9,
        bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.9, "pad": 3},
    )
    metadata["titles"].append(heading)
    metadata["statistics_annotations"] = [stats]
    metadata["dense_artists"].append(markers)
    metadata["scatter_pairs"] = paired.copy()


def _draw_provenance(figure: Figure) -> None:
    metadata = getattr(figure, "_sarb_plot_metadata")
    axis = metadata["regional_summary"]["provenance_axis"]
    for text in metadata.get("provenance_text", []):
        text.remove()
    axis.set_axis_off()
    figure.canvas.draw()
    renderer = figure.canvas.get_renderer()  # type: ignore[attr-defined]
    panel_box = axis.get_window_extent(renderer)
    title_box = metadata["regional_summary"]["scatter_axis"].title.get_window_extent(renderer)
    top_offset = (title_box.y1 - panel_box.y1) * 72 / figure.dpi
    entries = []
    sections = labeling.regional_provenance_sections(metadata["provenance"])
    for heading, body in sections:
        for value, bold in ((heading, True), (body, False)):
            if value:
                text = axis.text(
                    0,
                    1,
                    value,
                    ha="left",
                    va="top",
                    fontsize=11 if value == "Provenance" else 9 if bold else 8,
                    fontweight="bold" if bold else "normal",
                    fontfamily="sans-serif" if bold else "monospace",
                    linespacing=1.1,
                    parse_math=False,
                )
                text.set_in_layout(False)
                entries.append(text)
    box = axis.get_window_extent(renderer)
    height = sum(text.get_window_extent(renderer).height + 2 * figure.dpi / 72 for text in entries)
    width = max(text.get_window_extent(renderer).width for text in entries)
    factor = min(
        1, 0.98 * (box.height + top_offset * figure.dpi / 72) / height, 0.98 * box.width / width
    )
    offset = 0.0
    for text in entries:
        text.set_fontsize(text.get_fontsize() * factor)
        text.set_transform(
            axis.transAxes
            + ScaledTranslation(0, (top_offset - offset) / 72, figure.dpi_scale_trans)
        )
        offset += text.get_window_extent(renderer).height * 72 / figure.dpi + 2
    entries[0].set_url(REPOSITORY_URL)
    entries[0].set_color("#365f80")
    metadata["provenance_text"] = entries


def _place_statistics(figure: Figure) -> None:
    """Prefer a corner whose statistics box covers no daily scatter points."""
    metadata = getattr(figure, "_sarb_plot_metadata")
    axis = metadata["regional_summary"]["scatter_axis"]
    annotation = metadata["statistics_annotations"][0]
    figure.canvas.draw()
    renderer = figure.canvas.get_renderer()  # type: ignore[attr-defined]
    points = axis.transData.transform(metadata["scatter_pairs"][["x", "y"]].to_numpy())
    candidates = []
    for index, (x, y, ha, va, legend) in enumerate(
        [
            (0.03, 0.97, "left", "top", "lower right"),
            (0.97, 0.03, "right", "bottom", "upper left"),
            (0.97, 0.97, "right", "top", "lower left"),
            (0.03, 0.03, "left", "bottom", "upper right"),
        ]
    ):
        annotation.set(position=(x, y), ha=ha, va=va)
        box = annotation.get_window_extent(renderer).padded(5 * figure.dpi / 72)
        count = np.count_nonzero(
            (points[:, 0] >= box.x0)
            & (points[:, 0] <= box.x1)
            & (points[:, 1] >= box.y0)
            & (points[:, 1] <= box.y1)
        )
        candidates.append((count, index, x, y, ha, va, legend))
    _, _, x, y, ha, va, legend = min(candidates)
    annotation.set(position=(x, y), ha=ha, va=va)
    axis.legend(loc=legend, fontsize=8)


def set_output_provenance(figure: Figure, path: Path) -> None:
    """Fill the actual delivery pathname before protocol checks and PDF metadata."""
    getattr(figure, "_sarb_plot_metadata")["provenance"]["pdf_output"] = str(path)
    _draw_provenance(figure)


def compose(
    figure: Figure,
    config: RunConfig,
    pairs: pd.DataFrame,
    region: str,
    *,
    source: str,
    input_provenance: Mapping[str, Any],
    field: RegionalField | None = None,
) -> Figure:
    """Extend the existing daily renderer, preserving its values and sampling."""
    metadata = getattr(figure, "_sarb_plot_metadata")
    series_axis = metadata["data_axes"][0]
    for text in metadata["statistics_annotations"]:
        text.remove()
    metadata["statistics_annotations"] = []
    figure.set_size_inches(16.2, 8.7)
    figure.set_layout_engine("constrained", h_pad=0.10, w_pad=0.16)
    # The AOD map needs a wider center column for its colorbar ticks.
    widths = [1.05, 1.05, 1] if field is None else [0.95, 1.3, 0.85]
    grid = figure.add_gridspec(2, 3, height_ratios=[0.9, 1.1], width_ratios=widths)
    series_axis.set_subplotspec(grid[0, :])
    panel = figure.add_subplot(grid[1, 0])
    configure_cartopy(cartopy_data_dir(config))
    subset = get_region(region)
    map_axis = figure.add_subplot(grid[1, 1], projection=_projection(subset))
    scatter_axis = figure.add_subplot(grid[1, 2])
    map_axis.set_anchor("N")
    scatter_axis.set_anchor("N")
    metadata["data_axes"].extend([map_axis, scatter_axis])
    metadata["regional_summary"] = {"provenance_axis": panel, "scatter_axis": scatter_axis}
    if source == "MODIS":
        source = labeling.collection_source_name(
            "modis", [s.collection for s in config.grid.streams.values()]
        )
    series_axis.lines[1].set_label(labeling.legend_label(source))
    series_axis.legend(loc="upper left", fontsize=10, ncols=2)
    config_files = [item["path"] for item in config._config_sources if item["role"] == "run"]
    metadata["provenance"] = {
        "repository_url": REPOSITORY_URL,
        "plot_git_commit": git_commit(),
        "plot_created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "weighting": config.weighting.method,
        "rho": config.weighting.rho,
        "qc": config.qc.preset,
        "qc_overrides": config.qc.model_dump(mode="json")["overrides"],
        "matchup_input": dict(input_provenance),
        "config_files": config_files,
        "pdf_output": "Pending Save",
        "sampling": "independent UTC daily means; paired site-days; equally weighted active sites",
        "scatter_basis": "the same regional daily means shown in the time series",
    }
    _draw_scatter(figure, scatter_axis, source)
    _draw_map(figure, map_axis, config, pairs, subset, field, source)
    if field is not None:
        # Bottom-row titles share one baseline above the map's mean subtitle.
        subtitle = metadata["subtitles"][-1]
        scatter_axis.set_title(
            scatter_axis.get_title(), fontsize=12, pad=subtitle.get_fontsize() + 18
        )
    _draw_provenance(figure)
    _place_statistics(figure)
    return figure
