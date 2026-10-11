"""Configured suite expansion, CAM7 aggregation, and offline publication gates."""

import json
import re
from dataclasses import replace
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
from sarb_aod.plots.labeling import bias_label
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
    meta["pairing"] = "cam7"
    assert json.loads(suites.pdf_metadata(inputs, figure)["Keywords"])["pairing"] == "cam7"


def test_regional_panels_pass_protocol(inputs: RunConfig) -> None:
    data = suites.aggregate_maps(inputs)
    config = inputs.model_copy(update={"plots": PlotsConfig(regions=["western_us"])})
    spec = suites.map_figure_specs(config)[8]
    with ncar_style("publication"):
        figure = suites.render_map_figure(config, data, spec)
        result = suites.validate_rendered_figures({spec.key: figure})
    assert result["passed"]
    assert (
        getattr(figure, "_sarb_plot_metadata")["titles"][0].get_text() == "AOD 550 nm - Western US"
    )


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
    info_id = re.search(rb"/Info (\d+) 0 R", pdf).group(1)
    info = re.search(rb"\b" + info_id + rb" 0 obj\s*<<(.*?)>>", pdf, re.S).group(1)
    for key in (b"/Title", b"/Subject", b"/Keywords", b"/Creator"):
        assert key in info
    for custom in (b"/config_sha256", b"/rho", b"/git_commit"):
        assert custom not in info


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


def test_public_plot_dispatches_configured_aeronet_suites(
    config: RunConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sarb_aod.cli import app

    changed = config.model_copy(update={"plots": PlotsConfig(suites=["aeronet_scatter"])})
    path = config.paths.data_root / "aeronet.yaml"
    path.write_text(yaml.safe_dump(changed.model_dump(mode="json")))
    calls = []
    monkeypatch.setattr(cli, "render_map_suites", lambda cfg, **kwargs: [])
    monkeypatch.setattr(cli, "render_aeronet_suites", lambda cfg, **kwargs: calls.append(cfg) or [])
    result = CliRunner().invoke(app, ["plot", "-c", str(path)])
    assert result.exit_code == 0, result.output
    assert calls[0].plots.suites == ["aeronet_scatter"]


def test_comparison_labels_describe_sensor_version_and_qc_changes(config: RunConfig) -> None:
    from sarb_aod.config.schema import QAOverride

    def selection(**updates: object) -> RunConfig:
        return config.model_copy(update=updates)

    viirs = selection(
        grid=config.grid.model_copy(update={"streams": {"snpp_db": config.grid.streams["snpp_db"]}})
    )
    modis = selection(
        grid=config.grid.model_copy(
            update={"streams": {"terra_dt": config.grid.streams["terra_dt"]}}
        )
    )
    assert bias_label(*suites._comparison_sources(viirs, modis), "1") == "VIIRS − MODIS"
    version = viirs.grid.streams["snpp_db"].model_copy(update={"collection": "2.1"})
    changed = viirs.model_copy(
        update={"grid": viirs.grid.model_copy(update={"streams": {"snpp_db": version}})}
    )
    assert (
        bias_label(*suites._comparison_sources(changed, viirs), "1")
        == "SNPP Deep Blue v2.1 − SNPP Deep Blue v2"
    )
    qa = selection(
        qc=config.qc.model_copy(update={"overrides": {"modis_dt": {"ocean": QAOverride(qa_min=3)}}})
    )
    assert bias_label(*suites._comparison_sources(qa, config), "1") == "Ocean QA ≥ 3 − Ocean QA ≥ 1"
    positive = selection(qc=config.qc.model_copy(update={"aod_min": 0}))
    assert bias_label(*suites._comparison_sources(positive, config), "1") == "AOD ≥ 0 − AOD ≥ -0.05"


@pytest.mark.parametrize("stream,source", [("terra_dt", "MODIS"), ("snpp_db", "VIIRS")])
@pytest.mark.parametrize(
    "field,quantity", [("daily_mean_aod", "AOD 550 nm"), ("uncertainty", "AOD Uncertainty 550 nm")]
)
def test_single_sensor_global_maps_identify_source(
    inputs: RunConfig, stream: str, source: str, field: str, quantity: str
) -> None:
    data = suites.aggregate_maps(inputs)
    single = inputs.model_copy(
        update={
            "grid": inputs.grid.model_copy(
                update={"streams": {stream: inputs.grid.streams[stream]}}
            )
        }
    )
    spec = next(spec for spec in suites.map_figure_specs(single) if spec.field == field)
    figure = suites.render_map_figure(single, data, spec)
    assert figure.axes[0].get_title() == f"{source} {quantity}"


@pytest.mark.parametrize("collection", ["6.1", "7"])
def test_monthly_collection_title_layout_and_recorded_provenance(
    inputs: RunConfig, collection: str
) -> None:
    for index in range(8):
        path = output_path(inputs, window_bounds(inputs.period.start, index)[0])
        with Dataset(path, "r+") as dataset:
            dataset.date_created = f"2021-01-0{1 + index % 2}T12:00:00Z"
            dataset.git_commit = "a" * 40
    data = suites.aggregate_maps(inputs)
    streams = {
        stream: inputs.grid.streams["terra_dt"].model_copy(
            update={"product": product, "collection": collection}
        )
        for stream, product in (
            ("terra_dt", "MOD04_L2"),
            ("terra_db", "MOD04_L2"),
            ("aqua_dt", "MYD04_L2"),
            ("aqua_db", "MYD04_L2"),
        )
    }
    config = inputs.model_copy(update={"grid": inputs.grid.model_copy(update={"streams": streams})})
    data = replace(data, means={**data.means, **{key: data.means["terra_dt"] for key in streams}})
    spec = suites.map_figure_specs(config)[0]
    figure = suites.render_map_figure(config, data, spec)
    metadata = getattr(figure, "_sarb_plot_metadata")
    assert metadata["titles"][0].get_text() == f"MODIS C{collection} AOD 550 nm"
    assert metadata["titles"][1].get_text() == "Terra Dark Target AOD 550 nm"
    axes = metadata["data_axes"]
    assert len(axes) == 5
    assert list(axes[0].get_subplotspec().rowspan) == [0]
    panel = metadata["provenance_text"][0]
    assert list(panel.axes.get_subplotspec().rowspan) == [1]
    assert suites.validate_rendered_figures({spec.key: figure})["passed"]
    np.testing.assert_allclose(axes[0].get_position().bounds[1:], axes[1].get_position().bounds[1:])
    assert panel.get_text() == "Provenance"
    assert panel.get_fontweight() == "bold"
    colorbar = metadata["colorbars"][0]
    tagline = next(
        text for text in metadata["provenance_text"] if text.get_text().startswith("DAVINCI")
    )
    assert tagline.axes is panel.axes
    assert tagline.get_fontweight() == "bold"
    assert tagline.get_text().replace("\n", " ") == (
        "DAVINCI Data Analysis and Visual Intelligence for Climate & Chemistry"
    )
    assert colorbar.ax.get_position().x0 >= axes[1].get_position().x0 - 0.01
    assert colorbar.ax.get_position().x1 <= axes[2].get_position().x1 + 0.01
    repository_text = metadata["provenance_text"][-1]
    np.testing.assert_allclose(
        repository_text.get_window_extent().y1, axes[3].title.get_window_extent().y1
    )
    assert repository_text.get_text() == "https://github.com/NCAR/CERES-SARB-MODIS-VIIRS"
    assert repository_text.get_url() == repository_text.get_text()
    assert repository_text.get_window_extent().y0 > panel.get_window_extent().y1
    provenance = json.loads(suites.pdf_metadata(config, figure)["Keywords"])["provenance"]
    assert provenance["grid_created_utc"] == ["2021-01-01T12:00:00Z", "2021-01-02T12:00:00Z"]
    assert provenance["processing_commits"] == ["a" * 40]
    assert len(provenance["grid_files"]) == 8
    assert provenance["pdf_output"] == str(
        plot_path(config, spec.suite, spec.name, spec.start, spec.end)
    )
    panel_text = "\n".join(text.get_text() for text in metadata["provenance_text"])
    assert "L2 Inputs (Configured):\n  " in panel_text
    assert "Time Windows: 8 × 3 Hours" in panel_text
    # Wrapped path pieces reconstruct the full path without splitting a component.
    from sarb_aod.plots.labeling import _provenance_path_lines

    for path in [
        *provenance["l2_input_templates"],
        provenance["grid_output_template"],
        provenance["pdf_output"],
    ]:
        lines = _provenance_path_lines(path, width=45)
        assert "".join(line.strip() for line in lines) == path
        assert all(line.endswith("/") for line in lines[:-1])
    # The exception for literal provenance must not exempt ordinary plot text.
    axes[0].text(0.5, 0.5, "raw_internal_key", transform=axes[0].transAxes)
    report = suites.validate_rendered_figures({spec.key: figure}, raise_on_failure=False)
    assert any("text exposes a raw key" in failure for failure in report["failures"])


def test_provenance_identifies_qc_overrides(inputs: RunConfig) -> None:
    from sarb_aod.config.schema import QAOverride
    from sarb_aod.plots.labeling import provenance_panel_text

    config = inputs.model_copy(
        update={
            "qc": inputs.qc.model_copy(
                update={"overrides": {"modis_dt": {"ocean": QAOverride(qa_min=3)}}}
            )
        }
    )
    spec = suites.map_figure_specs(config)[0]
    provenance = suites._map_provenance(config, suites.aggregate_maps(inputs), spec)
    assert provenance["qc_config"]["overrides"]["modis_dt"]["ocean"]["qa_min"] == 3
    assert "Retrieval-Team QC + Overrides" in provenance_panel_text(provenance)


@pytest.mark.parametrize("region", ["canada", "southern_ocean"])
def test_shallow_regional_provenance_remains_readable(inputs: RunConfig, region: str) -> None:
    data = suites.aggregate_maps(inputs)
    streams = {
        stream: inputs.grid.streams["terra_dt"].model_copy(update={"product": product})
        for stream, product in (
            ("terra_dt", "MOD04_L2"),
            ("terra_db", "MOD04_L2"),
            ("aqua_dt", "MYD04_L2"),
            ("aqua_db", "MYD04_L2"),
        )
    }
    config = inputs.model_copy(update={"grid": inputs.grid.model_copy(update={"streams": streams})})
    data = replace(data, means={**data.means, **{key: data.means["terra_dt"] for key in streams}})
    spec = suites.MapFigureSpec(
        "aod_maps", "mean_aod", inputs.period.start, inputs.period.end, "mean_aod", region
    )
    with ncar_style("publication"):
        figure = suites.render_map_figure(config, data, spec)
        assert suites.validate_rendered_figures({spec.key: figure})["passed"]
    texts = getattr(figure, "_sarb_plot_metadata")["provenance_text"]
    assert min(text.get_fontsize() for text in texts) >= 7


def test_south_atlantic_provenance_with_multiple_revisions_fits(config: RunConfig) -> None:
    from sarb_aod.config.loader import load_config
    from sarb_aod.grid.spec import load_grid
    from sarb_aod.paths import cartopy_data_dir

    run = load_config("config/runs/modis_l2_200807_gls_rho05.yaml", profile="mac")
    run = run.model_copy(
        update={
            "paths": run.paths.model_copy(
                update={
                    "data_root": Path("/example/researcher/Data"),
                    "cartopy_data_dir": str(cartopy_data_dir(config)),
                }
            )
        }
    )
    grid = load_grid()
    values = np.full(grid.shape, 0.15)
    data = suites.MapAggregate(
        grid=grid,
        means={name: values for name in ["product", "modis", *run.grid.streams]},
        uncertainty=values,
        count=values,
        observation_fraction=values,
        chi2=values,
        negative_fraction=values,
        daily_means={},
        windows=248,
        provenance={
            "grid_created_utc": ["2026-10-11T00:00:00Z"],
            "processing_commits": ["a" * 40, "b" * 40, "c" * 40],
        },
    )
    spec = suites.MapFigureSpec(
        "aod_maps",
        "mean_aod_south_atlantic_ocean",
        run.period.start,
        run.period.end,
        "mean_aod",
        "south_atlantic_ocean",
    )
    with ncar_style("publication"):
        figure = suites.render_map_figure(run, data, spec)
        assert suites.validate_rendered_figures({spec.key: figure})["passed"]
