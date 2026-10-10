"""Offline map protocol: scales, geometry, vector context and raster PDF fill."""

import re
import warnings
from pathlib import Path

import cartopy
import matplotlib
import numpy as np
import pytest
import shapefile
from cartopy.io import Downloader
from cartopy.mpl.feature_artist import FeatureArtist
from matplotlib.colors import BoundaryNorm, TwoSlopeNorm
from matplotlib.text import Text

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from sarb_aod.evaluation.regions import get_region
from sarb_aod.grid.spec import load_grid
from sarb_aod.plots import maps
from sarb_aod.plots.style import aod_colorbar_ticks, geosit_aod_levels, ncar_style


@pytest.fixture
def coastline_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tiny real shapefile keeps unit tests offline and machine-independent."""
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

    def refuse_download(*args: object, **kwargs: object) -> None:
        raise AssertionError("ordinary map rendering must not download data")

    monkeypatch.setattr(Downloader, "_urlopen", refuse_download)
    return tmp_path / "cartopy"


@pytest.fixture(autouse=True)
def publication_style() -> None:
    with ncar_style("publication", use_seaborn=False):
        yield
    plt.close("all")


def _field() -> np.ndarray:
    grid = load_grid()
    return 0.4 + 0.2 * np.cos(np.deg2rad(grid.lat))[:, None] * np.cos(np.deg2rad(grid.lon))[None, :]


def _metadata(figure: object) -> dict:
    return getattr(figure, "_sarb_plot_metadata")


def test_absolute_map_scale_bounds_context_and_vector_text(coastline_cache: Path) -> None:
    data = _field()
    figure = maps.plot_map(data, source="modis", start="2008-07-01", end="2008-07-31")
    metadata = _metadata(figure)
    ax, artist = metadata["data_axes"][0], metadata["dense_artists"][0]
    assert isinstance(artist.norm, BoundaryNorm)
    np.testing.assert_array_equal(artist.norm.boundaries, geosit_aod_levels(data))
    assert artist.cmap.name == "turbo"
    assert all(item.get_rasterized() for item in metadata["dense_artists"])
    assert all(not item.get_rasterized() for item in figure.findobj(Text))
    assert metadata["titles"][0].get_text() == "MODIS AOD"
    assert metadata["subtitles"][0].get_text() == "2008-07-01 – 2008-07-31 | Mean: 0.400"
    assert metadata["colorbars"][0].extend == "max"
    assert metadata["colorbars"][0].ax.get_xlabel() == ""
    assert not metadata["colorbars"][0].solids.get_rasterized()
    np.testing.assert_array_equal(
        metadata["colorbars"][0].get_ticks(), aod_colorbar_ticks(artist.norm.boundaries)
    )
    feature_artists = [item for item in ax.collections if isinstance(item, FeatureArtist)]
    assert len(feature_artists) == 1 and feature_artists[0].get_gid() == "coastline"
    assert not feature_artists[0].get_rasterized()
    assert metadata["gridliners"][0].collection_kwargs["linestyle"] == "-"
    assert not metadata["gridliners"][0].top_labels
    assert not metadata["gridliners"][0].right_labels
    np.testing.assert_allclose(ax.get_extent(), [-180, 180, -90, 90])
    # Longitude cells/data columns must move together. The first displayed
    # cell is centered at -180 (original longitude 180, column 144).
    np.testing.assert_allclose(artist.get_coordinates()[0, :2, 0], [-180.625, -179.375])
    assert artist.get_coordinates()[:, :, 1].min() == -90
    assert artist.get_coordinates()[:, :, 1].max() == 90
    np.testing.assert_allclose(artist.get_array()[:, 0], data[:, 144])


def test_panels_compute_shared_levels_once(
    coastline_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = maps.geosit_aod_levels
    calls = []

    def record(values: object) -> np.ndarray:
        calls.append(np.asarray(values))
        return original(values)

    monkeypatch.setattr(maps, "geosit_aod_levels", record)
    fields = [np.full((192, 288), value) for value in (0.04, 0.3, 1.4)]
    figure = maps.map_panels(fields, ["modis", "viirs", "mixed"], columns=2)
    metadata = _metadata(figure)
    assert len(calls) == 1 and calls[0].size == 3 * 192 * 288
    norms = [item["artist"].norm for item in metadata["field_artists"]]
    assert all(norm is norms[0] for norm in norms)
    np.testing.assert_array_equal(norms[0].boundaries, geosit_aod_levels([1]))
    assert len(metadata["colorbars"]) == 1
    assert len(metadata["data_axes"]) == 3
    assert figure.get_layout_engine().__class__.__name__ == "ConstrainedLayoutEngine"
    figure.canvas.draw()


@pytest.mark.parametrize(
    "kind,cmap",
    [
        ("difference", "RdBu_r"),
        ("bias", "RdBu_r"),
        ("fraction", "gray_r"),
        ("count", "viridis"),
        ("uncertainty", "viridis"),
        ("chi2", "viridis"),
        ("rmse", "viridis"),
        ("correlation", "viridis"),
    ],
)
def test_scale_policy_for_other_fields(kind: str, cmap: str) -> None:
    values = np.concatenate([np.linspace(-0.2, 0.7, 1000), [1000]])
    scale = maps.field_scale(values, kind)
    assert scale.cmap.name == cmap
    if kind in ("difference", "bias"):
        assert isinstance(scale.norm, TwoSlopeNorm)
        assert scale.norm.vcenter == 0
        assert scale.norm.vmin == -scale.norm.vmax
        assert scale.norm.vmax == pytest.approx(np.percentile(np.abs(values), 98))
    elif kind in ("fraction", "correlation"):
        assert (scale.norm.vmin, scale.norm.vmax) == (0, 1)
    else:
        assert scale.norm.vmin == 0 and 0.7 <= scale.norm.vmax < 1


def test_nice_robust_limits_empty_constant_and_outlier() -> None:
    assert maps.nice_robust_limits([np.nan, -999]) == (0, 1)
    assert maps.nice_robust_limits([0, 0]) == (0, 1)
    np.testing.assert_allclose(maps.nice_robust_limits([0.1234]), [0, 0.125])
    assert maps.nice_robust_limits(np.concatenate([np.linspace(0, 10, 1000), [1000]])) == (0, 10)
    with pytest.raises(ValueError, match="percentiles"):
        maps.nice_robust_limits([1, 2], (98, 2))


def test_cos_latitude_mean_ignores_fill_and_uses_region_centers() -> None:
    lat, lon = [-60, 0, 60], [-120, 0, 120]
    data = np.array([[1, 1, 1], [2, 2, 2], [5, 5, 5]], dtype=float)
    assert maps.area_mean(data, lat, lon) == pytest.approx(2.5)
    assert maps.area_mean(data, lat, lon, "southern_ocean") == pytest.approx(1)
    data[0, 0] = -999
    assert maps.area_mean(data, lat, lon) == pytest.approx(29 / 11)
    assert np.isnan(maps.area_mean(np.full((3, 3), -999), lat, lon))


def test_regional_map_uses_named_extent_and_subset_title(coastline_cache: Path) -> None:
    data = _field()
    figure = maps.plot_map(data, region="western_us", start="2008-07-01", end="2008-07-31")
    metadata = _metadata(figure)
    np.testing.assert_allclose(
        metadata["data_axes"][0].get_extent(), get_region("western_us").extent
    )
    assert metadata["titles"][0].get_text() == "AOD - Western US"
    grid = load_grid()
    mean = maps.area_mean(data, grid.lat, grid.lon, "western_us")
    assert metadata["subtitles"][0].get_text().endswith(f"Mean: {mean:.3f}")


@pytest.mark.parametrize("panels", [False, True])
def test_shallow_latitude_band_title_spacing_passes_protocol(
    coastline_cache: Path, panels: bool
) -> None:
    from sarb_aod.plots.protocol import validate_rendered_figures

    options = dict(region="southern_ocean", start="2020-09-01", end="2020-09-30")
    figure = (
        maps.map_panels([_field(), _field()], ["modis", "viirs"], **options)
        if panels
        else maps.plot_map(_field(), **options)
    )
    assert validate_rendered_figures({"southern_ocean": figure})["passed"]


def test_site_sizes_and_site_metric_map(coastline_cache: Path) -> None:
    sizes = maps.site_marker_sizes([0, 1, 25, 100, np.nan, -1])
    np.testing.assert_allclose(sizes, [0, 28, 60, 100, 0, 0])
    figure = maps.plot_site_map(
        [-120, -110, -100, -90],
        [35, 40, 42, 45],
        [0.1, 0.2, 0.3, 0.4],
        [1, 25, 100, 0],
        kind="rmse",
    )
    metadata = _metadata(figure)
    artist = metadata["dense_artists"][0]
    assert artist.get_rasterized()
    assert len(artist.get_offsets()) == 3
    np.testing.assert_allclose(artist.get_sizes(), [28, 60, 100])
    assert metadata["subtitles"][0].get_text() == "Sites: 3"
    assert artist.cmap.name == "viridis" and artist.norm.vmin == 0


def _real_image_objects(path: Path) -> set[int]:
    data = path.read_bytes()
    objects = {
        int(number): body
        for number, body in re.findall(rb"(\d+)\s+0\s+obj\s*(.*?)endobj", data, re.S)
    }
    images = {
        number for number, body in objects.items() if re.search(rb"/Subtype\s*/Image\b", body)
    }
    masks = {
        int(number)
        for body in objects.values()
        for number in re.findall(rb"/SMask\s+(\d+)\s+0\s+R", body)
    }
    return images - masks


@pytest.mark.parametrize("rasterized", [True, False])
def test_pdf_has_raster_image_only_for_dense_raster_version(
    coastline_cache: Path, tmp_path: Path, rasterized: bool
) -> None:
    figure = maps.plot_map(_field(), rasterized=rasterized)
    path = tmp_path / "map.pdf"
    figure.savefig(path, bbox_inches=figure.bbox_inches)
    assert bool(_real_image_objects(path)) is rasterized


@pytest.mark.parametrize("plot_type", ["pcolormesh", "contourf"])
@pytest.mark.parametrize("value", [0.4, -0.02])
def test_global_dateline_has_no_blank_column_or_warning(
    coastline_cache: Path, plot_type: str, value: float
) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        warnings.filterwarnings(
            "ignore",
            message="The locs attribute was deprecated in Matplotlib 3.11.*",
            category=matplotlib.MatplotlibDeprecationWarning,
            module=r"cartopy\.mpl\.ticker",
        )
        figure = maps.plot_map(np.full((192, 288), value), plot_type=plot_type)
        figure.canvas.draw()
    ax = _metadata(figure)["data_axes"][0]
    rgba = np.asarray(figure.canvas.buffer_rgba())
    height = rgba.shape[0]
    # Sample just inside both datelines at a non-gridline, open-ocean latitude.
    # Missing contour closure would leave a white strip here.
    for longitude in (-179.8, -179.2, 179.2, 179.8):
        px, py = ax.transData.transform((longitude, -12))
        pixel = rgba[height - 1 - int(round(py)), int(round(px)), :3]
        assert not np.all(pixel > 245), (plot_type, value, longitude, pixel)


def test_missing_cache_fails_before_any_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(cartopy.config, "data_dir", tmp_path / "empty")
    monkeypatch.setitem(cartopy.config, "pre_existing_data_dir", tmp_path / "also_empty")
    with pytest.raises(FileNotFoundError, match="prefetch-maps"):
        maps.plot_map(_field())


def test_invalid_shapes_and_scales_are_rejected() -> None:
    with pytest.raises(ValueError, match="AOD levels"):
        maps.field_scale([0.1], aod_levels=[0, 0.2, 0.1])
    with pytest.raises(ValueError, match="only applies"):
        maps.field_scale([0.1], "fraction", aod_levels=[0, 1])
    with pytest.raises(ValueError, match="matching nonempty"):
        maps.map_panels([], [])
    with pytest.raises(ValueError, match="marker size range"):
        maps.site_marker_sizes([1], (100, 20))
