"""VIIRS Dark Target maps, spectral identity and UTC coverage windows."""

from datetime import date
from pathlib import Path

import numpy as np
import pytest
from netCDF4 import Dataset

from sarb_aod.config.loader import load_config
from sarb_aod.paths import granule_dir
from sarb_aod.readers import LAND, OCEAN, read_granule
from sarb_aod.readers.base import coverage_times
from sarb_aod.readers.netcdf import NetCDFSource
from sarb_aod.streams import dimension_indexer, resolve_stream
from sarb_aod.timeconv import tai93_to_unix_seconds
from sarb_aod.windows import day_time_edges_seconds

DAY = date(2020, 9, 8)
PRODUCTS = ("AERDT_L2_VIIRS_SNPP", "AERDT_L2_VIIRS_NOAA20")


def test_dt_platforms_share_maps_and_cam7_keeps_average_ocean():
    recipes = [resolve_stream(product, "2.1", "dt") for product in PRODUCTS]
    assert recipes[0].surfaces == recipes[1].surfaces
    for recipe in recipes:
        assert recipe.family == "viirs_dt"
        assert recipe.geolocation.time_kind == "coverage_attrs"
        assert recipe.geolocation.time is None
        assert recipe.surfaces["land"].aod.select == {"Wavelength_Used_Land_2": 1}
        assert recipe.surfaces["ocean"].aod.select == {"Wavelength_Used_ALL": 1}
        assert all(surface.sigma is None for surface in recipe.surfaces.values())
        cam7 = resolve_stream(recipe.product, "2.1", "dt", preset="cam7_v2")
        assert cam7.surfaces == recipe.surfaces


@pytest.mark.real_data
@pytest.mark.parametrize("product", PRODUCTS)
def test_real_dt_combined_identity_and_same_granule_db_windows(product):
    repo = Path(__file__).resolve().parents[1]
    config = load_config(repo / "config/runs/modis_viirs_l2_202009.yaml")
    catalog = config.load_catalog()
    directory = granule_dir(config.paths, catalog, product, "2.1", DAY)
    hhmm = "2024" if product == PRODUCTS[0] else "1936"
    paths = sorted(directory.glob(f"*.{hhmm}.*.nc"))
    if not paths:
        pytest.skip(f"no staged {product} smoke granule under configured data root")
    recipe = resolve_stream(product, "2.1", "dt", catalog=catalog)
    batch = read_granule(paths[0], [recipe], day=DAY)[recipe.stream_id]
    with Dataset(paths[0]) as handle:

        def physical(name):
            return np.asarray(np.ma.asarray(handle[name][:], dtype=np.float64).filled(np.nan))

        lat = physical(recipe.geolocation.lat)
        lon = physical(recipe.geolocation.lon)
        qa = physical(recipe.surfaces["land"].qa.name)
        aods = {}
        for surface in ("land", "ocean"):
            reference = recipe.surfaces[surface].aod
            variable = handle[reference.name]
            assert variable.dimensions[-1] in reference.select
            aods[surface] = np.round(
                physical(reference.name)[dimension_indexer(variable.dimensions, reference)], 6
            )
        land = np.isfinite(aods["land"]) & (qa == 3)
        ocean = np.isfinite(aods["ocean"]) & (qa >= 1) & (qa <= 3)
        assert not np.any(land & ocean)
        combined = np.round(physical("geophysical_data/Optical_Depth_Land_And_Ocean"), 6)
        np.testing.assert_array_equal(aods["land"][land], combined[land])
        np.testing.assert_array_equal(aods["ocean"][ocean], combined[ocean])
        expected = np.where(land, aods["land"], aods["ocean"])
        selected = (land | ocean) & np.isfinite(lat) & np.isfinite(lon)
        selected &= (expected >= -0.05) & (expected <= 5)
        np.testing.assert_array_equal(batch.aod, expected[selected])
        np.testing.assert_array_equal(batch.surface, np.where(land, LAND, OCEAN)[selected])
        np.testing.assert_allclose(batch.sigma, 0.05 + 0.15 * np.abs(batch.aod))
    with NetCDFSource(paths[0]) as source:
        dt_times = coverage_times(source, lat.shape)
    db_product = product.replace("AERDT", "AERDB")
    db_dir = granule_dir(config.paths, catalog, db_product, "2", DAY)
    db_paths = sorted(db_dir.glob(f"*.{hhmm}.*.nc"))
    assert db_paths, "same-granule Deep Blue file is needed for this verification"
    with Dataset(db_paths[0]) as handle:
        raw = np.asarray(np.ma.asarray(handle["Scan_Start_Time"][:]).filled(np.nan))
        db_times = tai93_to_unix_seconds(raw)
    edges = day_time_edges_seconds(DAY)
    dt_windows = np.searchsorted(edges, dt_times, side="right") - 1
    db_windows = np.searchsorted(edges, db_times[np.isfinite(db_times)], side="right") - 1
    assert db_windows.size and np.unique(dt_windows).size == 1
    np.testing.assert_array_equal(np.unique(dt_windows), np.unique(db_windows))
    np.testing.assert_array_equal(
        np.searchsorted(edges, batch.t, side="right") - 1, dt_windows[selected]
    )
