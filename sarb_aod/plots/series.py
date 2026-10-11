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
from matplotlib.transforms import ScaledTranslation, blended_transform_factory
from numpy.typing import ArrayLike

from sarb_aod.evaluation.calculator import quick_stats, regional_daily_series
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
    axes_heading = kind == "regional_time_series"
    heading = (
        axes[0].set_title(title, loc="left", pad=6) if axes_heading else figure.suptitle(title)
    )
    subtitle = labeling.subtitle_text(start, end)
    subtitle_artists = []
    if subtitle:
        if axes_heading:
            subtitle_artists.append(
                axes[0].text(
                    1,
                    1,
                    subtitle,
                    transform=axes[0].transAxes
                    + ScaledTranslation(0, 6 / 72, figure.dpi_scale_trans),
                    ha="right",
                    va="baseline",
                    fontsize=10,
                )
            )
        else:
            # Figure-owned subtitles reserve space above the axes.
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
    min_samples: int = 3,
) -> Figure:
    """Paired daily regional means, with the middle 50% of site-day means.

    Independent daily means are paired by site-day; sites then receive equal
    weight. Both bands use those same paired sites. A single site
    has no displayed spread, and missing days break both lines and bands.
    Statistics compare the two regional daily means, not the pooled windows.
    """
    selected = pairs.loc[pairs["region"] == region]
    dates = pd.to_datetime(selected["time"], utc=True)
    visible = np.ones(len(selected), dtype=bool)
    if start is not None:
        visible &= (dates >= pd.to_datetime(start, utc=True).floor("D")).to_numpy()
    if end is not None:
        visible &= (
            dates < pd.to_datetime(end, utc=True).floor("D") + pd.Timedelta(days=1)
        ).to_numpy()
    selected = selected.loc[visible]
    site_daily, regional = regional_daily_series(selected, x_var=x_var, y_var=y_var)
    spread = regional[["day", "active_sites"]].copy()
    for name in (x_var, y_var):
        if site_daily.empty:
            spread[f"{name}_lower"] = np.nan
            spread[f"{name}_upper"] = np.nan
        else:
            quantiles = site_daily.groupby("day")[name].quantile([0.25, 0.75]).unstack()
            spread[f"{name}_lower"] = spread["day"].map(quantiles[0.25])
            spread[f"{name}_upper"] = spread["day"].map(quantiles[0.75])
        spread.loc[spread["active_sites"] < 2, [f"{name}_lower", f"{name}_upper"]] = np.nan
    daily = regional.merge(spread, on=["day", "active_sites"]).set_index("day")
    first = pd.to_datetime(start, utc=True) if start is not None else daily.index.min()
    last = pd.to_datetime(end, utc=True) if end is not None else daily.index.max()
    if pd.notna(first) and pd.notna(last):
        daily = daily.reindex(pd.date_range(first.floor("D"), last.floor("D"), freq="D"))
    figure, axis = _new_figure(figsize=(10, 5.8))
    times = pd.to_datetime(daily.index, utc=True).tz_localize(None).to_numpy()
    dense: list[Artist] = []
    for name, src, color in ((x_var, "aeronet", X_COLOR), (y_var, source, Y_COLOR)):
        lower = daily[f"{name}_lower"].to_numpy(dtype=float)
        upper = daily[f"{name}_upper"].to_numpy(dtype=float)
        band_times = times
        valid = np.isfinite(lower) & np.isfinite(upper)
        if valid.any():
            isolated = valid & ~np.r_[False, valid[:-1]] & ~np.r_[valid[1:], False]
            if isolated.any():
                # Give isolated site-day ranges visible width without joining
                # them across missing or single-site days.
                half_width = np.timedelta64(6, "h")
                band_times = np.concatenate(
                    [times, times[isolated] - half_width, times[isolated] + half_width]
                )
                order = np.argsort(band_times)
                band_times = band_times[order]
                lower = np.concatenate([lower, lower[isolated], lower[isolated]])[order]
                upper = np.concatenate([upper, upper[isolated], upper[isolated]])[order]
        dense.append(
            axis.fill_between(
                band_times,
                lower,
                upper,
                color=color,
                alpha=0.22,
                linewidth=0,
                rasterized=True,
            )
        )
        axis.plot(
            times,
            daily[name].to_numpy(dtype=float),
            marker="o",
            markersize=3,
            color=color,
            label=labeling.legend_label(src),
        )
    inferred_start, inferred_end = _date_axes(axis, [times])
    title, _ = labeling.title_for_labeled_subset("AOD 550 nm", region)
    axis.set_ylabel(labeling.axis_label("AOD 550 nm", "1"))
    axis.margins(y=0.12)
    axis.xaxis.set_label_coords(0.5, 0.015)
    axis.xaxis.label.set_verticalalignment("bottom")
    axis.tick_params(axis="x", rotation=20)
    plt.setp(axis.get_xticklabels(), ha="right")
    stats = quick_stats(
        regional[x_var].to_numpy(dtype=float),
        regional[y_var].to_numpy(dtype=float),
        ["N", "MB", "RMSE", "R", "NME"],
        min_samples=min_samples,
    )
    annotations = [
        figure.text(
            0,
            0.065,
            labeling.regional_statistics_text(stats),
            transform=blended_transform_factory(axis.transAxes, figure.transFigure),
            ha="left",
            va="center",
            fontsize=10,
        ),
        figure.text(
            0,
            0.02,
            labeling.regional_spread_note(),
            transform=blended_transform_factory(axis.transAxes, figure.transFigure),
            ha="left",
            va="bottom",
            fontsize=9,
        ),
    ]
    if not stats["N"]:
        axis.text(
            0.5,
            0.5,
            labeling.reference_label("no_data"),
            transform=axis.transAxes,
            ha="center",
            va="center",
        )
    _finish(
        figure,
        [axis],
        kind="regional_time_series",
        title=title,
        start=start if start is not None else inferred_start,
        end=end if end is not None else inferred_end,
        dense=dense,
        date_axes=[axis],
        site_daily=site_daily,
        regional_daily=regional,
        regional_spread=spread,
        metrics=stats,
        statistics_annotations=annotations,
    )
    figure.set_layout_engine("constrained", rect=(0, 0.11, 1, 0.89))
    return figure
