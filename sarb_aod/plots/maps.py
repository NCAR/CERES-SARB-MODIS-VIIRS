"""DAVINCI-style gridded and site maps with explicit PDF rasterization.

Adapted from NCAR/DAVINCI ``plots/renderers/spatial/{base,field}.py`` and
``plots/renderers/aod_correction_science.py`` (origin/develop, Apache-2.0).
Grid bounds and cyclic contours are specific to the CAM7 f09 product here.
Ordinary rendering uses cached coastlines only; downloads belong exclusively
to the explicit prefetch command in ``style.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Any, Literal

import cartopy
import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.pyplot as plt
import numpy as np
from cartopy.io import Downloader
from cartopy.util import add_cyclic_point
from matplotlib.colors import BoundaryNorm, Colormap, Normalize, TwoSlopeNorm
from matplotlib.figure import Figure
from matplotlib.transforms import ScaledTranslation
from numpy.typing import ArrayLike

from sarb_aod.evaluation.regions import Region, get_region, spatial_mask
from sarb_aod.grid.spec import GridSpec, load_grid
from sarb_aod.plots import labeling
from sarb_aod.plots.labels import calculate_symmetric_limits
from sarb_aod.plots.style import aod_colorbar_ticks, configure_cartopy, geosit_aod_levels

FieldKind = Literal[
    "aod", "difference", "bias", "fraction", "count", "uncertainty", "chi2", "rmse", "correlation"
]


@dataclass(frozen=True)
class FieldScale:
    kind: FieldKind
    cmap: Colormap
    norm: Normalize
    levels: np.ndarray
    ticks: np.ndarray | None
    extend: Literal["neither", "both", "min", "max"]


def _values(data: ArrayLike) -> np.ndarray:
    values: np.ndarray = np.ma.asarray(data, dtype=float).filled(np.nan)
    return np.where(np.isfinite(values) & (values > -900), values, np.nan)


def nice_robust_limits(
    values: ArrayLike,
    robust_pct: tuple[float, float] = (2, 98),
    *,
    target_intervals: int = 5,
) -> tuple[float, float]:
    """DAVINCI 2–98% range, rounded outward to 1/2/2.5/5/10 steps.

    Empty fields use [0, 1]; constant fields get a nonzero range. Field scales
    for nonnegative diagnostics additionally pin the lower bound to zero.
    """
    if target_intervals < 1:
        raise ValueError("target_intervals must be positive")
    if len(robust_pct) != 2 or not 0 <= robust_pct[0] <= robust_pct[1] <= 100:
        raise ValueError("robust_pct must contain two ordered percentiles in [0, 100]")
    data = _values(values)
    finite = data[np.isfinite(data)]
    if not finite.size:
        return 0.0, 1.0
    low, high = np.percentile(finite, robust_pct)
    if high == low:
        low, high = min(float(low), 0.0), max(float(high), 0.0)
        if high == low:
            return 0.0, 1.0
    raw_step = (high - low) / target_intervals
    magnitude = 10.0 ** np.floor(np.log10(raw_step))
    fraction = raw_step / magnitude
    nice_fraction = next(value for value in (1.0, 2.0, 2.5, 5.0, 10.0) if fraction <= value)
    step = nice_fraction * magnitude
    return float(np.floor(low / step) * step), float(np.ceil(high / step) * step)


def field_scale(
    values: ArrayLike,
    kind: FieldKind = "aod",
    *,
    aod_levels: ArrayLike | None = None,
) -> FieldScale:
    """Build one reusable scale from every panel's concatenated values."""
    data = _values(values)
    if kind == "aod":
        levels = (
            geosit_aod_levels(data) if aod_levels is None else np.asarray(aod_levels, dtype=float)
        )
        if (
            levels.ndim != 1
            or levels.size < 2
            or not np.isfinite(levels).all()
            or levels[0] != 0
            or levels[-1] > 1
            or np.any(np.diff(levels) <= 0)
        ):
            raise ValueError("AOD levels must increase from zero to a cap no higher than one")
        cmap = plt.get_cmap("turbo")
        return FieldScale(
            kind,
            cmap,
            BoundaryNorm(levels, cmap.N, extend="max"),
            levels,
            aod_colorbar_ticks(levels),
            "max",
        )
    if aod_levels is not None:
        raise ValueError("aod_levels only applies to absolute AOD maps")
    if kind in ("difference", "bias"):
        vmin, vmax = calculate_symmetric_limits(data, percentile=98)
        return FieldScale(
            kind,
            plt.get_cmap("RdBu_r"),
            TwoSlopeNorm(vmin=vmin, vcenter=0, vmax=vmax),
            np.linspace(vmin, vmax, 21),
            None,
            "both",
        )
    if kind in ("fraction", "correlation"):
        return FieldScale(
            kind,
            plt.get_cmap("gray_r" if kind == "fraction" else "viridis"),
            Normalize(0, 1),
            np.linspace(0, 1, 21),
            np.linspace(0, 1, 6),
            "neither",
        )
    if kind in ("count", "uncertainty", "chi2", "rmse"):
        _, high = nice_robust_limits(data)
        high = high if high > 0 else 1.0
        return FieldScale(
            kind, plt.get_cmap("viridis"), Normalize(0, high), np.linspace(0, high, 21), None, "max"
        )
    raise ValueError(f"Unknown map field kind {kind!r}")


def _grid_coordinates(
    values: np.ndarray,
    lats: np.ndarray,
    lons: np.ndarray,
    lat_bnds: ArrayLike | None,
    lon_bnds: ArrayLike | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if lats.ndim != 1 or lons.ndim != 1 or values.shape != (lats.size, lons.size):
        raise ValueError(
            "gridded values must have shape (lat, lon) with one-dimensional coordinates"
        )
    if not np.isfinite(lats).all() or not np.isfinite(lons).all() or np.any(np.diff(lats) <= 0):
        raise ValueError("grid coordinates must be finite and latitude strictly increasing")
    if lat_bnds is None or lon_bnds is None:
        canonical = load_grid()
        if (
            values.shape != canonical.shape
            or not np.allclose(lats, canonical.lat, rtol=0, atol=1e-6)
            or not np.allclose(lons, canonical.lon, rtol=0, atol=1e-6)
        ):
            raise ValueError("non-f09 coordinates require explicit latitude and longitude bounds")
        lat_bnds, lon_bnds = canonical.lat_bnds, canonical.lon_bnds
    lat_bounds, lon_bounds = np.asarray(lat_bnds, dtype=float), np.asarray(lon_bnds, dtype=float)
    if lat_bounds.shape != (lats.size, 2) or lon_bounds.shape != (lons.size, 2):
        raise ValueError("bounds must have shape (coordinate, 2)")
    if not np.isfinite(lat_bounds).all() or not np.isfinite(lon_bounds).all():
        raise ValueError("grid bounds must be finite")
    if np.any(lat_bounds[:, 1] <= lat_bounds[:, 0]) or not np.allclose(
        lat_bounds[:-1, 1], lat_bounds[1:, 0], rtol=0, atol=1e-6
    ):
        raise ValueError("latitude bounds must be increasing and contiguous")
    latitude_edges = np.clip(np.concatenate([lat_bounds[:, 0], lat_bounds[-1:, 1]]), -90, 90)
    normalized = ((lons + 180) % 360) - 180
    order = np.argsort(normalized)
    shifted_bounds = lon_bounds + (normalized - lons)[:, None]
    shifted_bounds = shifted_bounds[order]
    if np.any(np.diff(normalized[order]) <= 0) or not np.allclose(
        shifted_bounds[:-1, 1], shifted_bounds[1:, 0], rtol=0, atol=1e-6
    ):
        raise ValueError("longitude cells must be unique and contiguous after normalization")
    longitude_edges = np.concatenate([shifted_bounds[:, 0], shifted_bounds[-1:, 1]])
    return values[:, order], lats, normalized[order], latitude_edges, longitude_edges


def draw_spatial_field(
    ax: Any,
    values: ArrayLike,
    lats: ArrayLike,
    lons: ArrayLike,
    *,
    plot_type: Literal["pcolormesh", "contourf", "scatter"] = "pcolormesh",
    scale: FieldScale | None = None,
    lat_bnds: ArrayLike | None = None,
    lon_bnds: ArrayLike | None = None,
    marker_sizes: ArrayLike = 25.0,
    rasterized: bool = True,
) -> Any:
    """Draw only the dense layer; titles, context and colorbar are caller-owned.

    Meshes use flat-shaded cell bounds. Contours sort normalized longitudes and
    add a cyclic column before projection, preventing dateline gaps and Shapely
    warnings. AOD negatives retain the colormap's lowest-bin color.
    """
    data, lat, lon = _values(values), np.asarray(lats, dtype=float), np.asarray(lons, dtype=float)
    style = scale if scale is not None else field_scale(data)
    kwargs = {"cmap": style.cmap, "norm": style.norm, "transform": ccrs.PlateCarree(), "zorder": 1}
    if plot_type == "scatter":
        if data.shape != lat.shape or data.shape != lon.shape:
            raise ValueError("point values, latitudes and longitudes must have identical shapes")
        sizes = np.broadcast_to(np.asarray(marker_sizes, dtype=float), data.shape)
        valid = (
            np.isfinite(data)
            & np.isfinite(lat)
            & np.isfinite(lon)
            & (np.abs(lat) <= 90)
            & np.isfinite(sizes)
            & (sizes > 0)
        )
        return ax.scatter(
            ((lon[valid] + 180) % 360) - 180,
            lat[valid],
            c=data[valid],
            s=sizes[valid],
            edgecolors="none",
            rasterized=rasterized,
            **kwargs,
        )
    if plot_type not in ("pcolormesh", "contourf"):
        raise ValueError("plot_type must be pcolormesh, contourf or scatter")
    data, lat, lon, lat_edges, lon_edges = _grid_coordinates(data, lat, lon, lat_bnds, lon_bnds)
    if plot_type == "pcolormesh":
        artist = ax.pcolormesh(
            lon_edges, lat_edges, data, shading="flat", rasterized=rasterized, **kwargs
        )
        wrapped = getattr(artist, "_wrapped_collection_fix", None)
        if wrapped is not None:
            wrapped.set_rasterized(rasterized)
        return artist
    # With extend='max', contourf does not paint values below its first level.
    # Map negative AOD to the lowest display bin while preserving it in means.
    contour_values = np.maximum(data, 0) if style.kind == "aod" else data
    cyclic, cyclic_lon = add_cyclic_point(np.ma.masked_invalid(contour_values), coord=lon)
    artist = ax.contourf(
        cyclic_lon, lat, cyclic, levels=style.levels, extend=style.extend, **kwargs
    )
    artist.set_rasterized(rasterized)
    return artist


def area_mean(
    values: ArrayLike, lats: ArrayLike, lons: ArrayLike, region: str | Region | None = None
) -> float:
    """Cos-latitude mean of finite cells, optionally within a region's centers."""
    data, lat, lon = _values(values), np.asarray(lats, dtype=float), np.asarray(lons, dtype=float)
    if data.shape != (lat.size, lon.size):
        raise ValueError("area mean expects a (lat, lon) grid")
    valid = np.isfinite(data)
    if region is not None:
        valid &= spatial_mask(region, lon[None, :], lat[:, None])
    weights = np.broadcast_to(np.clip(np.cos(np.deg2rad(lat)), 0, None)[:, None], data.shape)
    denominator = weights[valid].sum()
    return (
        float(np.sum(data[valid] * weights[valid]) / denominator)
        if denominator > 0
        else float("nan")
    )


def site_marker_sizes(counts: ArrayLike, size_range: tuple[float, float] = (20, 100)) -> np.ndarray:
    """Marker areas in points² scale with sqrt(N/max(N)); zero counts disappear."""
    lower, upper = size_range
    if not 0 < lower <= upper or not np.isfinite(size_range).all():
        raise ValueError("marker size range must be finite, positive and increasing")
    n = np.asarray(counts, dtype=float)
    valid = np.isfinite(n) & (n > 0)
    sizes = np.zeros(n.shape)
    if valid.any():
        sizes[valid] = lower + (upper - lower) * np.sqrt(n[valid] / n[valid].max())
    return sizes


def _cached_coastline(resolution: str) -> None:
    if resolution not in {"110m", "50m"}:
        raise ValueError("coastline resolution must be 110m or 50m")
    downloader = Downloader.from_config(
        ("shapefiles", "natural_earth", resolution, "physical", "coastline")
    )
    parameters = {
        "config": cartopy.config,
        "resolution": resolution,
        "category": "physical",
        "name": "coastline",
    }
    candidates = (downloader.pre_downloaded_path(parameters), downloader.target_path(parameters))
    if not any(
        path is not None
        and all(Path(path).with_suffix(suffix).exists() for suffix in (".shp", ".shx", ".dbf"))
        for path in candidates
    ):
        raise FileNotFoundError(
            f"Cached {resolution} coastline is missing; run sarb-aod plot --prefetch-maps"
        )


def _map_context(ax: Any, region: Region | None, resolution: str | None) -> tuple[Any, Any]:
    chosen = resolution or ("110m" if region is None or region.key == "global" else "50m")
    _cached_coastline(chosen)
    if region is None or region.key == "global":
        ax.set_global()
    else:
        west, east, south, north = region.extent
        ax.set_extent(
            (west, east + 360 if west > east else east, south, north), crs=ccrs.PlateCarree()
        )
    # Cartopy 0.25 adopts the shapefile's geographic CRS after its first read.
    # Reprojecting the exact global boundary into that CRS can create invalid
    # empty geometry collections at the poles. These Natural Earth coordinates
    # are longitude/latitude degrees, so an explicit PlateCarree source is exact
    # and avoids the unnecessary geographic extent transformation.
    feature = cfeature.NaturalEarthFeature("physical", "coastline", chosen)
    coastline = ax.add_geometries(
        feature.geometries(),
        ccrs.PlateCarree(),
        facecolor="none",
        edgecolor="black",
        linewidth=0.5,
        zorder=3,
    )
    coastline.set_gid("coastline")
    gridliner = ax.gridlines(draw_labels=True, linewidth=0.5, alpha=0.5, linestyle="-", zorder=4)
    gridliner.top_labels = False
    gridliner.right_labels = False
    return coastline, gridliner


def _projection(region: Region | None) -> Any:
    return ccrs.PlateCarree(
        central_longitude=180 if region and region.extent[0] > region.extent[1] else 0
    )


def _quantity(kind: FieldKind, quantity: str | None) -> str:
    defaults = {
        "aod": "AOD",
        "difference": "AOD Difference",
        "bias": "AOD Bias",
        "fraction": "AOD Coverage",
        "count": "Pixel Count",
        "uncertainty": "AOD Uncertainty",
        "chi2": "Reduced χ²",
        "rmse": "AOD Error",
        "correlation": "AOD Correlation",
    }
    return labeling.title_text(quantity or defaults[kind])


def _title(ax: Any, title: str, subtitle: str) -> tuple[Any, Any]:
    labeling.validate_title(title)
    # Physical spacing stays legible for shallow latitude bands as well as
    # global and tall regional extents. Axes-fraction offsets collapse when
    # the map's geographic aspect ratio makes the axes short.
    detail = ax.text(
        0.5,
        1.0,
        subtitle,
        transform=ax.transAxes + ScaledTranslation(0, 8 / 72, ax.figure.dpi_scale_trans),
        ha="center",
        va="bottom",
        fontsize="small",
    )
    heading = ax.set_title(title, y=1.0, pad=detail.get_fontsize() + 18)
    return heading, detail


def _colorbar(figure: Figure, artist: Any, axes: Any, scale: FieldScale, units: str | None) -> Any:
    colorbar = figure.colorbar(
        artist,
        ax=axes,
        orientation="horizontal",
        fraction=0.055,
        pad=0.09,
        shrink=0.90,
        aspect=45,
        extend=scale.extend,
    )
    if scale.ticks is not None:
        colorbar.set_ticks(scale.ticks.tolist())
    colorbar.set_label(labeling.format_units(units))
    if colorbar.solids is not None:
        colorbar.solids.set_rasterized(False)
    return colorbar


def _metadata(
    figure: Figure,
    *,
    kind: str,
    axes: list[Any],
    artists: list[Any],
    scale: FieldScale,
    colorbar: Any,
    titles: list[Any],
    subtitles: list[Any],
    gridliners: list[Any],
    region: Region | None,
    plot_type: str,
) -> None:
    dense = []
    for artist in artists:
        dense.append(artist)
        wrapped = getattr(artist, "_wrapped_collection_fix", None)
        if wrapped is not None:
            dense.append(wrapped)
    absolute = dense if scale.kind == "aod" else []
    metadata = {
        "kind": kind,
        "data_axes": axes,
        "dense_artists": dense,
        "field_artists": [{"artist": artist, "kind": scale.kind} for artist in dense],
        "absolute_aod_artists": absolute,
        "colorbars": [colorbar],
        "titles": titles,
        "subtitles": subtitles,
        "gridliners": gridliners,
        "region": region,
        "plot_type": plot_type,
    }
    setattr(figure, "_sarb_plot_metadata", metadata)
    for prefix in ("_sarb", "_davinci"):
        setattr(figure, f"{prefix}_dense_artists", dense)
        setattr(figure, f"{prefix}_absolute_aod_artists", absolute)


def plot_map(
    values: ArrayLike,
    *,
    grid: GridSpec | None = None,
    kind: FieldKind = "aod",
    source: str | None = None,
    quantity: str | None = None,
    units: str | None = "1",
    start: Any = None,
    end: Any = None,
    region: str | Region | None = None,
    plot_type: Literal["pcolormesh", "contourf"] = "pcolormesh",
    aod_levels: ArrayLike | None = None,
    cartopy_data_dir: str | Path | None = None,
    coastline_resolution: str | None = None,
    figsize: tuple[float, float] = (9, 5),
    rasterized: bool = True,
) -> Figure:
    """One grid map; callers save with bbox_inches=figure.bbox_inches."""
    if cartopy_data_dir is not None:
        configure_cartopy(cartopy_data_dir)
    spec = grid if grid is not None else load_grid()
    subset = get_region(region) if region is not None else None
    scale = field_scale(values, kind, aod_levels=aod_levels)
    figure, ax = plt.subplots(figsize=figsize, subplot_kw={"projection": _projection(subset)})
    _, gridliner = _map_context(ax, subset, coastline_resolution)
    artist = draw_spatial_field(
        ax,
        values,
        spec.lat,
        spec.lon,
        plot_type=plot_type,
        scale=scale,
        lat_bnds=spec.lat_bnds,
        lon_bnds=spec.lon_bnds,
        rasterized=rasterized,
    )
    quantity_text = _quantity(kind, quantity)
    title = (
        labeling.title_for_labeled_subset(quantity_text, subset.key)[0]
        if subset and subset.key != "global"
        else labeling.sourced_title(source, quantity_text)
    )
    subtitle = labeling.map_subtitle(
        start, end, mean=area_mean(values, spec.lat, spec.lon, subset), decimals=3
    )
    heading, detail = _title(ax, title, subtitle)
    colorbar = _colorbar(figure, artist, ax, scale, units)
    figure.tight_layout(pad=0.5)
    _metadata(
        figure,
        kind="map",
        axes=[ax],
        artists=[artist],
        scale=scale,
        colorbar=colorbar,
        titles=[heading],
        subtitles=[detail],
        gridliners=[gridliner],
        region=subset,
        plot_type=plot_type,
    )
    return figure


def map_panels(
    fields: Sequence[ArrayLike],
    sources: Sequence[str],
    *,
    grid: GridSpec | None = None,
    kind: FieldKind = "aod",
    quantity: str | None = None,
    units: str | None = "1",
    start: Any = None,
    end: Any = None,
    region: str | Region | None = None,
    columns: int = 2,
    plot_type: Literal["pcolormesh", "contourf"] = "pcolormesh",
    aod_levels: ArrayLike | None = None,
    cartopy_data_dir: str | Path | None = None,
    coastline_resolution: str | None = None,
) -> Figure:
    """Shared-scale panels, with a single horizontal vector colorbar."""
    if not fields or len(fields) != len(sources) or columns < 1:
        raise ValueError("panels require matching nonempty fields/sources and positive columns")
    if cartopy_data_dir is not None:
        configure_cartopy(cartopy_data_dir)
    spec = grid if grid is not None else load_grid()
    subset = get_region(region) if region is not None else None
    scale = field_scale(
        np.concatenate([_values(field).ravel() for field in fields]), kind, aod_levels=aod_levels
    )
    columns = min(columns, len(fields))
    rows = ceil(len(fields) / columns)
    figure = plt.figure(figsize=(5.4 * columns, 3.6 * rows + 0.9), constrained_layout=True)
    axes, artists, titles, subtitles, gridliners = [], [], [], [], []
    quantity_text = _quantity(kind, quantity)
    for index, (field, source) in enumerate(zip(fields, sources, strict=True)):
        ax = figure.add_subplot(rows, columns, index + 1, projection=_projection(subset))
        _, gridliner = _map_context(ax, subset, coastline_resolution)
        artist = draw_spatial_field(
            ax,
            field,
            spec.lat,
            spec.lon,
            plot_type=plot_type,
            scale=scale,
            lat_bnds=spec.lat_bnds,
            lon_bnds=spec.lon_bnds,
        )
        heading, subtitle = _title(
            ax,
            labeling.sourced_title(source, quantity_text),
            labeling.map_subtitle(
                start, end, mean=area_mean(field, spec.lat, spec.lon, subset), decimals=3
            ),
        )
        axes.append(ax)
        artists.append(artist)
        titles.append(heading)
        subtitles.append(subtitle)
        gridliners.append(gridliner)
    colorbar = _colorbar(figure, artists[0], axes, scale, units)
    overall = (
        labeling.title_for_labeled_subset(quantity_text, subset.key)[0]
        if subset and subset.key != "global"
        else quantity_text
    )
    labeling.validate_title(overall)
    titles.insert(0, figure.suptitle(overall))
    _metadata(
        figure,
        kind="map_panels",
        axes=axes,
        artists=artists,
        scale=scale,
        colorbar=colorbar,
        titles=titles,
        subtitles=subtitles,
        gridliners=gridliners,
        region=subset,
        plot_type=plot_type,
    )
    return figure


def plot_site_map(
    lons: ArrayLike,
    lats: ArrayLike,
    values: ArrayLike,
    counts: ArrayLike,
    *,
    kind: FieldKind = "aod",
    source: str = "aeronet",
    quantity: str | None = None,
    units: str | None = "1",
    start: Any = None,
    end: Any = None,
    region: str | Region | None = None,
    aod_levels: ArrayLike | None = None,
    size_range: tuple[float, float] = (20, 100),
    cartopy_data_dir: str | Path | None = None,
    coastline_resolution: str | None = None,
) -> Figure:
    """AERONET/site metric map with fixed-range marker areas scaled by N."""
    if cartopy_data_dir is not None:
        configure_cartopy(cartopy_data_dir)
    lon, lat, data, n = (
        np.asarray(lons, dtype=float),
        np.asarray(lats, dtype=float),
        _values(values),
        np.asarray(counts, dtype=float),
    )
    if not lon.shape == lat.shape == data.shape == n.shape:
        raise ValueError("site coordinates, values and counts must have identical shapes")
    subset = get_region(region) if region is not None else None
    valid = (
        np.isfinite(data)
        & np.isfinite(lon)
        & np.isfinite(lat)
        & (np.abs(lat) <= 90)
        & np.isfinite(n)
        & (n > 0)
    )
    if subset is not None:
        valid &= spatial_mask(subset, lon, lat)
    lon, lat, data, n = lon[valid], lat[valid], data[valid], n[valid]
    scale = field_scale(data, kind, aod_levels=aod_levels)
    figure, ax = plt.subplots(figsize=(9, 5), subplot_kw={"projection": _projection(subset)})
    _, gridliner = _map_context(ax, subset, coastline_resolution)
    artist = draw_spatial_field(
        ax,
        data,
        lat,
        lon,
        plot_type="scatter",
        scale=scale,
        marker_sizes=site_marker_sizes(n, size_range),
    )
    quantity_text = _quantity(kind, quantity)
    title = (
        labeling.title_for_labeled_subset(quantity_text, subset.key)[0]
        if subset and subset.key != "global"
        else labeling.sourced_title(source, quantity_text)
    )
    heading, detail = _title(ax, title, labeling.map_subtitle(start, end, sites=data.size))
    colorbar = _colorbar(figure, artist, ax, scale, units)
    figure.tight_layout(pad=0.5)
    _metadata(
        figure,
        kind="site_map",
        axes=[ax],
        artists=[artist],
        scale=scale,
        colorbar=colorbar,
        titles=[heading],
        subtitles=[detail],
        gridliners=[gridliner],
        region=subset,
        plot_type="scatter",
    )
    return figure
