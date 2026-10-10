"""Scientific values, complete sampling, vector text, and renderer conventions."""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest
from matplotlib.colors import TwoSlopeNorm
from matplotlib.text import Text

from sarb_aod.evaluation.calculator import binned_bias as calculate_binned_bias
from sarb_aod.evaluation.metrics import expected_error, get_metric, regression
from sarb_aod.plots import labeling
from sarb_aod.plots.scatter import density_scatter, density_scatter_panels
from sarb_aod.plots.series import TimedValues, regional_time_series, site_time_series
from sarb_aod.plots.style import X_COLOR, Y_COLOR
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
    yield
    plt.close("all")


@pytest.fixture
def paired():
    rng = np.random.default_rng(813)
    x = rng.gamma(2, 0.15, 1000)
    return x, 1.03 * x + 0.0073 + rng.normal(0, 0.03, len(x))


def assert_artists(figure):
    figure.canvas.draw()
    metadata = figure._sarb_plot_metadata
    for artist in metadata["dense_artists"]:
        assert artist.get_rasterized()
    for artist in figure.findobj(match=Text):
        assert not artist.get_rasterized()
    for title in metadata["titles"]:
        labeling.validate_title(title.get_text())
    for colorbar in metadata["colorbars"]:
        if colorbar.solids is not None:
            assert not colorbar.solids.get_rasterized()
    return metadata


def test_site_series_keeps_unmatched_grid_windows_and_rasterizes_bands():
    times = pd.date_range("2008-07-01", periods=8, freq="3h")
    figure = site_time_series(
        "Mauna_Loa",
        aeronet_points=TimedValues(times[:3], [0.1, 0.2, 0.3]),
        aeronet_windows=TimedValues(times[[0, 2]], [0.15, 0.25]),
        grid=TimedValues(times, np.arange(8) / 20, np.full(8, 0.03)),
        overpasses=TimedValues(times[[1, 6]], [0.2, 0.3]),
    )
    metadata = assert_artists(figure)
    axis = metadata["data_axes"][0]
    grid = next(line for line in axis.lines if line.get_label().startswith("MODIS Grid"))
    aeronet = next(line for line in axis.lines if line.get_label().startswith("AERONET Window"))
    assert len(grid.get_xdata()) == 8
    assert len(aeronet.get_xdata()) == 2
    assert grid.get_color() == Y_COLOR
    assert aeronet.get_color() == X_COLOR
    assert "mean ± σ" in grid.get_label()
    assert "mean ± σ" not in aeronet.get_label()
    assert metadata["titles"][0].get_text() == "AOD - Mauna Loa"
    assert all(tick.get_rotation() == 45 for tick in axis.get_xticklabels())
    assert len(metadata["dense_artists"]) == 3


def test_regional_series_uses_equal_site_daily_means():
    pairs = pd.DataFrame(
        {
            "site": ["A", "A", "A", "B"],
            "time": pd.to_datetime(["2008-07-01"] * 4),
            "region": ["western_us"] * 4,
            "x": [0.1, 0.1, 0.1, 0.5],
            "y": [0.2, 0.2, 0.2, 0.6],
        }
    )
    figure = regional_time_series(pairs, "western_us")
    metadata = assert_artists(figure)
    regional = metadata["regional_daily"]
    assert regional.x.iloc[0] == pytest.approx(0.3)
    assert regional.y.iloc[0] == pytest.approx(0.4)
    assert regional.active_sites.iloc[0] == 2
    assert metadata["titles"][0].get_text() == "AOD - Western US"


def test_numeric_times_are_unix_seconds_and_invalid_sigma_has_no_band_label():
    times = np.array([1214870400.0, 1214881200.0])
    values = TimedValues(times, [0.1, 0.2], [np.nan, -1])
    figure = site_time_series(
        "Mauna_Loa", aeronet_points=values, aeronet_windows=values, grid=values
    )
    metadata = assert_artists(figure)
    assert metadata["subtitles"][0].get_text() == "2008-07-01"
    assert all("±" not in line.get_label() for line in metadata["data_axes"][0].lines)


def test_density_scatter_statistics_regressions_and_line_artists(paired):
    x, y = paired
    figure = density_scatter(x, y, start="2008-07-01", end="2008-07-31")
    metadata = assert_artists(figure)
    axis = metadata["data_axes"][0]
    for metric in ("N", "MB", "RMSE", "R"):
        assert metadata["metrics"][metric] == get_metric(metric).compute(x, y)
    assert metadata["metrics"]["pct_ee"] == expected_error(x, y)["pct_ee"]
    assert metadata["regression"] == regression(x, y)
    assert metadata["statistics_annotations"][0].get_text() == labeling.statistics_text(
        metadata["metrics"]
    )
    assert axis.get_xlabel() == "AERONET AOD"
    assert axis.get_ylabel() == "MODIS AOD"
    labels = [line.get_label() for line in axis.lines]
    assert "1:1" in labels and "Expected Error" in labels
    assert any(label.startswith("OLS: slope ") for label in labels)
    assert any(label.startswith("RMA: slope ") for label in labels)
    assert all(not line.get_rasterized() for line in axis.lines)
    assert len(metadata["dense_artists"]) == 1


def test_binned_bias_delegates_to_calculator(paired):
    x, y = paired
    figure = binned_bias(x, y, edges=[0, 0.1, 0.5, 2], min_samples=2)
    metadata = assert_artists(figure)
    pd.testing.assert_frame_equal(
        metadata["binned_statistics"], calculate_binned_bias(x, y, [0, 0.1, 0.5, 2], min_samples=2)
    )
    assert metadata["titles"][0].get_text() == "AOD Bias"


def test_scatter_without_envelope_and_regional_panels(paired):
    from sarb_aod.plots.protocol import validate_rendered_figures

    x, y = paired
    single = density_scatter(x, y, envelope=None)
    metadata = assert_artists(single)
    assert "pct_ee" not in metadata["metrics"]
    assert all(line.get_label() != "Expected Error" for line in metadata["data_axes"][0].lines)
    panels = density_scatter_panels(
        {"western_us": (x, y), "east_asia": (x[:100], y[:100]), "southern_ocean": ([], [])},
        start="2008-07-01",
        end="2008-07-31",
    )
    metadata = assert_artists(panels)
    assert len(metadata["data_axes"]) == 3
    assert metadata["panel_metrics"]["western_us"]["N"] == 1000
    assert metadata["panel_metrics"]["southern_ocean"]["N"] == 0
    assert validate_rendered_figures({"scatter": single, "panels": panels})["passed"]


def test_z_histogram_unit_normal_and_sample_statistics():
    values = np.array([-2, -1, 0, 1, 2, np.nan, np.inf])
    figure = z_histogram(values, bins=5)
    metadata = assert_artists(figure)
    assert metadata["metrics"]["N_z"] == 5
    assert metadata["metrics"]["z_mean"] == 0
    assert metadata["metrics"]["z_std"] == pytest.approx(np.std(values[:5], ddof=1))
    reference = metadata["data_axes"][0].lines[0]
    np.testing.assert_allclose(
        reference.get_ydata(), np.exp(-0.5 * reference.get_xdata() ** 2) / np.sqrt(2 * np.pi)
    )
    assert reference.get_color() == X_COLOR


def test_rho_sweep_keeps_raw_metrics_and_reference_targets():
    metrics = {"RMSE": np.array([0.1000001, 0.09, 0.08]), "z_std": np.array([1.2, 1, 0.8])}
    figure = rho_sweep([0, 0.5, 1], metrics)
    metadata = assert_artists(figure)
    np.testing.assert_array_equal(metadata["data_axes"][0].lines[0].get_ydata(), metrics["RMSE"])
    np.testing.assert_array_equal(metadata["data_axes"][1].lines[1].get_ydata(), [1, 1])
    assert all(axis.get_xlabel() == "Error Correlation (ρ)" for axis in metadata["data_axes"])


def test_scorecard_uses_metric_scales_and_friendly_region_names():
    table = pd.DataFrame(
        {"MB": [-0.02, 0.03], "RMSE": [0.08, 0.1], "R": [0.7, 0.9]},
        index=["western_us", "east_asia"],
    )
    metadata = assert_artists(scorecard(table))
    bias, rmse, correlation = metadata["dense_artists"]
    assert isinstance(bias.norm, TwoSlopeNorm)
    assert bias.cmap.name == "RdBu_r"
    assert rmse.cmap.name == correlation.cmap.name == "viridis"
    assert (correlation.norm.vmin, correlation.norm.vmax) == (0, 1)
    # Both diverging endpoints are dark; normalized value alone cannot decide
    # whether black lettering has enough contrast on the positive-red end.
    assert metadata["data_axes"][0].texts[1].get_color() == "white"
    assert [tick.get_text() for tick in metadata["data_axes"][0].get_yticklabels()] == [
        "Western US",
        "East Asia",
    ]
    pd.testing.assert_frame_equal(metadata["scorecard_values"], table)


def test_taylor_negative_correlations_and_vector_contours(paired):
    x, y = paired
    metadata = assert_artists(taylor_diagram(x, {"modis": y, "viirs": -x}))
    axis = metadata["data_axes"][0]
    assert axis.get_thetamax() == 180
    assert metadata["metrics"]["viirs"]["R"] == pytest.approx(-1)
    assert all(
        not contour.filled and not contour.get_rasterized() for contour in metadata["line_contours"]
    )
    labels = [line.get_label() for line in axis.lines]
    assert labels == ["AERONET", "MODIS", "VIIRS"]


def test_regional_boxes_keep_source_colors_and_rasterize_fliers(paired):
    x, y = paired
    figure = regional_boxplots(
        {
            "western_us": {"modis": y[:100], "aeronet": x[:100]},
            "east_asia": {"modis": y[100:], "aeronet": x[100:]},
        }
    )
    metadata = assert_artists(figure)
    axis = metadata["data_axes"][0]
    assert [text.get_text() for text in axis.get_legend().get_texts()] == ["AERONET", "MODIS"]
    assert len(metadata["dense_artists"]) == 4


def test_regional_boxes_full_campaign_labels_pass_layout_protocol(paired):
    from sarb_aod.evaluation.regions import AEROSOL_REGIONS
    from sarb_aod.plots.protocol import validate_rendered_figures

    x, y = paired
    figure = regional_boxplots(
        {region: {"aeronet": x, "modis": y} for region in ["global", *AEROSOL_REGIONS]},
        start="2008-07-01",
        end="2008-07-31",
    )
    assert validate_rendered_figures({"full_campaign_regions": figure})["passed"]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: density_scatter([], []),
        lambda: z_histogram([]),
        lambda: binned_bias([], []),
        lambda: taylor_diagram([1, 1], {"modis": [2, 2]}),
    ],
)
def test_empty_or_constant_inputs_draw_without_warnings(factory):
    assert_artists(factory())
