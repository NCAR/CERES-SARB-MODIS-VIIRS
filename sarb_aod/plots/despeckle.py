"""Protocol-checked maps and time series for same-file paired AOD."""

from __future__ import annotations

from typing import Any, cast

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import BoundaryNorm
from matplotlib.figure import Figure
from matplotlib.transforms import Bbox

from sarb_aod.config.schema import RunConfig
from sarb_aod.despeckle_comparison import PairedAggregate, aggregate_comparison, comparison_table
from sarb_aod.evaluation.regions import Region
from sarb_aod.paths import cartopy_data_dir
from sarb_aod.plots import labeling, maps
from sarb_aod.plots.series import _finish, _new_figure
from sarb_aod.plots.style import geosit_aod_levels
from sarb_aod.plots.suites import PlotArtifact, save_figure, write_protocol_report

SUITE = "aod_despeckle_comparison"


def _sampling_note(figure: Figure, text: str) -> None:
    # Solve the original layout before reserving a fixed physical footer.
    for colorbar in getattr(figure, "_sarb_plot_metadata", {}).get("colorbars", []):
        colorbar.ax.tick_params(labelsize=9)
    figure.canvas.draw()
    figure.set_layout_engine(None)
    # Freeze the extension-adjusted colorbar rectangle. Matplotlib's locator
    # otherwise reinstates its original aspect ratio after our footer resize.
    renderer = getattr(figure.canvas, "get_renderer")()
    for colorbar in getattr(figure, "_sarb_plot_metadata", {}).get("colorbars", []):
        axis = colorbar.ax
        box = axis.get_position().frozen()
        locator = axis.get_axes_locator()
        allocated = locator(axis, renderer) if locator is not None else box
        axis.set_axes_locator(None)
        axis.set_box_aspect(None)
        axis.set_aspect("auto")
        # Use the allocated width but the rendered vertical clearance. The
        # colorbar's default aspect can halve a deliberately wide, thin bar.
        axis.set_position(Bbox.from_bounds(allocated.x0, box.y0, allocated.width, box.height))
    width, height = figure.get_size_inches()
    footer_height = 0.55
    new_height = height + footer_height
    positions = [(axis, axis.get_position().frozen()) for axis in figure.axes]
    # Append space instead of compressing the axes: geographic axes preserve
    # their aspect ratio, and tall source panels need their original clearance.
    figure.set_size_inches(width, new_height)
    for axis, box in positions:
        axis.set_position(
            (
                box.x0,
                (height * box.y0 + footer_height) / new_height,
                box.width,
                height * box.height / new_height,
            )
        )
    for label in figure.texts:
        x, y = label.get_position()
        label.set_position((x, (height * y + footer_height) / new_height))
    figure.text(0.5, 0.15 / new_height, text, ha="center", fontsize=8)


def comparison_series(table: pd.DataFrame, *, start: object, end: object, title: str) -> Figure:
    figure, axes = _new_figure(figsize=(12, 6), nrows=2, sharex=True)
    times = pd.to_datetime(table.time, utc=True).dt.tz_localize(None)
    for name, color in (("unfiltered_common", "#333333"), ("filtered_common", "#0072B2")):
        axes[0].plot(
            times, table[name], label=labeling.despeckle_label(name), color=color, linewidth=1
        )
    for name, color in (
        ("unfiltered_fraction", "#333333"),
        ("filtered_fraction", "#0072B2"),
        ("removed_fraction", "#D55E00"),
        ("replaced_fraction", "#009E73"),
    ):
        key = (
            name.replace("_fraction", "_coverage")
            if name.startswith(("unfiltered", "filtered"))
            else name
        )
        axes[1].plot(
            times, table[name], label=labeling.despeckle_label(key), color=color, linewidth=1
        )
    for ax in axes:
        ax.legend(loc="upper left", ncols=2, fontsize=8)
    axes[0].set_ylabel(labeling.despeckle_label("aod"))
    axes[1].set_ylabel(labeling.despeckle_label("fraction"))
    axes[1].set_xlabel(labeling.despeckle_label("time"))
    axes[1].tick_params(axis="x", rotation=20)
    return _finish(figure, list(axes), kind="despeckle_series", title=title, start=start, end=end)


def _difference_scale(values: np.ndarray) -> maps.FieldScale:
    changed = values[np.isfinite(values) & (values != 0)]
    scale = maps.field_scale(changed, "difference")
    # A small symmetric margin keeps rounded colorbar ticks inside TwoSlopeNorm.
    # Exact decimal endpoints can otherwise become +/-inf through float roundoff.
    assert scale.norm.vmin is not None and scale.norm.vmax is not None
    scale.norm.vmin = float(scale.norm.vmin) * 1.001
    scale.norm.vmax = float(scale.norm.vmax) * 1.001
    return scale


def render_comparison(
    config: RunConfig, aggregate: PairedAggregate | None = None
) -> list[PlotArtifact]:
    result = aggregate if aggregate is not None else aggregate_comparison(config)
    fields = result.fields()
    table = comparison_table(result)
    regions: list[tuple[str, Region | None]] = [("global", None)]
    for i, (south, north) in enumerate(config.despeckle.latitude_bands):
        key = f"band_{i+1}"
        regions.append(
            (key, Region(key, labeling.latitude_band_name(south, north), (-180, 180, south, north)))
        )
    artifacts = []
    levels = geosit_aod_levels(
        np.concatenate([fields[k].ravel() for k in ("unfiltered", "filtered")])
    )
    for key, region in regions:
        for kind, names, sampling in (
            ("aod", ["unfiltered", "filtered"], "available"),
            ("difference", ["difference_common"], "common"),
            ("fraction", ["removed_fraction", "replaced_fraction"], "fractions"),
            ("fraction", ["unfiltered_coverage", "filtered_coverage"], "coverage"),
        ):
            common: dict[str, Any] = dict(
                grid=result.grid,
                kind=kind,
                start=config.period.start,
                end=config.period.end,
                region=region,
                cartopy_data_dir=cartopy_data_dir(config),
                coastline_resolution=config.plots.coastline_resolution,
            )
            if kind == "difference":
                figure = maps.plot_map(
                    fields[names[0]],
                    quantity=labeling.title_text("AOD (550 nm)", "Difference"),
                    scale=_difference_scale(fields[names[0]]),
                    figsize=(12, 3.0) if region else (9, 5),
                    **common,
                )
            else:
                figure = maps.map_panels(
                    [fields[name] for name in names],
                    names,
                    columns=1 if region else 2,
                    source_labels={name: labeling.despeckle_label(name) for name in names},
                    quantity=labeling.despeckle_label("aod") if kind == "aod" else "Fraction",
                    aod_levels=levels if kind == "aod" else None,
                    **common,
                )
            if region is not None and kind == "difference":
                figure.set_layout_engine(None)
                figure.axes[0].set_position((0.09, 0.30, 0.84, 0.24))
                figure.axes[-1].set_position((0.10, 0.07, 0.80, 0.025))
            if region is not None and kind != "difference":
                figure.set_size_inches(12, 5.4)
                figure.set_layout_engine(None)
                heading = getattr(figure, "_suptitle", None)
                if heading is not None:
                    getattr(figure, "_sarb_plot_metadata")["titles"].remove(heading)
                    heading.remove()
                for i, axis in enumerate(figure.axes[:-1]):
                    axis.set_position((0.09, 0.65 - 0.40 * i, 0.84, 0.15))
                figure.axes[-1].set_position((0.09, 0.075, 0.84, 0.025))
            # Sampling is interpretation metadata, separate from quantity titles.
            _sampling_note(
                figure,
                labeling.despeckle_label(sampling)
                + (f" | {region.display_name}" if region else ""),
            )
            getattr(figure, "_sarb_plot_metadata")["comparison_provenance"] = {
                "sampling": sampling,
                "filter": config.despeckle.model_dump(mode="json"),
                "temporal_weights": "original aod_count",
                "cam_aod_variant": "unfiltered",
            }
            name = f"{key}_{sampling}"
            try:
                artifacts.append(
                    save_figure(
                        config,
                        figure,
                        suite=SUITE,
                        name=name,
                        start=config.period.start,
                        end=config.period.end,
                    )
                )
            finally:
                plt.close(figure)
        rows = table.loc[table.region == key]
        title = labeling.subset_map_title(
            labeling.despeckle_label("aod"), key, region.display_name if region else "Global"
        )
        figure = comparison_series(
            rows, start=config.period.start, end=config.period.end, title=title
        )
        try:
            artifacts.append(
                save_figure(
                    config,
                    figure,
                    suite=SUITE,
                    name=f"{key}_timeseries",
                    start=config.period.start,
                    end=config.period.end,
                )
            )
        finally:
            plt.close(figure)
    write_protocol_report(config, SUITE, artifacts)
    destination = (
        artifacts[0].pdf.parent
        / f"comparison_{config.period.start:%Y%m%d}_{config.period.end:%Y%m%d}.tsv"
    )
    table.to_csv(destination, sep="\t", index=False)
    return artifacts


def action_scale() -> maps.FieldScale:
    """Five fixed categories remain distinct even when most cells are unchanged."""
    levels = np.array([0.0, 0.5, 1.5, 2.5, 3.5, 4.5])
    cmap = plt.get_cmap("viridis", 5)
    return maps.FieldScale(
        "count", cmap, BoundaryNorm(levels, cmap.N), levels, np.arange(5), "neither"
    )


def render_window(config: RunConfig, start: Any, region: Region) -> list[PlotArtifact]:
    """Selected window evidence: source pairs, original counts, support and actions."""
    import netCDF4

    from sarb_aod.contract import validate_contract_file
    from sarb_aod.grid.spec import load_grid
    from sarb_aod.paths import output_path
    from sarb_aod.pipeline import require_processing_provenance

    path = output_path(config, start)
    valid, errors = validate_contract_file(path)
    if not valid:
        raise ValueError(f"{path}: {errors}")
    fields: dict[str, np.ndarray] = {}
    with netCDF4.Dataset(path) as ds:
        require_processing_provenance(ds, config, start)
        ds.set_auto_mask(False)
        for source in config.grid.streams:
            for key, variable in (
                (f"{source}_unfiltered", f"aod_mean_{source}_unfiltered"),
                (f"{source}_filtered", f"aod_mean_{source}_filtered"),
                (f"{source}_counts", f"aod_count_{source}"),
                (f"{source}_support", f"aod_despeckle_support_fraction_{source}"),
                (f"{source}_flags", f"aod_despeckle_flag_{source}"),
            ):
                data = np.asarray(ds[variable][:], dtype=float)
                fields[key] = (
                    np.where(data >= 0, data, np.nan)
                    if not key.endswith(("unfiltered", "filtered"))
                    else np.where(data > -900, data, np.nan)
                )
    artifacts = []
    for category, kind, suffixes, quantity in (
        ("means", "aod", ("unfiltered", "filtered"), "AOD (550 nm)"),
        ("counts", "count", ("counts",), "Retrieval Count"),
        ("support", "fraction", ("support",), "Neighbor Support"),
        ("flags", "count", ("flags",), "Action Flag"),
    ):
        names = [f"{source}_{suffix}" for source in config.grid.streams for suffix in suffixes]
        labels = {
            f"{source}_{suffix}": f"{labeling.source_display_name(source)} {labeling.despeckle_label(suffix) if category == 'means' else ''}".strip()
            for source in config.grid.streams
            for suffix in suffixes
        }
        figure = maps.map_panels(
            [fields[name] for name in names],
            names,
            grid=load_grid(config.grid.source),
            kind=cast(maps.FieldKind, kind),
            scale=action_scale() if category == "flags" else None,
            quantity=quantity,
            source_labels=labels,
            region=region,
            columns=2,
            start=start,
            end=start,
            cartopy_data_dir=cartopy_data_dir(config),
            coastline_resolution=config.plots.coastline_resolution,
        )
        _sampling_note(figure, labeling.despeckle_window_note(category) + f" | {start:%H:%M} UTC")
        getattr(figure, "_sarb_plot_metadata")["comparison_provenance"] = {
            "grid_file": str(path),
            "window_start": start.isoformat(),
            "filter": config.despeckle.model_dump(mode="json"),
        }
        name = f"window_{start:%Y%m%dT%H%M}_{region.key}_{category}"
        try:
            artifacts.append(
                save_figure(
                    config, figure, suite=SUITE, name=name, start=start.date(), end=start.date()
                )
            )
        finally:
            plt.close(figure)
    return artifacts
