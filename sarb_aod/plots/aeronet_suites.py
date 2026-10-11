"""AERONET validation suites with independent sampling and shared statistics.

Suite composition follows NCAR/DAVINCI's plotting practice (Apache-2.0).
Regional series use CERES-SARB-CAM7's site-day then equal-site method,
with independent daily satellite and AERONET means paired at the same sites.
No reference repository is imported. Inputs are validated before rendering.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import xarray as xr
from matplotlib.figure import Figure
from netCDF4 import Dataset

from sarb_aod.aeronet.fetch import read_cached_day
from sarb_aod.aeronet.spectral import convert_550nm, window_uncertainty
from sarb_aod.config.schema import PlotSiteSelection, RunConfig
from sarb_aod.contract import validate_contract_file
from sarb_aod.evaluation.collocate import nearest_cell
from sarb_aod.evaluation.regions import get_region, region_mask
from sarb_aod.evaluation.report import (
    EvaluationReport,
    build_report,
    load_matchups,
    matchup_signature,
)
from sarb_aod.grid.spec import load_grid
from sarb_aod.paths import cartopy_data_dir, output_path
from sarb_aod.pipeline import processing_sha256, require_processing_provenance
from sarb_aod.plots import labeling, maps, regional_summary, scatter, series, summary
from sarb_aod.plots.protocol import PlotProtocolError
from sarb_aod.plots.regional_inset import add_regional_inset
from sarb_aod.plots.series import TimedValues
from sarb_aod.plots.style import NCAR_PALETTE, ncar_style
from sarb_aod.plots.suites import PlotArtifact, plot_regions, save_figure, write_protocol_report
from sarb_aod.windows import window_bounds

Level = Literal["grid", "l2"]
AERONET_SUITES = frozenset(
    {
        "aeronet_site_bias",
        "aeronet_site_timeseries",
        "aeronet_regional_timeseries",
        "aeronet_scatter",
        "evaluation_summary",
    }
)


@dataclass(frozen=True)
class GridSeries:
    """All grid windows at sites and daily complete-area regional means."""

    sites: pd.DataFrame  # site, time, source, aod, sigma
    regional: pd.DataFrame  # day, region, source, aod


@dataclass(frozen=True)
class SuiteInputs:
    matchups: Mapping[Level, pd.DataFrame]
    aeronet: pd.DataFrame | None = None
    grid: GridSeries | None = None
    daily_grid: pd.DataFrame | None = None


def select_sites(matchups: pd.DataFrame, selection: PlotSiteSelection | list[str]) -> list[str]:
    """Most grid matchups first; equal counts sort by exact site name."""
    available = set(matchups["site"].dropna().astype(str))
    if isinstance(selection, list):
        missing = set(selection) - available
        if missing:
            raise ValueError(f"Selected sites have no grid matchups: {sorted(missing)}")
        return selection.copy()
    counts = matchups.groupby("site", observed=True).size().rename("count").reset_index()
    return (
        counts.sort_values(["count", "site"], ascending=[False, True])["site"]
        .head(selection.n)
        .tolist()
    )


def subset_region(frame: pd.DataFrame, region: str) -> pd.DataFrame:
    times = pd.to_datetime(frame["time"], utc=True).dt.tz_convert(None).to_numpy()
    mask = region_mask(region, frame["longitude"], frame["latitude"], times=times)
    return frame.loc[mask].copy()


def _sources(config: RunConfig) -> dict[str, list[str]]:
    catalog = config.load_catalog()
    sources: dict[str, list[str]] = {}
    for stream, selected in config.grid.streams.items():
        sensor = catalog.product(selected.product).instrument.lower()
        sources.setdefault(sensor, []).append(stream)
    return sources


def _product_source(config: RunConfig) -> str:
    return " + ".join(labeling.source_display_name(sensor) for sensor in _sources(config))


def _source_label(config: RunConfig, source: str) -> str:
    return _product_source(config) if source == "product" else source


def _rho_compatible(config: RunConfig, other: RunConfig) -> bool:
    adjusted = other.model_copy(
        update={"weighting": other.weighting.model_copy(update={"rho": config.weighting.rho})}
    )
    return matchup_signature(config) == matchup_signature(adjusted)


def load_grid_series(config: RunConfig, sites: pd.DataFrame, regions: Sequence[str]) -> GridSeries:
    """Read every window, including windows lacking AERONET observations.

    Counts weight each cell's daily field before its cosine-latitude area
    average. Site samples use the same periodic nearest-cell rule as pairing.
    """
    grid = load_grid(config.grid.source)
    sensors = _sources(config)
    selected = (
        ["product"]
        if config.plots.site_sources == "product"
        else list(sensors) if config.plots.site_sources == "sensor" else list(config.grid.streams)
    )
    names = list(dict.fromkeys(["product", *selected]))
    locations = (
        sites[["site", "latitude", "longitude"]].drop_duplicates("site").reset_index(drop=True)
    )
    ilat, ilon = nearest_cell(grid, locations["latitude"], locations["longitude"])
    site_frames, regional_rows = [], []
    for day in config.period.days():
        totals = {name: np.zeros(grid.shape) for name in names}
        counts = {name: np.zeros(grid.shape) for name in names}
        for index in range(8):
            start, _, center = window_bounds(day, index)
            path = output_path(config, start)
            valid, errors = validate_contract_file(path)
            if not valid:
                raise ValueError(f"Invalid AERONET plot grid input {path}: {errors}")
            with Dataset(path) as dataset:
                require_processing_provenance(dataset, config, start)
                dataset.set_auto_mask(False)

                def read(name: str) -> np.ndarray:
                    data = np.asarray(dataset[name][:], dtype=float)
                    if data.shape != grid.shape:
                        raise ValueError(f"Unexpected grid shape for {name} in {path}")
                    return data

                for name in names:
                    if name == "product":
                        values, count = read("aod_weighted_mean"), read("aod_count")
                        sigma = read("aod_uncertainty")
                    elif name in sensors:
                        values = read(f"aod_weighted_mean_{name}")
                        count = np.sum(
                            [read(f"aod_count_{stream}") for stream in sensors[name]], axis=0
                        )
                        sigma = np.full(grid.shape, np.nan)
                    else:
                        values, count = read(f"aod_mean_{name}"), read(f"aod_count_{name}")
                        sigma = np.full(grid.shape, np.nan)
                    good = np.isfinite(values) & (values > -900) & np.isfinite(count) & (count > 0)
                    values = np.where(good, values, np.nan)
                    totals[name] += np.where(good, values * count, 0)
                    counts[name] += np.where(good, count, 0)
                    site_frames.append(
                        pd.DataFrame(
                            {
                                "site": locations["site"],
                                "time": pd.Timestamp(center, tz="UTC"),
                                "source": name,
                                "aod": values[ilat, ilon],
                                "count": np.where(good, count, 0)[ilat, ilon],
                                "sigma": np.where(sigma > -900, sigma, np.nan)[ilat, ilon],
                            }
                        )
                    )
        for name in names:
            daily = np.full(grid.shape, np.nan)
            np.divide(totals[name], counts[name], out=daily, where=counts[name] > 0)
            for region in regions:
                # Date-limited regions must also constrain the independent field.
                lon, lat = np.meshgrid(grid.lon, grid.lat)
                mask = region_mask(region, lon, lat, times=np.datetime64(day))
                mean = maps.area_mean(np.where(mask, daily, np.nan), grid.lat, grid.lon)
                regional_rows.append({"day": day, "region": region, "source": name, "aod": mean})
    return GridSeries(pd.concat(site_frames, ignore_index=True), pd.DataFrame(regional_rows))


def aeronet_window_series(points: pd.DataFrame, config: RunConfig) -> pd.DataFrame:
    """Independent half-open window means, with the configured minimum count."""
    values = points.copy()
    values["window"] = pd.to_datetime(values["time"], utc=True).dt.floor("3h")
    rows = []
    for window, group in values.groupby("window", observed=True):
        finite = group.loc[np.isfinite(group["aod_550nm"]), "aod_550nm"].to_numpy()
        if len(finite) >= config.evaluation.min_aeronet_grid:
            rows.append(
                {
                    "time": window + pd.Timedelta(minutes=90),
                    "aod": float(finite.mean()),
                    "sigma": window_uncertainty(
                        finite, instrument_sigma=config.aeronet.instrument_sigma
                    ),
                }
            )
    return pd.DataFrame(rows, columns=["time", "aod", "sigma"])


def _series_grid_pairs(
    config: RunConfig, aeronet: pd.DataFrame, gridded: GridSeries
) -> pd.DataFrame:
    """Independent grid window pairs for series context during L2 evaluation."""
    pieces = []
    for site, points in aeronet.groupby("AERONET_Site", observed=True):
        windows = aeronet_window_series(points, config).rename(
            columns={"aod": "x", "sigma": "sigma_x"}
        )
        windows["site"] = str(site)
        windows["latitude"] = float(points["Site_Latitude(Degrees)"].iloc[0])
        windows["longitude"] = float(points["Site_Longitude(Degrees)"].iloc[0])
        pieces.append(windows)
    windows = (
        pd.concat(pieces, ignore_index=True)
        if pieces
        else pd.DataFrame(columns=["site", "time", "x", "sigma_x", "latitude", "longitude"])
    )
    windows["time"] = pd.to_datetime(windows["time"], utc=True)
    product = gridded.sites.loc[gridded.sites["source"] == "product"].rename(
        columns={"aod": "y", "sigma": "sigma_y"}
    )
    paired = windows.merge(product, on=["site", "time"], how="inner")
    return paired.loc[
        np.isfinite(paired["x"].to_numpy(dtype=float))
        & np.isfinite(paired["y"].to_numpy(dtype=float))
    ].reset_index(drop=True)


def _timed(frame: pd.DataFrame, value: str, sigma: str | None = None) -> TimedValues:
    return TimedValues(
        frame["time"].to_numpy(),
        frame[value].to_numpy(),
        None if sigma is None else frame[sigma].to_numpy(),
    )


def site_figure(config: RunConfig, site: str, inputs: SuiteInputs) -> Figure:
    assert inputs.aeronet is not None and inputs.grid is not None
    points = inputs.aeronet.loc[inputs.aeronet["AERONET_Site"] == site]
    windows = aeronet_window_series(points, config)
    gridded = inputs.grid.sites.loc[inputs.grid.sites["site"] == site]
    product = gridded.loc[gridded["source"] == "product"]
    l2 = inputs.matchups.get("l2", pd.DataFrame(columns=["site", "time", "y"]))
    passes = l2.loc[l2["site"] == site]
    figure = series.site_time_series(
        site,
        aeronet_points=_timed(points, "aod_550nm"),
        aeronet_windows=_timed(windows, "aod", "sigma"),
        grid=_timed(product, "aod", "sigma"),
        overpasses=_timed(passes, "y") if not passes.empty else None,
        source=_product_source(config),
        start=config.period.start,
        end=config.period.end,
    )
    axis = figure.axes[0]
    for index, (name, group) in enumerate(
        gridded.loc[gridded["source"] != "product"].groupby("source", sort=False)
    ):
        axis.plot(
            pd.to_datetime(group["time"], utc=True).dt.tz_convert(None),
            group["aod"],
            color=NCAR_PALETTE[index % len(NCAR_PALETTE)],
            label=labeling.series_legend_label(str(name), "grid"),
            linewidth=1.1,
        )
    axis.legend(loc="upper left", fontsize=8, ncols=2)
    return figure


def _regional_sources(config: RunConfig) -> list[str]:
    """Extra comparisons requested explicitly, omitting the sole-sensor duplicate."""
    if config.plots.site_sources == "product":
        return []
    if config.plots.site_sources == "sensor":
        sensors = list(_sources(config))
        return sensors if len(sensors) > 1 else []
    return list(config.grid.streams)


def regional_figure(
    config: RunConfig, region: str, inputs: SuiteInputs, *, source: str = "product"
) -> Figure:
    if inputs.daily_grid is None:
        raise ValueError("Regional time series require independently paired daily grid means")
    daily_grid = inputs.daily_grid
    if "pairing" in daily_grid and not daily_grid["pairing"].eq("daily").all():
        raise ValueError("Regional time series require daily pairing, not window matchups")
    days = pd.to_datetime(daily_grid["time"], utc=True)
    in_period = (days >= pd.Timestamp(config.period.start, tz="UTC")) & (
        days < pd.Timestamp(config.period.end, tz="UTC") + pd.Timedelta(days=1)
    )
    pairs = subset_region(daily_grid.loc[in_period], region).assign(region=region)
    if source != "product":
        if inputs.grid is None:
            raise ValueError("Regional component plots require grid samples")
        field = inputs.grid.sites.loc[inputs.grid.sites["source"] == source]
        field = field.loc[np.isfinite(field["aod"]) & (field["count"] > 0)].copy()
        field["day"] = pd.to_datetime(field["time"], utc=True).dt.floor("D")
        field["total"] = field["aod"] * field["count"]
        daily = field.groupby(["site", "day"], observed=True)[["total", "count"]].sum()
        daily["y"] = daily["total"] / daily["count"]
        pairs = (
            pairs.drop(columns="y")
            .assign(day=pd.to_datetime(pairs["time"], utc=True).dt.floor("D"))
            .merge(
                daily[["y"]].reset_index(),
                on=["site", "day"],
                how="left",
                validate="one_to_one",
            )
        )
    figure = series.regional_time_series(
        pairs,
        region,
        source=_source_label(config, source),
        start=config.period.start,
        end=config.period.end,
        min_samples=config.evaluation.min_samples,
    )
    metadata = getattr(figure, "_sarb_plot_metadata")
    axis = metadata["data_axes"][0]
    daily = metadata["regional_daily"]
    minimum = int(daily["active_sites"].min()) if not daily.empty else 0
    maximum = int(daily["active_sites"].max()) if not daily.empty else 0
    for subtitle in metadata["subtitles"]:
        subtitle.set_text(
            labeling.active_sites_subtitle(
                config.period.start,
                config.period.end,
                minimum,
                maximum,
                extent=get_region(region).extent,
            )
        )
    axis.legend(loc="upper left", fontsize=8, ncols=2)
    axis.set_xlim(
        pd.Timestamp(config.period.start) - pd.Timedelta(hours=2),
        pd.Timestamp(config.period.end) + pd.Timedelta(days=1),
    )
    # Calendar-aligned ticks can put September 29 beside October 1. Uniform
    # intervals avoid a crowded final tick when the configured month ends.
    locator = mdates.AutoDateLocator(minticks=4, maxticks=8, interval_multiples=False)
    axis.xaxis.set_major_locator(locator)
    axis.xaxis.set_major_formatter(mdates.AutoDateFormatter(locator))
    if config.plots.regional_layout == "summary":
        regional_summary.compose(
            figure,
            config,
            pairs,
            region,
            source=_source_label(config, source),
            input_provenance=daily_grid.attrs.get("source_provenance", {}),
        )
    else:
        add_regional_inset(figure, pairs, region, cartopy_data_dir=cartopy_data_dir(config))
    return figure


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    if not slug:
        raise ValueError(f"Cannot form a plot filename from {value!r}")
    return slug if slug == value else f"{slug}-{hashlib.sha256(value.encode()).hexdigest()[:12]}"


def _figures(
    config: RunConfig,
    suite: str,
    inputs: SuiteInputs,
    reports: Mapping[Level, EvaluationReport],
    comparisons: Sequence[tuple[RunConfig, Mapping[Level, EvaluationReport]]],
) -> Iterator[tuple[str, Figure]]:
    dates: dict[str, Any] = {"start": config.period.start, "end": config.period.end}
    source = _product_source(config)
    regions = [region or "global" for region in plot_regions(config)]
    if suite == "aeronet_site_timeseries":
        for site in select_sites(inputs.matchups["grid"], config.plots.sites):
            yield f"site_{_slug(site)}", site_figure(config, site, inputs)
        return
    if suite == "aeronet_regional_timeseries":
        for region in regions:
            yield f"region_{region}", regional_figure(config, region, inputs)
            for component in _regional_sources(config):
                yield f"region_{region}_{_slug(component)}", regional_figure(
                    config, region, inputs, source=component
                )
        return
    for level, report in reports.items():
        pairs = inputs.matchups[level]
        if suite == "aeronet_site_bias":
            for region in regions:
                regional = subset_region(pairs, region)
                stats = build_report(regional, config, level=level).tables["by_site"]
                positions = regional.groupby("site", observed=True)[
                    ["latitude", "longitude"]
                ].first()
                stats = stats.join(positions, on="site")
                for metric, kind in (("MB", "bias"), ("RMSE", "rmse"), ("R", "correlation")):
                    yield f"{level}_{region}_{metric.lower()}", maps.plot_site_map(
                        stats["longitude"],
                        stats["latitude"],
                        stats[metric],
                        stats["N"],
                        kind=cast(maps.FieldKind, kind),
                        source=source,
                        region=region,
                        cartopy_data_dir=cartopy_data_dir(config),
                        coastline_resolution=config.plots.coastline_resolution,
                        **dates,
                    )
        elif suite == "aeronet_scatter":
            yield f"{level}_global", scatter.density_scatter(
                pairs["x"], pairs["y"], source=source, envelope=None, **dates
            )
            panels = {
                region: (selected["x"], selected["y"])
                for region in regions
                if region != "global"
                for selected in [subset_region(pairs, region)]
            }
            if panels:
                yield f"{level}_regions", scatter.density_scatter_panels(
                    panels, source=source, **dates
                )
            if level == "l2":
                for stream, values in pairs.groupby("stream", observed=True, sort=True):
                    yield f"l2_stream_{_slug(str(stream))}", scatter.density_scatter(
                        values["x"], values["y"], source=str(stream), envelope=None, **dates
                    )
        elif suite == "evaluation_summary":
            yield f"{level}_binned_bias", summary.binned_bias(
                pairs["x"],
                pairs["y"],
                source=source,
                min_samples=config.evaluation.min_samples,
                **dates,
            )
            denominator = np.hypot(pairs["sigma_x"], pairs["sigma_y"])
            z = np.divide(
                pairs["y"] - pairs["x"],
                denominator,
                out=np.full(len(pairs), np.nan),
                where=denominator > 0,
            )
            yield f"{level}_z", summary.z_histogram(z, source=source, **dates)
            regional = report.tables["pooled_regions"].set_index("region").reindex(regions)
            yield f"{level}_scorecard", summary.scorecard(
                regional[["N", "MB", "RMSE", "R", "pct_ee"]], **dates
            )
            yield f"{level}_taylor", summary.taylor_diagram(
                pairs["x"], {source: pairs["y"]}, **dates
            )
            box = {
                region: {"aeronet": selected["x"], source: selected["y"]}
                for region in regions
                for selected in [subset_region(pairs, region)]
            }
            yield f"{level}_regional_box", summary.regional_boxplots(box, **dates)
            sweep = [
                (cfg.weighting.rho, result[level].tables["summary"].iloc[0])
                for cfg, result in [(config, reports), *comparisons]
                if cfg.weighting.method == "gls"
                and level in result
                and _rho_compatible(config, cfg)
            ]
            if len({rho for rho, _ in sweep}) > 1:
                yield f"{level}_rho_sweep", summary.rho_sweep(
                    [rho for rho, _ in sweep],
                    {
                        metric: [row[metric] for _, row in sweep]
                        for metric in ("MB", "RMSE", "R", "z_std")
                    },
                    source=source,
                    **dates,
                )


def render_aeronet_suites(
    config: RunConfig,
    *,
    compare_configs: Sequence[RunConfig] = (),
    levels: Sequence[Level] = ("grid", "l2"),
    inputs: SuiteInputs | None = None,
    pairing: Literal["default", "cam7", "daily"] | None = None,
    evaluation_level: Level | None = None,
) -> list[PlotArtifact]:
    """Render selected suites, failing explicitly on missing or stale inputs."""
    suites = [suite for suite in config.plots.suites if suite in AERONET_SUITES]
    if not suites:
        return []
    if not levels or len(set(levels)) != len(levels) or set(levels) - {"grid", "l2"}:
        raise ValueError("AERONET plot levels must contain grid and/or l2 exactly once")
    if evaluation_level is not None and evaluation_level not in levels:
        raise ValueError("Evaluation plot scope must be included in the requested levels")
    if (
        pairing not in {None, "default", "cam7", "daily"}
        or (pairing in {"cam7", "daily"} and "l2" in levels)
        or (pairing == "cam7" and inputs is None)
    ):
        raise ValueError(
            "Daily/CAM7 pairing requires grid-only levels; CAM7 needs supplied matchups"
        )
    regional_series = "aeronet_regional_timeseries" in suites
    only_regional = set(suites) == {"aeronet_regional_timeseries"}
    level_pairings: dict[Level, Literal["default", "cam7", "daily"]] = {}
    for level in dict.fromkeys([*levels, *(inputs.matchups if inputs is not None else [])]):
        if pairing is not None:
            level_pairings[level] = pairing
        elif inputs is None:
            level_pairings[level] = "daily" if level == "grid" else "default"
        else:
            frame = inputs.matchups.get(level)
            level_pairings[level] = (
                "daily"
                if level == "grid"
                and frame is not None
                and not frame.empty
                and "pairing" in frame
                and frame["pairing"].eq("daily").all()
                else "default"
            )
    candidates = (
        dict(inputs.matchups)
        if inputs is not None
        else (
            {}
            if only_regional
            else {
                level: load_matchups(config, level, pairing=level_pairings[level])
                for level in levels
            }
        )
    )
    frames = {
        level: frame.loc[
            np.isfinite(frame["x"].to_numpy(dtype=float))
            & np.isfinite(frame["y"].to_numpy(dtype=float))
        ].copy()
        for level, frame in candidates.items()
    }
    daily_grid = inputs.daily_grid if inputs is not None else None
    if regional_series and daily_grid is None:
        daily_grid = (
            frames["grid"]
            if level_pairings.get("grid") == "daily" and "grid" in frames
            else load_matchups(config, "grid", pairing="daily")
        )
    site_series = "aeronet_site_timeseries" in suites
    independent_needed = site_series
    grid_needed = independent_needed or (regional_series and bool(_regional_sources(config)))
    aeronet = inputs.aeronet if inputs is not None else None
    gridded = inputs.grid if inputs is not None else None
    if independent_needed and aeronet is None:
        raw = pd.concat(
            [read_cached_day(config, day) for day in config.period.days()], ignore_index=True
        )
        aeronet = convert_550nm(
            raw,
            method=config.aeronet.spectral,
            include_extrapolated=config.aeronet.include_extrapolated,
        )
    if grid_needed and gridded is None:
        locations = frames.get("grid") if site_series else daily_grid
        if locations is None:
            assert aeronet is not None
            locations = aeronet.rename(
                columns={
                    "AERONET_Site": "site",
                    "Site_Latitude(Degrees)": "latitude",
                    "Site_Longitude(Degrees)": "longitude",
                }
            )
            locations = locations.loc[
                np.isfinite(locations["latitude"])
                & np.isfinite(locations["longitude"])
                & locations["latitude"].between(-90, 90)
            ]
        if regional_series and daily_grid is not None:
            locations = pd.concat([locations, daily_grid], ignore_index=True)
        gridded = load_grid_series(config, locations, [])
    inputs = SuiteInputs(frames, aeronet, gridded, daily_grid)
    if site_series and "grid" not in frames:
        assert inputs.aeronet is not None and inputs.grid is not None
        frames["grid"] = _series_grid_pairs(config, inputs.aeronet, inputs.grid)
        inputs = SuiteInputs(frames, inputs.aeronet, inputs.grid, daily_grid)
    reports = {
        level: build_report(frame, config, level=level, pairing=level_pairings[level])
        for level, frame in candidates.items()
    }
    comparisons = []
    for other in compare_configs:
        if other.period != config.period:
            raise ValueError("AERONET plot comparisons require identical configured periods")
        comparisons.append(
            (
                other,
                {
                    level: build_report(
                        load_matchups(other, level, pairing=level_pairings[level]),
                        other,
                        level=level,
                        pairing=level_pairings[level],
                    )
                    for level in levels
                },
            )
        )
    artifacts: list[PlotArtifact] = []
    with ncar_style(config.plots.context):
        for suite in suites:
            expected = []
            output_suite = suite if evaluation_level is None else f"{suite}_{evaluation_level}"
            if suite == "aeronet_regional_timeseries" and config.plots.regional_layout == "summary":
                output_suite = output_suite.replace(
                    "aeronet_regional_timeseries", "aeronet_regional_summary"
                )
            if pairing not in {None, "default"}:
                output_suite = f"{output_suite}_{pairing}"
            try:
                for name, figure in _figures(config, suite, inputs, reports, comparisons):
                    figure_pairing = "default"
                    if suite == "aeronet_regional_timeseries":
                        figure_pairing = "daily"
                    elif name.startswith("grid_"):
                        figure_pairing = level_pairings["grid"]
                    elif name.startswith("l2_"):
                        figure_pairing = level_pairings["l2"]
                    getattr(figure, "_sarb_plot_metadata")["pairing"] = figure_pairing
                    if pairing not in {None, "default"}:
                        name = f"{pairing}_{name}"
                    elif pairing is None and name.startswith("grid_") and figure_pairing == "daily":
                        name = f"daily_{name}"
                    key = f"{name}.{config.period.start:%Y%m%d}-{config.period.end:%Y%m%d}"
                    if key in expected:
                        plt.close(figure)
                        raise ValueError(f"Duplicate figure name in {output_suite}: {name}")
                    expected.append(key)
                    try:
                        artifacts.append(
                            save_figure(
                                config,
                                figure,
                                suite=output_suite,
                                name=name,
                                start=config.period.start,
                                end=config.period.end,
                            )
                        )
                    finally:
                        plt.close(figure)
                if not expected:
                    raise ValueError(
                        f"Suite {suite} produced no figures; inspect matchup/site selection"
                    )
            except PlotProtocolError as exc:
                write_protocol_report(config, output_suite, artifacts, failure=exc.report)
                raise
            except Exception as exc:
                write_protocol_report(
                    config,
                    output_suite,
                    artifacts,
                    failure={"figures": [], "checks": {}, "failures": [str(exc)]},
                )
                raise
            protocol_path = write_protocol_report(
                config, output_suite, artifacts, expected_figures=expected
            )
            protocol = json.loads(protocol_path.read_text())
            if not protocol["passed"]:
                raise PlotProtocolError(protocol)
    return artifacts
