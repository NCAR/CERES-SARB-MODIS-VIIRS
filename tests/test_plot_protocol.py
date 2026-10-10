"""Positive publication fixtures and a deliberate failure for every protocol rule."""

import json
from types import SimpleNamespace

import cartopy
import matplotlib
import shapefile

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest
from cartopy.io import Downloader
from matplotlib.colors import BoundaryNorm, Normalize, TwoSlopeNorm

from sarb_aod.plots import labeling
from sarb_aod.plots.maps import map_panels, plot_map, plot_site_map
from sarb_aod.plots.protocol import PlotProtocolError, validate_rendered_figures
from sarb_aod.plots.scatter import density_scatter
from sarb_aod.plots.series import TimedValues, regional_time_series, site_time_series
from sarb_aod.plots.style import aod_colorbar_ticks, geosit_aod_levels, ncar_style
from sarb_aod.plots.summary import (
    binned_bias,
    regional_boxplots,
    rho_sweep,
    scorecard,
    taylor_diagram,
    z_histogram,
)


@pytest.fixture(autouse=True)
def close_figures():
    with ncar_style("publication", use_seaborn=False):
        yield
    plt.close("all")


def basic():
    fig, ax = plt.subplots(figsize=(8, 5), layout="constrained")
    title = ax.set_title("AOD")
    ax.set_xlabel("AERONET AOD")
    ax.set_ylabel("MODIS AOD")
    metadata = dict(
        kind="test",
        data_axes=[ax],
        titles=[title],
        subtitles=[],
        dense_artists=[],
        field_artists=[],
        absolute_aod_artists=[],
        colorbars=[],
        gridliners=[],
    )
    fig._sarb_plot_metadata = metadata
    return fig, ax, metadata


def field(kind="aod", values=None):
    fig, ax, metadata = basic()
    values = np.arange(25).reshape(5, 5) / 50 if values is None else values
    if kind == "aod":
        norm, cmap = BoundaryNorm(geosit_aod_levels(values), 256, extend="max"), "turbo"
    elif kind in {"bias", "difference"}:
        norm, cmap = TwoSlopeNorm(vmin=-1, vcenter=0, vmax=1), "RdBu_r"
    else:
        norm, cmap = Normalize(0, 1), "gray_r" if kind == "fraction" else "viridis"
    artist = ax.pcolormesh(values, norm=norm, cmap=cmap, rasterized=True)
    metadata["field_artists"] = [{"artist": artist, "kind": kind}]
    metadata["dense_artists"] = [artist]
    return fig, ax, metadata, artist


def failures(fig):
    return "\n".join(
        validate_rendered_figures({"example": fig}, raise_on_failure=False)["failures"]
    )


def test_all_scientific_renderers_pass_and_report_is_json_safe(tmp_path):
    rng = np.random.default_rng(813)
    x = rng.gamma(2, 0.15, 1000)
    y = 1.03 * x + 0.0073 + rng.normal(0, 0.03, len(x))
    times = pd.date_range("2008-07-01", periods=8, freq="3h")
    dates = dict(start="2008-07-01", end="2008-07-31")
    pairs = pd.DataFrame(
        dict(site=["Mauna_Loa"] * 8, time=times, x=x[:8], y=y[:8], region=["western_us"] * 8)
    )
    figures = {
        "site": site_time_series(
            "Mauna_Loa",
            aeronet_points=TimedValues(times, x[:8]),
            aeronet_windows=TimedValues(times, x[:8]),
            grid=TimedValues(times, y[:8], np.full(8, 0.03)),
        ),
        "regional": regional_time_series(pairs, "western_us", **dates),
        "scatter": density_scatter(x, y, **dates),
        "bias": binned_bias(x, y, **dates),
        "residual": z_histogram((y - x) / 0.05, **dates),
        "correlation": rho_sweep(
            [0, 0.5, 1], {"RMSE": [0.1, 0.09, 0.08], "z_std": [1.2, 1, 0.8]}, **dates
        ),
        "scorecard": scorecard(
            pd.DataFrame(
                {"MB": [-0.02, 0.03], "RMSE": [0.08, 0.1], "R": [0.7, 0.9]},
                index=["western_us", "east_asia"],
            ),
            **dates,
        ),
        "Taylor": taylor_diagram(x, {"modis": y, "viirs": -x}, **dates),
        "boxes": regional_boxplots(
            {
                "western_us": {"modis": y[:100], "aeronet": x[:100]},
                "east_asia": {"modis": y[100:], "aeronet": x[100:]},
            },
            **dates,
        ),
    }
    path = tmp_path / "protocol.json"
    report = validate_rendered_figures(figures, report_path=path, expected_figures=list(figures))
    assert report["passed"]
    assert json.loads(path.read_text()) == report
    assert all(check["passed"] for check in report["checks"].values())


def test_real_cartopy_labels_maps_and_site_maps_pass_offline(tmp_path, monkeypatch):
    directory = tmp_path / "cartopy" / "shapefiles" / "natural_earth" / "physical"
    directory.mkdir(parents=True)
    for resolution in ("110m", "50m"):
        with shapefile.Writer(
            str(directory / f"ne_{resolution}_coastline"), shapeType=shapefile.POLYLINE
        ) as writer:
            writer.field("name", "C")
            writer.line([[[-35, -45], [-30, -10], [-35, 20], [-20, 40]]])
            writer.record("synthetic coastline")
    monkeypatch.setitem(cartopy.config, "data_dir", tmp_path / "cartopy")
    monkeypatch.setitem(cartopy.config, "pre_existing_data_dir", tmp_path / "empty")

    def refuse_download(*args, **kwargs):
        raise AssertionError("protocol tests must stay offline")

    monkeypatch.setattr(Downloader, "_urlopen", refuse_download)
    dates = dict(start="2008-07-01", end="2008-07-31")
    figures = {
        "map": plot_map(np.full((192, 288), 0.2), source="modis", **dates),
        "panels": map_panels(
            [np.full((192, 288), 0.2), np.full((192, 288), 0.3)], ["modis", "viirs"], **dates
        ),
        "sites": plot_site_map(
            [-110, -80], [30, 45], [-0.1, 0.1], [30, 50], kind="bias", source="modis", **dates
        ),
        "region": plot_map(np.full((192, 288), 0.2), source="modis", region="western_us", **dates),
    }
    assert validate_rendered_figures(figures)["passed"]


@pytest.mark.parametrize("value", [0, np.nan, 0.04, 0.15, 0.3, 0.6, 1.4])
def test_geosit_scale_accepts_empty_low_and_capped_fields(value):
    fig, _, _, _ = field(values=np.full((5, 5), value))
    assert validate_rendered_figures({"map": fig})["passed"]


@pytest.mark.parametrize(
    "kind",
    ["bias", "difference", "fraction", "count", "uncertainty", "chi2", "rmse", "correlation"],
)
def test_other_field_scales_pass(kind):
    assert validate_rendered_figures({"field": field(kind)[0]})["passed"]


@pytest.mark.parametrize(
    "title",
    [
        "AOD 2008",
        "AOD vs MODIS",
        "AOD Mean",
        "AOD N = 50",
        "AOD R = 0.8",
        "aod_weighted_mean",
        "AOD GLS",
        "AOD ρ",
        "AOD x",
        "AOD Scatter",
        "AOD Histogram",
        "AOD Time Series",
    ],
)
def test_title_rules(title):
    fig, axis, _ = basic()
    axis.set_title(title)
    assert "title contains" in failures(fig)


@pytest.mark.parametrize("text", ["aod_weighted_mean", "noaa20", "modis", "x", "y"])
def test_raw_display_names_and_pairing_labels(text):
    fig, axis, _ = basic()
    axis.set_xlabel(text)
    assert "text exposes" in failures(fig)


def test_preformatted_experiment_labels_preserve_acronyms():
    assert labeling.source_display_name("GLS (ρ = 0.5)") == "GLS (ρ = 0.5)"
    assert labeling.bias_label("GLS (ρ = 0.5)", "Count", "1") == "GLS (ρ = 0.5) − Count-Weighted"


def test_missing_title_metadata_and_suite_completeness(tmp_path):
    fig, axis, _ = basic()
    axis.set_title("")
    assert "visible title" in failures(fig)
    del fig._sarb_plot_metadata
    assert "register plot metadata" in failures(fig)
    assert "contains no figures" in str(validate_rendered_figures({}, raise_on_failure=False))
    assert "unique" in str(
        validate_rendered_figures([("a", fig), ("a", fig)], raise_on_failure=False)
    )
    with pytest.raises(PlotProtocolError) as error:
        validate_rendered_figures(
            {"a": fig}, expected_figures=["b"], report_path=tmp_path / "failed.json"
        )
    assert not error.value.report["passed"]
    assert json.loads((tmp_path / "failed.json").read_text()) == error.value.report


@pytest.mark.parametrize("layer", ["mesh", "image", "scatter", "filled_contours", "histogram"])
def test_unregistered_dense_layers_must_be_rasterized(layer):
    fig, ax, _ = basic()
    data = np.arange(25).reshape(5, 5)
    if layer == "mesh":
        ax.pcolormesh(data)
    elif layer == "image":
        ax.imshow(data)
    elif layer == "scatter":
        ax.scatter([1, 2], [1, 2])
    elif layer == "filled_contours":
        ax.contourf(data)
    else:
        ax.hist(np.arange(20))
    assert "dense artist is not rasterized" in failures(fig)


def test_contour_lines_must_remain_vector():
    fig, ax, _ = basic()
    lines = ax.contour(np.arange(25).reshape(5, 5))
    assert validate_rendered_figures({"contours": fig})["passed"]
    lines.set_rasterized(True)
    assert "line ContourSet must remain vector" in failures(fig)


@pytest.mark.parametrize("target", ["text", "axis", "xaxis", "yaxis", "spine", "colorbar"])
def test_vector_components(target):
    fig, ax, metadata, artist = field()
    if target == "text":
        ax.title.set_rasterized(True)
    elif target == "axis":
        ax.set_rasterized(True)
    elif target == "xaxis":
        ax.xaxis.set_rasterized(True)
    elif target == "yaxis":
        ax.yaxis.set_rasterized(True)
    elif target == "spine":
        ax.spines["left"].set_rasterized(True)
    else:
        colorbar = fig.colorbar(artist, ax=ax, ticks=aod_colorbar_ticks(artist.norm.boundaries))
        metadata["colorbars"] = [colorbar]
        colorbar.solids.set_rasterized(True)
    assert "must remain vector" in failures(fig)


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda a: a.set_norm(Normalize(0, 1)), "BoundaryNorm"),
        (lambda a: a.set_cmap("viridis"), "requires turbo"),
        (lambda a: a.set_norm(BoundaryNorm(geosit_aod_levels([1]), 256)), "extend=max"),
        (lambda a: a.set_norm(BoundaryNorm([-1, 0, 1], 256, extend="max")), "increase from zero"),
        (lambda a: a.set_norm(BoundaryNorm([0, 0.5, 2], 256, extend="max")), "at most one"),
        (lambda a: a.set_norm(BoundaryNorm([0, 0.1, 0.5, 1], 256, extend="max")), "GEOSIT levels"),
    ],
)
def test_invalid_aod_scales(mutation, reason):
    fig, _, _, artist = field()
    mutation(artist)
    assert reason in failures(fig)


def test_aod_panels_must_share_boundaries_and_aliases_are_checked():
    fig, ax, metadata, artist = field()
    other = ax.pcolormesh(
        np.zeros((2, 2)),
        norm=BoundaryNorm(geosit_aod_levels([1]), 256, extend="max"),
        cmap="turbo",
        rasterized=True,
    )
    fig._davinci_absolute_aod_artists = [other]
    assert "do not share one scale" in failures(fig)
    metadata["field_artists"] = []
    fig._sarb_absolute_aod_artists = [artist]
    assert "do not share one scale" in failures(fig)


@pytest.mark.parametrize(
    "kind",
    ["bias", "difference", "fraction", "count", "uncertainty", "chi2", "rmse", "correlation"],
)
def test_wrong_field_norm_and_colormap(kind):
    fig, _, _, artist = field(kind)
    artist.set_cmap("plasma")
    assert "field_scales" in failures(fig)
    artist.set_norm(Normalize(-1, 2))
    assert "requires" in failures(fig) or "require" in failures(fig)


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda c: c.set_label("AOD"), "no label"),
        (lambda c: c.set_ticks([0, 0.123, 0.456]), "sparse GEOSIT ticks"),
    ],
)
def test_aod_colorbar_rules(mutation, reason):
    fig, ax, metadata, artist = field()
    colorbar = fig.colorbar(artist, ax=ax, ticks=aod_colorbar_ticks(artist.norm.boundaries))
    colorbar.solids.set_rasterized(False)
    metadata["colorbars"] = [colorbar]
    assert validate_rendered_figures({"map": fig})["passed"]
    mutation(colorbar)
    assert reason in failures(fig)


def test_date_ticks_require_rotation():
    fig, ax, metadata = basic()
    metadata["date_axes"] = [ax]
    assert "rotate 45" in failures(fig)


@pytest.mark.parametrize(
    "role", ["title", "subtitle", "panel", "xlabel", "ylabel", "annotation", "gridlabel"]
)
def test_clipped_text_by_role(role):
    fig, ax, metadata = basic()
    fig.set_layout_engine(None)
    if role == "title":
        text = ax.title
    elif role == "xlabel":
        text = ax.xaxis.label
    elif role == "ylabel":
        text = ax.yaxis.label
    elif role in {"annotation", "subtitle"}:
        text = ax.text(0.5, 0.5, "AOD")
    else:
        text = fig.text(0.5, 0.5, "AOD")
    text.set_transform(fig.transFigure)
    text.set_position((1.1, 1.1))
    if role == "gridlabel":
        metadata["gridliners"] = [SimpleNamespace(label_artists=[text])]
    assert "clipped by the figure" in failures(fig)


@pytest.mark.parametrize(
    "role", ["title_subtitle", "panel", "xlabel", "ylabel", "annotation", "legend", "gridlabel"]
)
def test_overlapping_text_by_role(role):
    fig, ax, metadata = basic()
    fig.set_layout_engine(None)
    first = fig.text(0.5, 0.6, "AOD", ha="center")
    if role == "title_subtitle":
        second = ax.set_title("AOD", y=0.6)
    elif role == "xlabel":
        ax.xaxis.set_label_coords(0.5, 0.6, transform=fig.transFigure)
        second = ax.xaxis.label
    elif role == "ylabel":
        ax.yaxis.set_label_coords(0.5, 0.6, transform=fig.transFigure)
        second = ax.yaxis.label
    elif role in {"annotation", "gridlabel"}:
        second = ax.text(0.5, 0.5, "MODIS AOD")
    elif role == "legend":
        ax.plot([0, 1], [0, 1], label="MODIS")
        legend = ax.legend(loc="center", bbox_to_anchor=(0.5, 0.6), bbox_transform=fig.transFigure)
        second = first
    else:
        second = fig.text(0.5, 0.6, "MODIS AOD")
    second.set_transform(fig.transFigure)
    second.set_position((0.5, 0.6))
    if role == "gridlabel":
        metadata["gridliners"] = [SimpleNamespace(label_artists=[second])]
    assert "overlaps" in failures(fig)


def test_colorbar_tick_overlap_is_rejected():
    fig, ax, metadata, artist = field("fraction")
    colorbar = fig.colorbar(artist, ax=ax)
    colorbar.solids.set_rasterized(False)
    colorbar.set_ticks([0.1, 0.101], labels=["0.100", "0.101"])
    metadata["colorbars"] = [colorbar]
    assert "tick" in failures(fig) and "overlaps" in failures(fig)


def test_clipped_legend_and_left_panel_title_rules():
    fig, ax, _ = basic()
    fig.set_layout_engine(None)
    ax.plot([0, 1], [0, 1], label="MODIS")
    ax.legend(loc="center", bbox_to_anchor=(1.1, 1.1), bbox_transform=fig.transFigure)
    ax.set_title("AOD 2008", loc="left")
    result = failures(fig)
    assert "legend is clipped" in result
    assert "title contains dates" in result


def test_active_site_subtitle():
    assert (
        labeling.active_sites_subtitle("2008-07-01", "2008-07-31", 2, 8)
        == "2008-07-01 – 2008-07-31 | Active Sites: 2–8"
    )
    assert labeling.active_sites_subtitle(None, None, 2, 2) == "Active Sites: 2"
    with pytest.raises(ValueError, match="ordered"):
        labeling.active_sites_subtitle(None, None, 3, 2)
