"""Configured CAM7 map suites and protocol-gated PDF/PNG publication.

Period means follow NCAR/CERES-SARB-CAM7's count-weighted monthly
analysis convention, specified in the implementation plan. Rendering and protocol
conventions follow NCAR/DAVINCI ``plots/suites`` (Apache-2.0); neither project
is imported. All eight UTC windows are required for every configured day.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.figure import Figure
from netCDF4 import Dataset

from sarb_aod.config.schema import AlgorithmFamily, RunConfig
from sarb_aod.contract import git_commit, validate_contract_file
from sarb_aod.evaluation.regions import REGIONS
from sarb_aod.grid.spec import GridSpec, load_grid
from sarb_aod.paths import cartopy_data_dir, output_path, plot_path
from sarb_aod.pipeline import config_sha256, processing_sha256, require_processing_provenance
from sarb_aod.plots import labeling, maps
from sarb_aod.plots.protocol import PROTOCOL, PlotProtocolError, validate_rendered_figures
from sarb_aod.plots.style import geosit_aod_levels, ncar_style
from sarb_aod.qc import resolve_rule
from sarb_aod.windows import TIMES_PER_DAY, window_bounds


@dataclass(frozen=True)
class MapAggregate:
    """Unrounded period summaries; no observations are NaN except count/coverage.

    ``means`` contains product, sensor and stream fields in display order.
    Sensor means use the sum of their accepted stream counts. A stream count
    can exist without an estimable mean; those counts do not weight missing
    means. Negative fractions use all accepted stream counts, including
    observations whose uncertainty is missing, matching the diagnostic count.
    """

    grid: GridSpec
    means: dict[str, np.ndarray]
    uncertainty: np.ndarray
    count: np.ndarray
    observation_fraction: np.ndarray
    chi2: np.ndarray
    negative_fraction: np.ndarray
    daily_means: dict[date, np.ndarray]
    windows: int
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MapFigureSpec:
    suite: str
    name: str
    start: date
    end: date
    field: str
    region: str | None = None
    comparison: int | None = None

    @property
    def key(self) -> str:
        return f"{self.name}.{self.start:%Y%m%d}-{self.end:%Y%m%d}"


@dataclass(frozen=True)
class PlotArtifact:
    suite: str
    name: str
    start: date
    end: date
    pdf: Path
    png: Path
    report: dict[str, Any] = field(repr=False)


class _WeightedMean:
    def __init__(self, shape: tuple[int, ...]) -> None:
        self.total = np.zeros(shape, dtype=np.float64)
        self.weight = np.zeros(shape, dtype=np.float64)

    def add(self, values: np.ndarray, count: np.ndarray) -> None:
        good = np.isfinite(values) & (values > -900) & np.isfinite(count) & (count > 0)
        self.total[good] += values[good] * count[good]
        self.weight[good] += count[good]

    def mean(self) -> np.ndarray:
        return _ratio(self.total, self.weight)


def _ratio(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    result = np.full(numerator.shape, np.nan, dtype=np.float64)
    np.divide(numerator, denominator, out=result, where=denominator > 0)
    return result


def _sensor_streams(config: RunConfig) -> dict[str, list[str]]:
    catalog = config.load_catalog()
    result: dict[str, list[str]] = {}
    for name, selection in config.grid.streams.items():
        sensor = catalog.product(selection.product).instrument.lower()
        result.setdefault(sensor, []).append(name)
    if not result:
        raise ValueError("plotting requires grid.streams to identify the input product")
    return result


def aggregate_maps(config: RunConfig) -> MapAggregate:
    """Read and validate each required CAM7 file, then aggregate cell by cell.

    Weighting here is by observation count, even when individual windows were
    produced by GLS. Fractions divide by the complete configured window count,
    so a missing input file is an error rather than apparent improved coverage.
    """
    grid = load_grid(config.grid.source)
    sensors = _sensor_streams(config)
    means = {
        name: _WeightedMean(grid.shape) for name in ("product", *sensors, *config.grid.streams)
    }
    uncertainty, chi2 = _WeightedMean(grid.shape), _WeightedMean(grid.shape)
    counts = np.zeros(grid.shape, dtype=np.float64)
    observed = np.zeros(grid.shape, dtype=np.float64)
    negative, accepted = np.zeros(grid.shape), np.zeros(grid.shape)
    daily_means = {}
    windows = 0
    created, revisions, grid_files = set(), set(), []
    for day in config.period.days():
        daily = _WeightedMean(grid.shape)
        for index in range(TIMES_PER_DAY):
            start = window_bounds(day, index)[0]
            path = output_path(config, start)
            valid, errors = validate_contract_file(path)
            if not valid:
                raise ValueError(f"Invalid map input {path}: {'; '.join(errors)}")
            with Dataset(path) as dataset:
                require_processing_provenance(dataset, config, start)
                created.add(str(dataset.date_created))
                revisions.add(str(dataset.git_commit))
                grid_files.append(str(path))
                dataset.set_auto_mask(False)

                def read(name: str) -> np.ndarray:
                    if name not in dataset.variables:
                        raise ValueError(f"Map input {path} is missing {name}; rerun grid")
                    values = np.asarray(dataset[name][:], dtype=np.float64)
                    if values.shape != grid.shape:
                        raise ValueError(f"Map input {path}: {name} has an invalid grid shape")
                    return values

                product, count = read("aod_weighted_mean"), read("aod_count")
                good = np.isfinite(product) & (product > -900) & (count > 0)
                count = np.where(good, count, 0)
                counts += count
                observed += good
                means["product"].add(product, count)
                daily.add(product, count)
                uncertainty.add(read("aod_uncertainty"), count)
                chi2.add(read("aod_chi2_per_dof"), count)
                stream_counts = {}
                for stream in config.grid.streams:
                    raw_count = read(f"aod_count_{stream}")
                    if np.any(~np.isfinite(raw_count) | (raw_count < 0)):
                        raise ValueError(f"Map input {path}: invalid count for {stream}")
                    stream_counts[stream] = raw_count
                    means[stream].add(read(f"aod_mean_{stream}"), raw_count)
                total_accepted = np.sum(list(stream_counts.values()), axis=0)
                negative_count = read("aod_count_negative")
                if np.any(
                    ~np.isfinite(negative_count)
                    | (negative_count < 0)
                    | (negative_count > total_accepted)
                ):
                    raise ValueError(f"Map input {path}: invalid negative AOD count")
                accepted += total_accepted
                negative += negative_count
                for sensor, streams in sensors.items():
                    sensor_count = np.sum([stream_counts[name] for name in streams], axis=0)
                    means[sensor].add(read(f"aod_weighted_mean_{sensor}"), sensor_count)
            windows += 1
        daily_means[day] = daily.mean()
    return MapAggregate(
        grid,
        {name: item.mean() for name, item in means.items()},
        uncertainty.mean(),
        counts,
        observed / windows,
        chi2.mean(),
        _ratio(negative, accepted),
        daily_means,
        windows,
        {
            "grid_created_utc": sorted(created),
            "processing_commits": sorted(revisions),
            "grid_files": grid_files,
        },
    )


def plot_regions(config: RunConfig) -> list[str | None]:
    """Global plus configured regional maps, with inactive seasonal boxes omitted."""
    chosen = config.plots.regions
    if chosen == "all":
        chosen = [
            key
            for key, region in REGIONS.items()
            if key != "global"
            and (
                region.start_date is None
                or (
                    region.start_date <= config.period.end
                    and region.end_date is not None
                    and region.end_date >= config.period.start
                )
            )
        ]
    return [None, *(key for key in (chosen or []) if key != "global")]


def map_figure_specs(
    config: RunConfig, *, compare_configs: Sequence[RunConfig] = ()
) -> list[MapFigureSpec]:
    """Expand configured suites into logical products without opening input files."""
    sensors = _sensor_streams(config)
    names = [config.name, *(other.name for other in compare_configs)]
    if len(names) != len(set(names)):
        raise ValueError("comparison runs must have distinct names")
    if any(other.period != config.period for other in compare_configs):
        raise ValueError("comparison runs must have the same UTC period")
    specs = []
    for region in plot_regions(config):
        suffix = f"_{region}" if region else ""
        if "aod_maps" in config.plots.suites:
            for name in (
                "mean_aod",
                "uncertainty",
                "observation_count",
                "observation_fraction",
                "stream_disagreement",
                "negative_aod_fraction",
            ):
                specs.append(
                    MapFigureSpec(
                        "aod_maps",
                        name + suffix,
                        config.period.start,
                        config.period.end,
                        name,
                        region,
                    )
                )
            for day in config.period.days():
                specs.append(
                    MapFigureSpec(
                        "aod_maps", "daily_mean_aod" + suffix, day, day, "daily_mean_aod", region
                    )
                )
        if "aod_differences" in config.plots.suites:
            if {"modis", "viirs"} <= sensors.keys():
                specs.append(
                    MapFigureSpec(
                        "aod_differences",
                        "viirs_minus_modis" + suffix,
                        config.period.start,
                        config.period.end,
                        "sensor_difference",
                        region,
                    )
                )
            for index, other in enumerate(compare_configs):
                specs.append(
                    MapFigureSpec(
                        "aod_differences",
                        f"{config.name}_minus_{other.name}{suffix}",
                        config.period.start,
                        config.period.end,
                        "run_difference",
                        region,
                        index,
                    )
                )
    return specs


def pdf_metadata(config: RunConfig, figure: Figure) -> dict[str, str]:
    """Standard PDF Info keys only; processing choices are searchable keywords."""
    metadata = getattr(figure, "_sarb_plot_metadata", {})
    titles = [item.get_text() for item in metadata.get("titles", [])]
    keywords = {
        "run": config.name,
        "weighting": config.weighting.method,
        "rho": config.weighting.rho,
        "qc": config.qc.preset,
        "collections": {
            name: selection.collection for name, selection in config.grid.streams.items()
        },
        "config_sha256": config_sha256(config),
        "processing_sha256": processing_sha256(config),
        "git_commit": git_commit(),
    }
    if metadata.get("comparison_provenance"):
        keywords["comparison"] = metadata["comparison_provenance"]
    if metadata.get("pairing"):
        keywords["pairing"] = metadata["pairing"]
    if metadata.get("provenance"):
        keywords["provenance"] = metadata["provenance"]
    return {
        "Title": " | ".join(titles),
        "Subject": "550 nm aerosol optical depth",
        "Keywords": json.dumps(keywords, sort_keys=True, separators=(",", ":")),
        "Creator": "sarb-aod",
    }


def save_figure(
    config: RunConfig,
    figure: Figure,
    *,
    suite: str,
    name: str,
    start: date,
    end: date,
) -> PlotArtifact:
    """Validate before publication; atomically replace PDF and fresh PNG files.

    An individual JSON report records failures even when no deliverable is
    published. ``write_protocol_report`` combines these reports per suite.
    Figures remain open so callers may inspect them; suite runners close them.
    """
    path = plot_path(config, suite, name, start, end)
    key = f"{name}.{start:%Y%m%d}-{end:%Y%m%d}"
    report = validate_rendered_figures(
        {key: figure}, report_path=path.with_suffix(".protocol.json")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    preview = path.with_suffix(".png")
    temporary = []
    try:
        for target, fmt in ((path, "pdf"), (preview, "png")):
            handle, filename = tempfile.mkstemp(
                prefix=f".{target.stem}.", suffix=target.suffix, dir=target.parent
            )
            os.close(handle)
            temp = Path(filename)
            temporary.append(temp)
            kwargs: dict[str, Any] = {"format": fmt, "bbox_inches": figure.bbox_inches}
            if fmt == "pdf":
                kwargs["metadata"] = pdf_metadata(config, figure)
            else:
                kwargs["dpi"] = 120
            figure.savefig(temp, **kwargs)
        # Both new products exist before publication. Remove a stale preview
        # before the new PDF becomes visible, then replace it with this render.
        preview.unlink(missing_ok=True)
        os.replace(temporary[0], path)
        os.replace(temporary[1], preview)
    finally:
        for temp in temporary:
            temp.unlink(missing_ok=True)
    return PlotArtifact(suite, name, start, end, path, preview, report)


def write_protocol_report(
    config: RunConfig,
    suite: str,
    artifacts: Sequence[PlotArtifact],
    *,
    failure: dict[str, Any] | None = None,
    expected_figures: Sequence[str] | None = None,
) -> Path:
    """Combine serializable figure checks without retaining large live figures."""
    reports = [item.report for item in artifacts if item.suite == suite]
    if failure is not None:
        reports.append(failure)
    figures = [name for report in reports for name in report["figures"]]
    failures = [message for report in reports for message in report.get("failures", [])]
    if not figures:
        failures.append("suite contains no figures")
    if len(figures) != len(set(figures)):
        failures.append("suite figure labels must be unique")
    if expected_figures is not None and figures != list(expected_figures):
        failures.append("rendered figure list differs from expected suite products")
    report = {
        "protocol": PROTOCOL,
        "passed": not failures,
        "figures": figures,
        "checks": {name: result for item in reports for name, result in item["checks"].items()},
        "failures": failures,
    }
    path = plot_path(config, suite, "protocol", config.period.start, config.period.end).with_suffix(
        ".json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return path


def _comparison_sources(config: RunConfig, other: RunConfig) -> tuple[str, str]:
    if config.weighting.method != other.weighting.method:
        return config.weighting.method, other.weighting.method
    if config.qc.preset != other.qc.preset:
        return config.qc.preset, other.qc.preset
    if config.weighting.rho != other.weighting.rho:
        return (
            labeling.source_display_name("gls", rho=config.weighting.rho),
            labeling.source_display_name("gls", rho=other.weighting.rho),
        )
    left_sensors, right_sensors = _sensor_streams(config), _sensor_streams(other)
    if left_sensors.keys() != right_sensors.keys():
        return (
            next(iter(left_sensors)) if len(left_sensors) == 1 else "mixed",
            next(iter(right_sensors)) if len(right_sensors) == 1 else "mixed",
        )
    catalog = config.load_catalog()
    changed = [
        stream
        for stream, selected in config.grid.streams.items()
        if stream in other.grid.streams
        and selected.product == other.grid.streams[stream].product
        and selected.collection != other.grid.streams[stream].collection
    ]
    if changed:
        instruments = {
            catalog.product(config.grid.streams[stream].product).instrument.lower()
            for stream in changed
        }
        left_versions = {config.grid.streams[stream].collection for stream in changed}
        right_versions = {other.grid.streams[stream].collection for stream in changed}
        if len(instruments) == len(left_versions) == len(right_versions) == 1:
            instrument = next(iter(instruments))
            source = changed[0] if len(changed) == 1 else instrument
            prefix = "C" if instrument == "modis" else "v"
            display = labeling.source_display_name(source)
            return (
                labeling.source_display_name(f"{display} {prefix}{next(iter(left_versions))}"),
                labeling.source_display_name(f"{display} {prefix}{next(iter(right_versions))}"),
            )
    if config.qc.aod_min != other.qc.aod_min:
        return f"AOD ≥ {config.qc.aod_min:g}", f"AOD ≥ {other.qc.aod_min:g}"
    if config.qc.aod_max != other.qc.aod_max:
        return f"AOD ≤ {config.qc.aod_max:g}", f"AOD ≤ {other.qc.aod_max:g}"
    if config.qc.overrides != other.qc.overrides:
        families: list[AlgorithmFamily] = []
        if any(stream.endswith("_dt") for stream in left_sensors.get("modis", [])):
            families.append("modis_dt")
        if any(stream.endswith("_dt") for stream in left_sensors.get("viirs", [])):
            families.append("viirs_dt")
        left_qa = {resolve_rule(family, "ocean", config.qc).qa_min for family in families}
        right_qa = {resolve_rule(family, "ocean", other.qc).qa_min for family in families}
        if len(left_qa) == len(right_qa) == 1 and left_qa != right_qa:
            return f"Ocean QA ≥ {next(iter(left_qa))}", f"Ocean QA ≥ {next(iter(right_qa))}"
    return config.name, other.name


def _mean_aod_layout(config: RunConfig) -> list[list[str]]:
    """Keep platform rows aligned within Dark Target and Deep Blue columns."""
    sensors = _sensor_streams(config)
    sensor_order = {name: index for index, name in enumerate(("modis", "viirs"))}
    summaries = (["product"] if len(sensors) > 1 else []) + sorted(
        sensors, key=lambda name: (sensor_order.get(name, len(sensor_order)), name)
    )
    platform_order = {name: index for index, name in enumerate(("terra", "aqua", "snpp", "noaa20"))}
    platforms = sorted(
        {stream.rsplit("_", 1)[0] for stream in config.grid.streams},
        key=lambda name: (platform_order.get(name, len(platform_order)), name),
    )
    layout = [[".", ".", "."] for _ in range(max(len(platforms), len(summaries) + 1))]
    for row, platform in enumerate(platforms):
        for column, algorithm in enumerate(("dt", "db"), start=1):
            stream = f"{platform}_{algorithm}"
            if stream in config.grid.streams:
                layout[row][column] = stream
    for row, source in enumerate(summaries):
        layout[row][0] = source
    layout[len(summaries)][0] = "provenance"
    return layout


def _map_provenance(
    config: RunConfig, aggregate: MapAggregate, spec: MapFigureSpec
) -> dict[str, Any]:
    """Separate recorded grid history from configured L2 source locations."""
    catalog = config.load_catalog()
    static = {
        "data_root": str(config.paths.data_root),
        "run": config.name,
        "stem": config.output.stem,
    }

    def resolve(template: str, values: dict[str, str]) -> str:
        for key, value in values.items():
            template = template.replace("{" + key + "}", value)
        return template

    inputs = []
    patterns = []
    for selection in config.grid.streams.values():
        product = catalog.product(selection.product)
        collection = catalog.collection(selection.product, selection.collection)
        values = dict(
            static,
            platform=product.platform,
            instrument=product.instrument,
            short_name=selection.product,
            collection=selection.collection,
            cmr_version=collection.cmr_version,
            file_token=collection.file_token,
        )
        inputs.append(resolve(config.paths.granule_dir, values))
        patterns.append(resolve(config.paths.granule_pattern, values))
    return {
        **aggregate.provenance,
        "grid_created_utc": aggregate.provenance.get("grid_created_utc", []),
        "processing_commits": aggregate.provenance.get("processing_commits", ["unknown"]),
        "plot_created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "plot_git_commit": git_commit(),
        "weighting": config.weighting.method,
        "rho": config.weighting.rho,
        "birge": config.weighting.birge,
        "qc": config.qc.preset,
        "qc_config": config.qc.model_dump(mode="json"),
        "grid": f"{config.grid.source} ({aggregate.grid.shape[0]} × {aggregate.grid.shape[1]})",
        "windows": aggregate.windows,
        "period_mean_weighting": "observation_count",
        "l2_input_templates": list(dict.fromkeys(inputs)),
        "l2_filename_patterns": list(dict.fromkeys(patterns)),
        "grid_output_template": resolve(config.paths.output, static),
        "pdf_output": str(plot_path(config, spec.suite, spec.name, spec.start, spec.end)),
    }


def render_map_figure(
    config: RunConfig,
    aggregate: MapAggregate,
    spec: MapFigureSpec,
    *,
    compare_configs: Sequence[RunConfig] = (),
    comparisons: Sequence[MapAggregate] = (),
) -> Figure:
    """Render one logical product with the full fixed GEOSIT AOD levels."""
    kwargs: dict[str, Any] = {
        "grid": aggregate.grid,
        "start": spec.start,
        "end": spec.end,
        "region": spec.region,
        "plot_type": config.plots.aod_fill,
        "cartopy_data_dir": cartopy_data_dir(config),
        "coastline_resolution": config.plots.coastline_resolution,
    }
    if spec.field == "mean_aod":
        layout = _mean_aod_layout(config)
        panel_sources = list(
            dict.fromkeys(name for row in layout for name in row if name not in {".", "provenance"})
        )
        labels = {
            sensor: labeling.collection_source_name(
                sensor, [config.grid.streams[stream].collection for stream in streams]
            )
            for sensor, streams in _sensor_streams(config).items()
        }
        return maps.map_panels(
            [aggregate.means[source] for source in panel_sources],
            panel_sources,
            aod_levels=geosit_aod_levels([1]),
            layout=layout,
            source_labels=labels,
            provenance=_map_provenance(config, aggregate, spec),
            **kwargs,
        )
    if spec.field == "daily_mean_aod":
        return maps.plot_map(
            aggregate.daily_means[spec.start],
            source=(
                next(iter(_sensor_streams(config))) if len(_sensor_streams(config)) == 1 else None
            ),
            aod_levels=geosit_aod_levels([1]),
            **kwargs,
        )
    if spec.field in {"sensor_difference", "run_difference"}:
        if spec.field == "sensor_difference":
            values = aggregate.means["viirs"] - aggregate.means["modis"]
            sources = ("viirs", "modis")
        else:
            if spec.comparison is None:
                raise ValueError("run difference requires a comparison index")
            values = aggregate.means["product"] - comparisons[spec.comparison].means["product"]
            sources = _comparison_sources(config, compare_configs[spec.comparison])
        figure = maps.plot_map(values, kind="difference", **kwargs)
        getattr(figure, "_sarb_plot_metadata")["colorbars"][0].set_label(
            labeling.bias_label(*sources, "1")
        )
        if spec.comparison is not None:
            other = compare_configs[spec.comparison]
            getattr(figure, "_sarb_plot_metadata")["comparison_provenance"] = {
                "run": other.name,
                "config_sha256": config_sha256(other),
                "weighting": other.weighting.model_dump(mode="json"),
                "qc": other.qc.preset,
                "collections": {
                    name: selection.collection for name, selection in other.grid.streams.items()
                },
            }
        figure.tight_layout(pad=0.5)
        return figure
    fields: dict[str, tuple[np.ndarray, maps.FieldKind, str]] = {
        "uncertainty": (aggregate.uncertainty, "uncertainty", "AOD Uncertainty"),
        "observation_count": (aggregate.count, "count", "Observation Count"),
        "observation_fraction": (
            aggregate.observation_fraction,
            "fraction",
            "Observation Fraction",
        ),
        "stream_disagreement": (aggregate.chi2, "chi2", "Stream Disagreement"),
        "negative_aod_fraction": (aggregate.negative_fraction, "fraction", "Negative AOD Fraction"),
    }
    values, kind, quantity = fields[spec.field]
    sensors = _sensor_streams(config)
    return maps.plot_map(
        values,
        kind=kind,
        quantity=quantity,
        source=next(iter(sensors)) if len(sensors) == 1 else None,
        **kwargs,
    )


def render_map_suites(
    config: RunConfig,
    *,
    compare_configs: Sequence[RunConfig] = (),
) -> list[PlotArtifact]:
    """Validate CAM7 inputs and publish configured global/regional map suites."""
    specs = map_figure_specs(config, compare_configs=compare_configs)
    if not specs:
        return []
    aggregate = aggregate_maps(config)
    comparisons = [aggregate_maps(other) for other in compare_configs]
    artifacts: list[PlotArtifact] = []
    with ncar_style(config.plots.context):
        for spec in specs:
            figure = render_map_figure(
                config, aggregate, spec, compare_configs=compare_configs, comparisons=comparisons
            )
            try:
                artifacts.append(
                    save_figure(
                        config,
                        figure,
                        suite=spec.suite,
                        name=spec.name,
                        start=spec.start,
                        end=spec.end,
                    )
                )
            except PlotProtocolError as exc:
                write_protocol_report(config, spec.suite, artifacts, failure=exc.report)
                raise
            finally:
                plt.close(figure)
    for suite in dict.fromkeys(spec.suite for spec in specs):
        write_protocol_report(
            config,
            suite,
            artifacts,
            expected_figures=[spec.key for spec in specs if spec.suite == suite],
        )
    return artifacts
