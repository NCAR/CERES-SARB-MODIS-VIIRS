"""Suite selection, independent sampling, input provenance and publication."""

import hashlib
import json
from pathlib import Path

import cartopy
import matplotlib
import numpy as np
import pandas as pd
import pytest
import shapefile
from cartopy.io import Downloader
from netCDF4 import Dataset

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from sarb_aod.config.schema import PlotSiteSelection, RunConfig
from sarb_aod.contract import write_contract_file
from sarb_aod.evaluation.collocate import write_matchups
from sarb_aod.paths import output_path, plot_path
from sarb_aod.pipeline import processing_sha256
from sarb_aod.plots import aeronet_suites as suites
from sarb_aod.plots.protocol import PlotProtocolError, validate_rendered_figures
from sarb_aod.windows import window_bounds


@pytest.fixture
def config(tmp_path, monkeypatch):
    cache = tmp_path / "cartopy/shapefiles/natural_earth/physical"
    cache.mkdir(parents=True)
    with shapefile.Writer(str(cache / "ne_110m_coastline"), shapeType=shapefile.POLYLINE) as writer:
        writer.field("name", "C")
        writer.line([[[-35, -45], [-30, -10], [-35, 20], [-20, 40]]])
        writer.record("fixture")
    monkeypatch.setitem(cartopy.config, "data_dir", tmp_path / "cartopy")
    monkeypatch.setitem(cartopy.config, "pre_existing_data_dir", tmp_path / "empty")

    def no_network(*args, **kwargs):
        raise AssertionError("No automatic downloads")

    monkeypatch.setattr(Downloader, "_urlopen", no_network)
    return RunConfig.model_validate(
        {
            "name": "suite_test",
            "paths": {"data_root": str(tmp_path)},
            "period": {"start": "2008-07-01", "end": "2008-07-01"},
            "stage": {"products": {"MOD04_L2": ["6.1"]}},
            "grid": {"streams": {"terra_dt": {"product": "MOD04_L2", "collection": "6.1"}}},
            "plots": {"suites": sorted(suites.AERONET_SUITES), "sites": {"n": 1}},
        }
    )


@pytest.fixture
def inputs():
    times = pd.date_range("2008-07-01T01:30Z", periods=8, freq="3h")
    pairs = pd.DataFrame(
        {
            "site": ["A"] * 4 + ["B"] * 4,
            "time": times,
            "x": [0.1, 0.2, 0.3, 0.4, 0.2, 0.4, 0.6, 0.8],
            "y": [0.12, 0.19, 0.33, 0.4, 0.21, 0.39, 0.61, 0.81],
            "sigma_x": 0.01,
            "sigma_y": 0.1,
            "latitude": 0.0,
            "longitude": 10.0,
            "sensor": "MODIS",
            "stream": "terra_dt",
            "algorithm": "dt",
            "collection": "6.1",
            "surface": "land",
            "aeronet_level": "AOD20",
        }
    )
    raw = pd.DataFrame(
        {
            "AERONET_Site": ["A"] * 8 + ["B"] * 8,
            "time": list(times.repeat(2)[:8]) + list(times.repeat(2)[8:]),
            "aod_550nm": np.repeat(pairs.x, 2),
            "Site_Latitude(Degrees)": 0.0,
            "Site_Longitude(Degrees)": 10.0,
        }
    )
    gridded = pd.DataFrame(
        {
            "site": ["A"] * 8 + ["B"] * 8,
            "time": list(times) * 2,
            "source": "product",
            "aod": list(np.arange(8) / 10) * 2,
            "sigma": 0.1,
        }
    )
    regional = pd.DataFrame(
        {
            "day": [pd.Timestamp("2008-07-01")],
            "region": ["global"],
            "source": ["product"],
            "aod": [0.7],
        }
    )
    return suites.SuiteInputs(
        {"grid": pairs, "l2": pairs.copy()}, raw, suites.GridSeries(gridded, regional)
    )


@pytest.fixture(autouse=True)
def close_figures():
    yield
    plt.close("all")


def test_top_sites_use_grid_counts_then_name(inputs):
    pairs = inputs.matchups["grid"]
    assert suites.select_sites(pairs, PlotSiteSelection(n=1)) == ["A"]
    extra = pd.concat([pairs, pairs.loc[pairs.site == "B"].iloc[:1]])
    assert suites.select_sites(extra, PlotSiteSelection(n=1)) == ["B"]
    assert suites.select_sites(pairs, ["B", "A"]) == ["B", "A"]
    with pytest.raises(ValueError, match="no grid matchups"):
        suites.select_sites(pairs, ["unknown"])


def test_time_limited_regions_exclude_other_days(inputs):
    pairs = inputs.matchups["grid"].copy()
    pairs["longitude"], pairs["latitude"] = -120, 40
    assert suites.subset_region(pairs, "labor_day_2020_smoke").empty
    pairs["time"] = pd.Timestamp("2020-09-08", tz="UTC")
    assert len(suites.subset_region(pairs, "labor_day_2020_smoke")) == len(pairs)


def test_matchup_provenance_accepts_plot_options_but_rejects_science(config, inputs):
    path = (
        Path(config.paths.data_root)
        / f"evaluation/{config.name}/matchups_grid_default_20080701_20080701.nc"
    )
    write_matchups(
        path,
        inputs.matchups["grid"],
        attributes={
            "level": "grid",
            "pairing": "default",
            "processing_sha256": processing_sha256(config),
            "config": config.model_dump(mode="json"),
        },
    )
    changed = config.model_copy(
        update={"plots": config.plots.model_copy(update={"regions": ["north_africa"]})}
    )
    assert len(suites.load_matchups(changed, "grid")) == 8
    changed = config.model_copy(
        update={"aeronet": config.aeronet.model_copy(update={"instrument_sigma": 0.02})}
    )
    with pytest.raises(ValueError, match="Stale matchup"):
        suites.load_matchups(changed, "grid")
    with pytest.raises(FileNotFoundError, match="run sarb-aod evaluate"):
        suites.load_matchups(config, "l2")


def test_aeronet_window_means_are_independent_and_half_open(config):
    points = pd.DataFrame(
        {
            "time": pd.to_datetime(
                ["2008-07-01T00:00Z", "2008-07-01T02:59Z", "2008-07-01T03:00Z", "2008-07-01T03:01Z"]
            ),
            "aod_550nm": [0.1, 0.3, 0.7, 0.9],
        }
    )
    result = suites.aeronet_window_series(points, config)
    np.testing.assert_allclose(result.aod, [0.2, 0.8])
    assert list(result.time.dt.hour) == [1, 4]
    assert result.sigma.iloc[0] == pytest.approx(np.sqrt(0.01**2 + 0.02))


def test_legacy_processing_provenance_requires_matching_report_checksum(config, inputs):
    directory = Path(config.paths.data_root) / f"evaluation/{config.name}"
    path = directory / "matchups_grid_default_20080701_20080701.nc"
    write_matchups(
        path,
        inputs.matchups["grid"],
        attributes={
            "level": "grid",
            "pairing": "default",
            "config": config.model_dump(mode="json"),
        },
    )
    report = directory / f"{config.name}_grid_default_20080701_20080701_report.json"
    provenance = {
        "processing_sha256": processing_sha256(config),
        "matchup_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    report.write_text(json.dumps({"provenance": provenance}))
    assert len(suites.load_matchups(config, "grid")) == 8
    provenance["matchup_sha256"] = "wrong"
    report.write_text(json.dumps({"provenance": provenance}))
    with pytest.raises(ValueError, match="processing provenance"):
        suites.load_matchups(config, "grid")


def test_rho_comparisons_require_other_scientific_settings_to_match(config):
    alternate = config.model_copy(
        update={"weighting": config.weighting.model_copy(update={"rho": 0})}
    )
    assert suites._rho_compatible(config, alternate)
    alternate = alternate.model_copy(
        update={"aeronet": config.aeronet.model_copy(update={"include_extrapolated": False})}
    )
    assert not suites._rho_compatible(config, alternate)


def test_grid_samples_keep_all_windows_and_area_daily_count_weighting(config, inputs):
    shape = (192, 288)
    for index in range(8):
        start = window_bounds(config.period.start, index)[0]
        write_contract_file(
            output_path(config, start),
            window_start=start,
            aod_weighted_mean=np.full(shape, (index + 1) / 10),
            aod_count=np.full(shape, index + 1),
            aod_uncertainty=np.full(shape, 0.02),
            global_attrs={"processing_sha256": processing_sha256(config)},
        )
    result = suites.load_grid_series(
        config, inputs.matchups["grid"], ["global", "labor_day_2020_smoke"]
    )
    assert len(result.sites) == 16
    assert result.sites.groupby("site").size().eq(8).all()
    assert result.regional.query("region == 'global'").aod.iloc[0] == pytest.approx(20.4 / 36)
    assert result.regional.query("region == 'labor_day_2020_smoke'").aod.isna().all()
    with Dataset(output_path(config, window_bounds(config.period.start, 0)[0]), "r+") as dataset:
        dataset.processing_sha256 = "stale"
    with pytest.raises(ValueError, match="provenance"):
        suites.load_grid_series(config, inputs.matchups["grid"], ["global"])


def test_series_keep_unmatched_grid_and_equal_site_sampling(config, inputs):
    site = suites.site_figure(config, "A", inputs)
    grid_line = next(line for line in site.axes[0].lines if "Grid" in line.get_label())
    assert len(grid_line.get_xdata()) == 8
    regional = suites.regional_figure(config, "global", inputs)
    metadata = regional._sarb_plot_metadata
    assert metadata["regional_daily"].x.iloc[0] == pytest.approx((0.25 + 0.5) / 2)
    assert metadata["full_regional_daily"].aod.iloc[0] == 0.7
    assert "Active Sites: 2" in metadata["subtitles"][0].get_text()
    assert validate_rendered_figures({"site": site, "region": regional})["passed"]


def test_all_suite_outputs_pass_protocol_and_include_expected_products(config, inputs):
    artifacts = suites.render_aeronet_suites(config, inputs=inputs)
    names = {item.name for item in artifacts}
    assert names >= {
        "grid_global_mb",
        "l2_global_r",
        "site_A",
        "region_global",
        "grid_global",
        "l2_stream_terra_dt",
        "grid_binned_bias",
        "l2_z",
        "grid_scorecard",
        "grid_taylor",
        "l2_regional_box",
    }
    for artifact in artifacts:
        assert artifact.pdf.is_file() and artifact.png.is_file()
        assert artifact.report["passed"]
    for suite in config.plots.suites:
        path = plot_path(
            config, suite, "protocol", config.period.start, config.period.end
        ).with_suffix(".json")
        assert json.loads(path.read_text())["passed"]


def test_protocol_failure_is_recorded_and_raised(config, inputs, monkeypatch):
    cfg = config.model_copy(
        update={"plots": config.plots.model_copy(update={"suites": ["aeronet_scatter"]})}
    )

    def rejected(*args, **kwargs):
        raise PlotProtocolError(
            {"figures": ["broken"], "checks": {}, "failures": ["dense layer not rasterized"]}
        )

    monkeypatch.setattr(suites, "save_figure", rejected)
    with pytest.raises(PlotProtocolError, match="not rasterized"):
        suites.render_aeronet_suites(cfg, inputs=inputs)
    path = plot_path(
        cfg, "aeronet_scatter", "protocol", cfg.period.start, cfg.period.end
    ).with_suffix(".json")
    assert not json.loads(path.read_text())["passed"]


def test_l2_series_derive_grid_context_without_grid_matchup_archive(config, inputs, monkeypatch):
    cfg = config.model_copy(
        update={
            "plots": config.plots.model_copy(
                update={
                    "suites": [
                        "aeronet_site_timeseries",
                        "aeronet_regional_timeseries",
                        "aeronet_scatter",
                    ]
                }
            )
        }
    )

    def no_archive(*args, **kwargs):
        raise AssertionError("L2 plot integration must not require a grid matchup archive")

    monkeypatch.setattr(suites, "load_matchups", no_archive)
    supplied = suites.SuiteInputs({"l2": inputs.matchups["l2"]}, inputs.aeronet, inputs.grid)
    artifacts = suites.render_aeronet_suites(cfg, levels=("l2",), inputs=supplied)
    assert {item.name for item in artifacts} == {
        "site_A",
        "region_global",
        "l2_global",
        "l2_stream_terra_dt",
    }


def test_sanitized_site_names_do_not_overwrite_each_other(config, inputs):
    rename = {"A": "A/B", "B": "A_B"}
    cfg = config.model_copy(
        update={
            "plots": config.plots.model_copy(
                update={"suites": ["aeronet_site_timeseries"], "sites": list(rename.values())}
            )
        }
    )
    supplied = suites.SuiteInputs(
        {
            level: frame.assign(site=frame.site.map(rename))
            for level, frame in inputs.matchups.items()
        },
        inputs.aeronet.assign(AERONET_Site=inputs.aeronet.AERONET_Site.map(rename)),
        suites.GridSeries(
            inputs.grid.sites.assign(site=inputs.grid.sites.site.map(rename)), inputs.grid.regional
        ),
    )
    artifacts = suites.render_aeronet_suites(cfg, inputs=supplied)
    assert len(artifacts) == len({item.pdf for item in artifacts}) == 2
    assert all(item.pdf.is_file() and item.png.is_file() for item in artifacts)


def test_cam7_plot_paths_are_distinct_from_default_pairing(config, inputs):
    cfg = config.model_copy(
        update={"plots": config.plots.model_copy(update={"suites": ["aeronet_scatter"]})}
    )
    supplied = suites.SuiteInputs({"grid": inputs.matchups["grid"]})
    default = suites.render_aeronet_suites(cfg, levels=("grid",), inputs=supplied)
    cam7 = suites.render_aeronet_suites(cfg, levels=("grid",), inputs=supplied, pairing="cam7")
    assert default[0].pdf != cam7[0].pdf
    assert default[0].pdf.is_file() and cam7[0].pdf.is_file()
    assert cam7[0].suite == "aeronet_scatter_cam7" and cam7[0].name == "cam7_grid_global"
    assert (
        plot_path(cfg, "aeronet_scatter_cam7", "protocol", cfg.period.start, cfg.period.end)
        .with_suffix(".json")
        .is_file()
    )


def test_evaluation_levels_keep_distinct_series_files_and_protocols(config, inputs):
    cfg = config.model_copy(
        update={"plots": config.plots.model_copy(update={"suites": ["aeronet_site_timeseries"]})}
    )
    l2 = suites.render_aeronet_suites(
        cfg,
        levels=("l2",),
        evaluation_level="l2",
        inputs=suites.SuiteInputs({"l2": inputs.matchups["l2"]}, inputs.aeronet, inputs.grid),
    )
    original = l2[0].pdf.read_bytes()
    grid = suites.render_aeronet_suites(
        cfg,
        levels=("grid",),
        evaluation_level="grid",
        inputs=suites.SuiteInputs({"grid": inputs.matchups["grid"]}, inputs.aeronet, inputs.grid),
    )
    assert l2[0].pdf != grid[0].pdf and l2[0].png != grid[0].png
    assert l2[0].pdf.read_bytes() == original
    for level in ("l2", "grid"):
        path = plot_path(
            cfg, f"aeronet_site_timeseries_{level}", "protocol", cfg.period.start, cfg.period.end
        ).with_suffix(".json")
        assert json.loads(path.read_text())["passed"]


def test_cam7_hourly_regional_components_use_containing_grid_window(config, inputs):
    pairs = inputs.matchups["grid"].iloc[:3].copy()
    pairs["time"] = pd.date_range("2008-07-01", periods=3, freq="h", tz="UTC")
    component = inputs.grid.sites.assign(source="modis", aod=0.6)
    gridded = suites.GridSeries(
        pd.concat([inputs.grid.sites, component], ignore_index=True), inputs.grid.regional
    )
    supplied = suites.SuiteInputs({"grid": pairs}, inputs.aeronet, gridded)
    figure = suites.regional_figure(config, "global", supplied)
    line = next(line for line in figure.axes[0].lines if line.get_label() == "MODIS Matched Sites")
    np.testing.assert_allclose(line.get_ydata(), [0.6])
    assert validate_rendered_figures({"hourly": figure})["passed"]


def test_duplicate_figure_keys_and_failed_suite_protocol_raise(config, inputs, monkeypatch):
    cfg = config.model_copy(
        update={"plots": config.plots.model_copy(update={"suites": ["aeronet_scatter"]})}
    )
    render = suites._figures

    def duplicated(*args, **kwargs):
        name, figure = next(render(*args, **kwargs))
        yield name, figure
        name, figure = next(render(*args, **kwargs))
        yield name, figure

    monkeypatch.setattr(suites, "_figures", duplicated)
    with pytest.raises(ValueError, match="Duplicate figure name"):
        suites.render_aeronet_suites(cfg, inputs=inputs)
    monkeypatch.setattr(suites, "_figures", render)
    original = suites.write_protocol_report

    def failed(*args, **kwargs):
        path = original(*args, **kwargs)
        document = json.loads(path.read_text())
        document.update(passed=False, failures=["missing expected figure"])
        path.write_text(json.dumps(document))
        return path

    monkeypatch.setattr(suites, "write_protocol_report", failed)
    with pytest.raises(PlotProtocolError, match="missing expected figure"):
        suites.render_aeronet_suites(cfg, inputs=inputs)
