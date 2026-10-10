"""Configured suite expansion, CAM7 aggregation, and offline publication gates."""

import json
from datetime import date
from pathlib import Path

import cartopy
import matplotlib
import numpy as np
import pytest
import shapefile
import typer
import yaml
from cartopy.io import Downloader
from netCDF4 import Dataset
from pydantic import ValidationError
from typer.testing import CliRunner

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from sarb_aod.config.schema import PlotsConfig, RunConfig
from sarb_aod.contract import write_contract_file
from sarb_aod.paths import output_path, plot_path
from sarb_aod.pipeline import processing_sha256
from sarb_aod.plots import cli, suites
from sarb_aod.plots.protocol import PlotProtocolError
from sarb_aod.plots.style import geosit_aod_levels, ncar_style
from sarb_aod.windows import window_bounds


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RunConfig:
    directory = tmp_path / "cartopy" / "shapefiles" / "natural_earth" / "physical"
    directory.mkdir(parents=True)
    with shapefile.Writer(
        str(directory / "ne_110m_coastline"), shapeType=shapefile.POLYLINE
    ) as writer:
        writer.field("name", "C")
        writer.line([[[-35, -45], [-30, -10], [-35, 20], [-20, 40]]])
        writer.record("synthetic coastline")
    monkeypatch.setitem(cartopy.config, "data_dir", tmp_path / "cartopy")
    monkeypatch.setitem(cartopy.config, "pre_existing_data_dir", tmp_path / "empty")

    def refuse_download(*args: object, **kwargs: object) -> None:
        raise AssertionError("plotting must use the configured local cache")

    monkeypatch.setattr(Downloader, "_urlopen", refuse_download)
    return RunConfig.model_validate(
        {
            "name": "synthetic_gls",
            "paths": {"data_root": str(tmp_path)},
            "period": {"start": "2020-09-09", "end": "2020-09-09"},
            "stage": {"products": {"MOD04_L2": ["6.1"], "AERDB_L2_VIIRS_SNPP": ["2"]}},
            "grid": {
                "streams": {
                    "terra_dt": {"product": "MOD04_L2", "collection": "6.1"},
                    "snpp_db": {"product": "AERDB_L2_VIIRS_SNPP", "collection": "2"},
                }
            },
        }
    )


def _write_inputs(config: RunConfig, *, offset: float = 0, sampled_window: int = 0) -> None:
    shape = (192, 288)
    for index in range(8):
        k = index + 1
        count = np.full(shape, 3 * k, dtype=int)
        mean = np.full(shape, k / 10 + offset)
        sigma = np.full(shape, k / 100)
        chi2 = np.full(shape, k / 2)
        modis_count = np.full(shape, k, dtype=int)
        viirs_count = np.full(shape, 2 * k, dtype=int)
        negative = np.full(shape, index % 2, dtype=int)
        for cell in ((0, 0), (2, 2), *([(1, 1)] if index != sampled_window else [])):
            mean[cell], sigma[cell], chi2[cell], count[cell] = -999, -999, -999, 0
        for cell in ((0, 0), *([(1, 1)] if index != sampled_window else [])):
            modis_count[cell], viirs_count[cell], negative[cell] = 0, 0, 0
        # Accepted negative observations without usable uncertainty remain a
        # meaningful diagnostic even though GLS cannot estimate this cell.
        negative[2, 2] = 1
        modis, viirs = np.where(mean > -900, mean - 0.05, -999), np.where(
            mean > -900, mean + 0.05, -999
        )
        start = window_bounds(config.period.start, index)[0]
        write_contract_file(
            output_path(config, start),
            window_start=start,
            aod_weighted_mean=mean,
            aod_count=count,
            aod_uncertainty=sigma,
            aod_weighted_mean_modis=modis,
            aod_weighted_mean_viirs=viirs,
            global_attrs={"processing_sha256": processing_sha256(config)},
            extra_fields={
                "aod_chi2_per_dof": chi2,
                "aod_count_negative": negative,
                "aod_count_terra_dt": modis_count,
                "aod_count_snpp_db": viirs_count,
                "aod_mean_terra_dt": modis,
                "aod_mean_snpp_db": viirs,
            },
        )


@pytest.fixture
def inputs(config: RunConfig) -> RunConfig:
    _write_inputs(config)
    return config


@pytest.fixture(autouse=True)
def close_figures() -> None:
    yield
    plt.close("all")


def test_count_weighted_period_means_and_fraction_denominators(inputs: RunConfig) -> None:
    data = suites.aggregate_maps(inputs)
    assert data.windows == 8
    assert list(data.means) == ["product", "modis", "viirs", "terra_dt", "snpp_db"]
    expected = 204 / 36 / 10
    assert data.means["product"][50, 50] == pytest.approx(expected)
    assert data.means["modis"][50, 50] == pytest.approx(expected - 0.05)
    assert data.means["viirs"][50, 50] == pytest.approx(expected + 0.05)
    assert data.uncertainty[50, 50] == pytest.approx(expected / 10)
    assert data.chi2[50, 50] == pytest.approx(expected * 5)
    assert data.count[50, 50] == 108
    assert data.observation_fraction[1, 1] == 1 / 8
    assert data.means["product"][1, 1] == pytest.approx(0.1)
    assert np.isnan(data.means["product"][0, 0])
    assert np.isnan(data.negative_fraction[0, 0])
    assert data.observation_fraction[0, 0] == data.count[0, 0] == 0
    assert data.negative_fraction[50, 50] == pytest.approx(4 / 108)
    assert data.negative_fraction[2, 2] == pytest.approx(8 / 108)
    assert data.count[2, 2] == 0
    np.testing.assert_equal(data.daily_means[inputs.period.start], data.means["product"])


@pytest.mark.parametrize("damage", ["missing", "provenance", "time", "diagnostic"])
def test_input_integrity_is_required(inputs: RunConfig, damage: str) -> None:
    path = output_path(inputs, window_bounds(inputs.period.start, 3)[0])
    if damage == "missing":
        path.unlink()
    else:
        with Dataset(path, "r+") as dataset:
            if damage == "provenance":
                dataset.processing_sha256 = "wrong"
            elif damage == "time":
                dataset["time"][...] += 3
                dataset["time_bnds"][:] += 3
            else:
                dataset.renameVariable("aod_chi2_per_dof", "missing_diagnostic")
    with pytest.raises(
        ValueError,
        match={
            "missing": "Invalid map input",
            "provenance": "provenance",
            "time": "requested window",
            "diagnostic": "missing aod_chi2",
        }[damage],
    ):
        suites.aggregate_maps(inputs)


def test_plot_options_do_not_invalidate_processing(inputs: RunConfig) -> None:
    changed = inputs.model_copy(
        update={"plots": PlotsConfig(context="presentation", aod_fill="contourf")}
    )
    assert processing_sha256(changed) == processing_sha256(inputs)
    suites.aggregate_maps(changed)


def test_logical_expansion_paths_regions_and_comparisons(config: RunConfig) -> None:
    other = config.model_copy(update={"name": "count"})
    specs = suites.map_figure_specs(config, compare_configs=[other])
    assert [item.field for item in specs] == [
        "mean_aod",
        "uncertainty",
        "observation_count",
        "observation_fraction",
        "stream_disagreement",
        "negative_aod_fraction",
        "daily_mean_aod",
        "sensor_difference",
        "run_difference",
    ]
    path = plot_path(config, specs[0].suite, specs[0].name, specs[0].start, specs[0].end)
    assert (
        path
        == config.paths.data_root / "plots/synthetic_gls/aod_maps/mean_aod.20200909-20200909.pdf"
    )
    regional = config.model_copy(update={"plots": PlotsConfig(regions=["western_us"])})
    expanded = suites.map_figure_specs(regional)
    assert len(expanded) == 16
    assert expanded[8].name == "mean_aod_western_us"
    assert suites.plot_regions(regional) == [None, "western_us"]
    with pytest.raises(ValueError, match="distinct names"):
        suites.map_figure_specs(config, compare_configs=[config])
    other = other.model_copy(
        update={"period": other.period.model_copy(update={"end": date(2020, 9, 10)})}
    )
    with pytest.raises(ValueError, match="same UTC period"):
        suites.map_figure_specs(config, compare_configs=[other])


def test_full_fixed_aod_scales_and_difference_of_period_means(inputs: RunConfig) -> None:
    data = suites.aggregate_maps(inputs)
    other = inputs.model_copy(update={"name": "comparison"})
    # Observations at different windows need not overlap. The comparison is
    # of the two period means, never the average of coincident differences.
    _write_inputs(other, offset=0.2, sampled_window=7)
    second = suites.aggregate_maps(other)
    assert data.means["product"][1, 1] == pytest.approx(0.1)
    assert second.means["product"][1, 1] == pytest.approx(1.0)
    specs = suites.map_figure_specs(inputs, compare_configs=[other])
    with ncar_style("publication"):
        for spec in (specs[0], specs[6]):
            figure = suites.render_map_figure(inputs, data, spec)
            for artist in getattr(figure, "_sarb_plot_metadata")["absolute_aod_artists"]:
                np.testing.assert_array_equal(artist.norm.boundaries, geosit_aod_levels([1]))
        figure = suites.render_map_figure(
            inputs, data, specs[-1], compare_configs=[other], comparisons=[second]
        )
    meta = getattr(figure, "_sarb_plot_metadata")
    actual = meta["field_artists"][0]["artist"].get_array().filled(np.nan)
    expected = np.roll(data.means["product"] - second.means["product"], -144, axis=1)
    np.testing.assert_allclose(actual, expected, equal_nan=True)
    assert actual[1, 145] == pytest.approx(-0.9)
    assert meta["colorbars"][0].ax.get_xlabel() == "Synthetic Gls − Comparison"
    keywords = json.loads(suites.pdf_metadata(inputs, figure)["Keywords"])
    assert keywords["comparison"]["run"] == "comparison"


def test_regional_panels_pass_protocol(inputs: RunConfig) -> None:
    data = suites.aggregate_maps(inputs)
    config = inputs.model_copy(update={"plots": PlotsConfig(regions=["western_us"])})
    spec = suites.map_figure_specs(config)[8]
    with ncar_style("publication"):
        figure = suites.render_map_figure(config, data, spec)
        result = suites.validate_rendered_figures({spec.key: figure})
    assert result["passed"]
    assert getattr(figure, "_sarb_plot_metadata")["titles"][0].get_text() == "AOD - Western US"


def test_complete_offline_suite_passes_protocol_and_writes_pdf_png(inputs: RunConfig) -> None:
    artifacts = suites.render_map_suites(inputs)
    assert len(artifacts) == 8
    assert all(
        item.pdf.stat().st_size > 1000 and item.png.stat().st_size > 1000 for item in artifacts
    )
    for suite, expected in (("aod_maps", 7), ("aod_differences", 1)):
        path = plot_path(
            inputs, suite, "protocol", inputs.period.start, inputs.period.end
        ).with_suffix(".json")
        report = json.loads(path.read_text())
        assert report["passed"] and not report["failures"]
        assert len(report["figures"]) == expected
        assert all(check["passed"] for check in report["checks"].values())
    pdf = artifacts[0].pdf.read_bytes()
    for key in (b"/Title", b"/Subject", b"/Keywords", b"/Creator"):
        assert key in pdf
    for custom in (b"/config_sha256", b"/rho", b"/git_commit"):
        assert custom not in pdf


def test_protocol_failure_prevents_publication_and_records_failure(inputs: RunConfig) -> None:
    data = suites.aggregate_maps(inputs)
    spec = suites.map_figure_specs(inputs)[0]
    with ncar_style("publication"):
        figure = suites.render_map_figure(inputs, data, spec)
        getattr(figure, "_sarb_plot_metadata")["dense_artists"][0].set_rasterized(False)
        with pytest.raises(PlotProtocolError):
            suites.save_figure(
                inputs, figure, suite=spec.suite, name=spec.name, start=spec.start, end=spec.end
            )
    path = plot_path(inputs, spec.suite, spec.name, spec.start, spec.end)
    assert not path.exists() and not path.with_suffix(".png").exists()
    assert not json.loads(path.with_suffix(".protocol.json").read_text())["passed"]


@pytest.mark.parametrize(
    "setting",
    [
        {"plots": {"regions": ["unknown"]}},
        {"plots": {"suites": ["aod_maps", "aod_maps"]}},
        {"plots": {"coastline_resolution": "10m"}},
        {"plots": {"sites": {"select": "top_n", "n": 0}}},
        {"paths": {"plot": "{data_root}/{unknown}.pdf"}},
        {"paths": {"plot": "{run}/{suite}/{name}.{start}-{end}.png"}},
    ],
)
def test_plot_configuration_rejects_invalid_options(config: RunConfig, setting: dict) -> None:
    raw = config.model_dump(mode="json")
    for key, values in setting.items():
        raw[key].update(values)
    with pytest.raises(ValidationError):
        RunConfig.model_validate(raw)


def test_plot_cli_comparisons_prefetch_and_failure(
    config: RunConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = typer.Typer()
    app.command("plot")(cli.plot_command)
    root = config.paths.data_root
    path = root / "run.yaml"
    path.write_text(yaml.safe_dump(config.model_dump(mode="json")))
    other = root / "other.yaml"
    other.write_text(
        yaml.safe_dump(config.model_copy(update={"name": "other"}).model_dump(mode="json"))
    )
    calls = []
    monkeypatch.setattr(
        cli, "render_map_suites", lambda cfg, **kwargs: calls.append((cfg, kwargs)) or []
    )
    runner = CliRunner()
    result = runner.invoke(app, ["-c", str(path), "--compare", str(other)])
    assert result.exit_code == 0, result.output
    assert calls[0][1]["compare_configs"][0].name == "other"
    monkeypatch.setattr(cli, "prefetch_coastlines", lambda directory: calls.append(directory))
    result = runner.invoke(app, ["-c", str(path), "--prefetch-maps"])
    assert result.exit_code == 0, result.output
    assert calls[-1] == root / "cartopy"

    def fail(*args: object, **kwargs: object) -> None:
        raise ValueError("synthetic provenance mismatch")

    monkeypatch.setattr(cli, "render_map_suites", fail)
    result = runner.invoke(app, ["-c", str(path)])
    assert result.exit_code == 2 and "provenance mismatch" in result.output


def test_plot_is_registered_on_public_cli() -> None:
    from sarb_aod.cli import app

    result = CliRunner().invoke(app, ["plot", "--help"])
    assert result.exit_code == 0, result.output
    assert "--compare" in result.output and "--prefetch-maps" in result.output
