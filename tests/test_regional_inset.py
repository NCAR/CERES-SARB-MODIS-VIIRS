"""Matched-site inset geography, offline coastlines, and drawing order."""

from datetime import date

import cartopy
import matplotlib
import numpy as np
import pandas as pd
import pytest
import shapefile
from cartopy.io import Downloader

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from sarb_aod.evaluation.regions import Region
from sarb_aod.plots.protocol import validate_rendered_figures
from sarb_aod.plots.regional_inset import _choose_position, add_regional_inset
from sarb_aod.plots.series import regional_time_series


@pytest.fixture(autouse=True)
def offline_coastline(tmp_path, monkeypatch):
    directory = tmp_path / "cartopy/shapefiles/natural_earth/physical"
    directory.mkdir(parents=True)
    with shapefile.Writer(str(directory / "ne_110m_coastline")) as writer:
        writer.field("name", "C")
        writer.line([[[-124, 25], [-121, 35], [-125, 48]]])
        writer.record("coastline")
    monkeypatch.setitem(cartopy.config, "data_dir", tmp_path / "cartopy")
    monkeypatch.setitem(cartopy.config, "pre_existing_data_dir", tmp_path / "empty")

    def no_download(*args, **kwargs):
        raise AssertionError("Inset coastlines must use the local cache")

    monkeypatch.setattr(Downloader, "_urlopen", no_download)
    yield
    plt.close("all")


def test_inset_uses_only_finite_paired_sites_and_draws_beneath_series(monkeypatch):
    pairs = pd.DataFrame(
        {
            "site": ["A", "A", "B", "unpaired", "outside", "invalid"],
            "time": pd.to_datetime(["2008-07-01"] * 6),
            "region": "western_us",
            "longitude": [-120, -120, -110, -105, 10, np.nan],
            "latitude": [35, 35, 45, 30, 35, 40],
            "x": [0.1] * 6,
            "y": [0.2, 0.3, 0.4, np.nan, 0.2, 0.2],
        }
    )
    figure = regional_time_series(pairs, "western_us")
    inset = add_regional_inset(figure, pairs, "western_us")
    metadata = figure._sarb_plot_metadata
    context = metadata["regional_inset"]
    axis = metadata["data_axes"][0]
    assert context["sites"].site.tolist() == ["A", "B"]
    np.testing.assert_array_equal(context["markers"].get_offsets(), [[-120, 35], [-110, 45]])
    np.testing.assert_allclose(inset.get_xlim(), [-125, -95])
    np.testing.assert_allclose(inset.get_ylim(), [25, 48])
    assert inset in axis.child_axes
    assert inset.patch.get_alpha() == 0
    assert context["markers"].get_rasterized()
    assert not context["outline"].get_rasterized()
    assert not context["coastline"].get_rasterized()
    assert all(inset.get_zorder() < band.get_zorder() for band in axis.collections)
    assert all(inset.get_zorder() < line.get_zorder() for line in axis.lines)

    # Confirm actual child-Axes draw ordering, not only nominal zorder values.
    drawn = []
    for name, artist in [
        ("map", inset),
        *[("band", a) for a in axis.collections],
        ("dates", axis.xaxis),
    ]:
        original = artist.draw

        def record(renderer, *, role=name, draw=original):
            drawn.append(role)
            return draw(renderer)

        monkeypatch.setattr(artist, "draw", record)
    assert validate_rendered_figures({"regional": figure})["passed"]
    assert drawn.index("map") < drawn.index("band")
    assert drawn.index("map") < drawn.index("dates")
    assert_inside_parent(inset, axis)
    assert context["corner"] in context["placement_scores"]
    assert context["placement_scores"][context["corner"]]["text_overlap"] == 0


def assert_inside_parent(inset, parent):
    inner, outer = inset.get_window_extent(), parent.get_window_extent()
    assert inner.x0 >= outer.x0 - 0.01
    assert inner.y0 >= outer.y0 - 0.01
    assert inner.x1 <= outer.x1 + 0.01
    assert inner.y1 <= outer.y1 + 0.01


@pytest.mark.parametrize("free_corner", ["lower right", "upper right", "lower left", "upper left"])
def test_position_choice_uses_filled_band_interiors(free_corner):
    figure, axis = plt.subplots(figsize=(10, 5))
    axis.set(xlim=(0, 1), ylim=(0, 1))
    for corner in ("lower right", "upper right", "lower left", "upper left"):
        if corner == free_corner:
            continue
        x = [0.5, 2] if "right" in corner else [-1, 0.5]
        low, high = (0.5, 2) if "upper" in corner else (-1, 0.5)
        axis.fill_between(x, low, high, alpha=0.22)
    _, chosen, scores = _choose_position(axis, 2)
    assert chosen == free_corner
    assert scores[chosen]["band_overlap"] == 0
    assert all(score["band_overlap"] > 0 for corner, score in scores.items() if corner != chosen)


def test_position_choice_detects_crossing_lines_and_preserves_nan_gaps():
    figure, axis = plt.subplots(figsize=(10, 5))
    axis.set(xlim=(0, 1), ylim=(0, 1))
    (line,) = axis.plot([-1, 2], [0.1, 0.1], marker="o")
    _, chosen, scores = _choose_position(axis, 2)
    assert chosen == "upper right"
    assert scores["lower right"]["curve_overlap"] > 0
    assert scores["lower right"]["marker_overlap"] == 0
    line.set_data([-1, np.nan, 2], [0.1, np.nan, 0.1])
    _, chosen, scores = _choose_position(axis, 2)
    assert chosen == "lower right"
    assert all(score["curve_overlap"] == 0 for score in scores.values())


def test_position_choice_avoids_legend_and_time_before_data():
    figure, axis = plt.subplots(figsize=(10, 5))
    axis.set(xlim=(0, 1), ylim=(0, 1))
    axis.plot([0, 1], [0.95, 0.95], label="AERONET")
    axis.legend(loc="lower right")
    axis.set_xlabel("Time")
    axis.xaxis.set_label_coords(0.15, 0.1)
    _, chosen, scores = _choose_position(axis, 2)
    assert scores["lower right"]["text_overlap"] > 0
    assert scores["lower left"]["text_overlap"] > 0
    assert chosen == "lower center"
    assert scores[chosen]["text_overlap"] == 0


def test_position_choice_scores_markers_and_actual_wide_map_footprint():
    figure, axis = plt.subplots(figsize=(10, 5))
    axis.set(xlim=(0, 1), ylim=(0, 1))
    axis.scatter([0.9], [0.05])
    # The broad band is inside the nominal lower-right 0.34 x 0.47 box,
    # but above the shorter aspect-fitted map; it must not penalize that map.
    axis.fill_between([-1, 2], 0.3, 0.5, alpha=0.22)
    _, chosen, scores = _choose_position(axis, 4)
    assert chosen == "upper right"
    assert scores["lower right"]["marker_overlap"] == 1
    assert scores["lower right"]["band_overlap"] == 0
    assert scores["lower right"]["bounds"][3] < 0.25


@pytest.mark.parametrize(
    "free_position",
    ["lower right", "upper right", "lower left", "upper left", "upper center", "lower center"],
)
def test_all_six_positions_can_win_and_centered_maps_clear_time(free_position):
    figure, axis = plt.subplots(figsize=(10, 5))
    axis.set(xlim=(0, 1), ylim=(0, 1))
    axis.set_xlabel("Time")
    axis.xaxis.set_label_coords(0.5, 0.015)
    axis.xaxis.label.set_verticalalignment("bottom")
    for position in (
        "lower right",
        "upper right",
        "lower left",
        "upper left",
        "upper center",
        "lower center",
    ):
        if position == free_position:
            continue
        column = 2 if "right" in position else 1 if "center" in position else 0
        low, high = (0.6, 2) if "upper" in position else (-1, 0.4)
        axis.fill_between([column / 3, (column + 1) / 3], low, high, alpha=0.22)
    figure._sarb_plot_metadata = {"data_axes": [axis], "dense_artists": [], "gridliners": []}
    pairs = pd.DataFrame(columns=["site", "time", "longitude", "latitude", "x", "y"])
    inset = add_regional_inset(figure, pairs, Region("narrow", "Narrow", (0, 12, 0, 20)))
    context = figure._sarb_plot_metadata["regional_inset"]
    assert context["position"] == context["corner"] == free_position
    assert len(context["placement_scores"]) == 6
    assert context["placement_scores"][free_position]["band_overlap"] == 0
    figure.canvas.draw()
    assert_inside_parent(inset, axis)
    if "center" in free_position:
        extent = inset.get_window_extent()
        assert extent.x0 + extent.width / 2 == pytest.approx(axis.bbox.x0 + axis.bbox.width / 2)
        assert inset.get_anchor() == ("N" if free_position == "upper center" else "S")
        if free_position == "lower center":
            assert extent.y0 > axis.xaxis.label.get_window_extent().y1


def test_inset_preserves_dateline_extent_and_region_activity_dates():
    region = Region(
        "dateline", "Dateline", (170, -170, -20, 20), date(2020, 9, 8), date(2020, 9, 14)
    )
    pairs = pd.DataFrame(
        {
            "site": ["west", "east", "outside", "inactive"],
            "time": pd.to_datetime(["2020-09-09"] * 3 + ["2020-09-01"]),
            "region": "global",
            "longitude": [175, -175, 0, 178],
            "latitude": [5, -5, 0, 8],
            "x": 0.1,
            "y": 0.2,
        }
    )
    figure = regional_time_series(pairs, "global")
    inset = add_regional_inset(figure, pairs, region)
    context = figure._sarb_plot_metadata["regional_inset"]
    assert context["sites"].site.tolist() == ["west", "east"]
    np.testing.assert_allclose(inset.get_xlim(), [-10, 10])
    np.testing.assert_allclose(context["outline"].get_xdata(), [-10, 10, 10, -10, -10])
    figure.canvas.draw()
    assert_inside_parent(inset, figure._sarb_plot_metadata["data_axes"][0])


@pytest.mark.parametrize("region", ["global", "southern_ocean"])
def test_empty_and_full_longitude_regions_keep_valid_map_extent(region):
    pairs = pd.DataFrame(columns=["site", "time", "region", "longitude", "latitude", "x", "y"])
    figure = regional_time_series(pairs, region, start="2008-07-01", end="2008-07-31")
    inset = add_regional_inset(figure, pairs, region)
    assert figure._sarb_plot_metadata["regional_inset"]["sites"].empty
    np.testing.assert_allclose(inset.get_xlim(), [-180, 180])
    assert validate_rendered_figures({"empty": figure})["passed"]
    axis = figure._sarb_plot_metadata["data_axes"][0]
    assert_inside_parent(inset, axis)
    for size in ((12, 3), (6, 8)):
        figure.set_size_inches(*size)
        figure.canvas.draw()
        assert_inside_parent(inset, axis)
