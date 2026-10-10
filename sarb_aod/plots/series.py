"""AOD time series following NCAR/DAVINCI timeseries renderer conventions.

Adapted from DAVINCI origin/develop plots/renderers/timeseries.py (Apache-2.0).
Regional aggregation delegates to the shared evaluation calculator; site
series accept complete independent inputs so unmatched grid windows survive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.artist import Artist
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from numpy.typing import ArrayLike

from sarb_aod.evaluation.calculator import regional_daily_series
from sarb_aod.plots import labeling
from sarb_aod.plots.style import NCAR_ACCENT, X_COLOR, Y_COLOR, apply_ncar_style


@dataclass(frozen=True)
class TimedValues:
    """A time vector (UTC datetimes or Unix seconds) with physical values."""

    time: ArrayLike
    values: ArrayLike
    sigma: ArrayLike | None = None


def _timed(values: TimedValues) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    raw_time = np.asarray(values.time)
    if raw_time.ndim != 1:
        raise ValueError("series times must be a vector")
    times = (
        pd.to_datetime(
            raw_time, unit="s" if raw_time.dtype.kind in "iuf" else None, utc=True, errors="coerce"
        )
        .tz_localize(None)
        .to_numpy()
    )
    data = np.asarray(values.values, dtype=float)
    if times.ndim != 1 or data.shape != times.shape:
        raise ValueError("series times and values must be equal-length vectors")
    sigma = None if values.sigma is None else np.asarray(values.sigma, dtype=float)
    if sigma is not None and sigma.shape != times.shape:
        raise ValueError("series uncertainty must match the time vector")
    order = np.argsort(times)
    order = order[~np.isnat(times[order])]
    return times[order], data[order], None if sigma is None else sigma[order]


def _new_figure(*, figsize: tuple[float, float] = (8.0, 5.0), **kwargs: Any) -> tuple[Figure, Any]:
    apply_ncar_style("publication")
    return plt.subplots(figsize=figsize, layout="constrained", **kwargs)


def _finish(
    figure: Figure,
    axes: list[Axes],
    *,
    kind: str,
    title: str,
    start: Any = None,
    end: Any = None,
    dense: list[Artist] | None = None,
    **metadata: Any,
) -> Figure:
    labeling.validate_title(title)
    heading = figure.suptitle(title)
    subtitle = labeling.subtitle_text(start, end)
    subtitle_artists = []
    if subtitle:
        # A figure-owned subtitle has reserved space instead of overlapping an
        # axes title or being clipped by a tight save.
        subtitle_artists.append(
            figure.text(0.5, 0.91, subtitle, ha="center", va="top", fontsize=10)
        )
        figure.set_layout_engine("constrained", rect=(0, 0, 1, 0.87))
    dense = dense or []
    for artist in dense:
        artist.set_rasterized(True)
    record = {
        "kind": kind,
        "plot_type": kind,
        "data_axes": axes,
        "dense_artists": dense,
        "field_artists": [],
        "absolute_aod_artists": [],
        "colorbars": [],
        "titles": [heading],
        "subtitles": subtitle_artists,
        "gridliners": [],
        **metadata,
    }
    setattr(figure, "_sarb_plot_metadata", record)
    for name in ("_sarb_dense_artists", "_davinci_dense_artists"):
        setattr(figure, name, dense)
    setattr(figure, "_sarb_absolute_aod_artists", [])
    setattr(figure, "_davinci_absolute_aod_artists", [])
    return figure


def _date_axes(axis: Axes, times: list[np.ndarray]) -> tuple[Any, Any]:
    nonempty = [time for time in times if time.size]
    start = min((time.min() for time in nonempty), default=None)
    end = max((time.max() for time in nonempty), default=None)
    if start is not None and end is not None and end > start:
        axis.set_xlim(start, end)
    axis.set_xlabel(labeling.axis_label("Time", None))
    axis.set_ylabel(labeling.axis_label("AOD", "1"))
    axis.tick_params(axis="x", rotation=45)
    axis.legend(loc="upper left", fontsize=8, ncols=2)
    return start, end


def site_time_series(
    site: str,
    *,
    aeronet_points: TimedValues,
    aeronet_windows: TimedValues,
    grid: TimedValues,
    overpasses: TimedValues | None = None,
    source: str = "modis",
    start: Any = None,
    end: Any = None,
) -> Figure:
    """Plot all AERONET points, window means, and every available grid window."""
    figure, axis = _new_figure(figsize=(10, 5))
    dense: list[Artist] = []
    time_arrays = []
    for values, src, role, color in (
        (aeronet_points, "aeronet", "all_points", X_COLOR),
        (aeronet_windows, "aeronet", "window_means", X_COLOR),
        (grid, source, "grid", Y_COLOR),
        (overpasses, source, "overpasses", NCAR_ACCENT),
    ):
        if values is None:
            continue
        times, data, sigma = _timed(values)
        time_arrays.append(times)
        band = (
            sigma is not None
            and role in {"window_means", "grid"}
            and bool(np.any(np.isfinite(data) & np.isfinite(sigma) & (sigma >= 0)))
        )
        label = labeling.series_legend_label(src, role, band_drawn=band)
        if role in {"all_points", "overpasses"}:
            dense.append(
                axis.scatter(
                    times,
                    data,
                    color=color,
                    s=9 if role == "all_points" else 20,
                    alpha=0.25 if role == "all_points" else 0.8,
                    marker="o" if role == "all_points" else "D",
                    label=label,
                    rasterized=True,
                )
            )
        else:
            axis.plot(
                times,
                data,
                color=color,
                linewidth=2 if role == "window_means" else 1.5,
                marker="o",
                markersize=3,
                label=label,
            )
        if band:
            assert sigma is not None
            valid = np.isfinite(data) & np.isfinite(sigma) & (sigma >= 0)
            dense.append(
                axis.fill_between(
                    times,
                    data - sigma,
                    data + sigma,
                    where=valid,
                    color=color,
                    alpha=0.18,
                    linewidth=0,
                    rasterized=True,
                )
            )
    inferred_start, inferred_end = _date_axes(axis, time_arrays)
    title, _ = labeling.title_for_labeled_subset("AOD", site)
    return _finish(
        figure,
        [axis],
        kind="site_time_series",
        title=title,
        start=start if start is not None else inferred_start,
        end=end if end is not None else inferred_end,
        dense=dense,
        date_axes=[axis],
    )


def regional_time_series(
    pairs: pd.DataFrame,
    region: str,
    *,
    source: str = "modis",
    x_var: str = "x",
    y_var: str = "y",
    start: Any = None,
    end: Any = None,
) -> Figure:
    """Equal-site daily means: site-day pairs first, then active-site averages."""
    selected = pairs.loc[pairs["region"] == region]
    site_daily, regional = regional_daily_series(selected, x_var=x_var, y_var=y_var)
    figure, axis = _new_figure(figsize=(10, 5))
    times = pd.to_datetime(regional["day"], utc=True).dt.tz_localize(None).to_numpy()
    for name, src, color in ((x_var, "aeronet", X_COLOR), (y_var, source, Y_COLOR)):
        axis.plot(
            times,
            regional[name].to_numpy(),
            marker="o",
            markersize=3,
            color=color,
            label=labeling.legend_label(src),
        )
    inferred_start, inferred_end = _date_axes(axis, [times])
    title, _ = labeling.title_for_labeled_subset("AOD", region)
    return _finish(
        figure,
        [axis],
        kind="regional_time_series",
        title=title,
        start=start if start is not None else inferred_start,
        end=end if end is not None else inferred_end,
        date_axes=[axis],
        site_daily=site_daily,
        regional_daily=regional,
    )
