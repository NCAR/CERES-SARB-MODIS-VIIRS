"""VIIRS Deep Blue catalog activation and v2/v2.1 surface consistency."""

from datetime import date
from pathlib import Path

import numpy as np
import pytest
from netCDF4 import Dataset

from sarb_aod.config.loader import load_config
from sarb_aod.config.schema import QCConfig
from sarb_aod.paths import granule_dir
from sarb_aod.readers import LAND, OCEAN, read_granule
from sarb_aod.streams import resolve_stream
from sarb_aod.timeconv import tai93_to_unix_seconds
from sarb_aod.windows import day_time_edges_seconds

PRODUCTS = ("AERDB_L2_VIIRS_SNPP", "AERDB_L2_VIIRS_NOAA20")
DAY = date(2020, 9, 8)


def test_both_versions_and_platforms_share_one_variable_map():
    recipes = [
        resolve_stream(product, version, "db") for product in PRODUCTS for version in ("2", "2.1")
    ]
    for recipe in recipes:
        assert recipe.family == "viirs_db"
        assert recipe.geolocation == recipes[0].geolocation
        assert recipe.surfaces == recipes[0].surfaces
        assert set(recipe.surfaces) == {"land", "ocean"}
    assert [recipe.stream_id for recipe in recipes] == [
        "snpp_db",
        "snpp_db",
        "noaa20_db",
        "noaa20_db",
    ]


@pytest.mark.parametrize("product", PRODUCTS)
@pytest.mark.parametrize("version,qa_fill", [("2", 0), ("2.1", -999)])
def test_db_fill_versions_overlap_and_negative_expected_uncertainty(
    tmp_path, product, version, qa_fill
):
    recipe = resolve_stream(product, version, "db")
    path = tmp_path / "viirs_db.nc"
    with Dataset(path, "w") as handle:
        handle.createDimension("Idx_Atrack", 2)
        handle.createDimension("Idx_Xtrack", 3)
        dimensions = ("Idx_Atrack", "Idx_Xtrack")
        handle.createVariable("Latitude", "f8", dimensions)[:] = np.arange(6).reshape(2, 3)
        handle.createVariable("Longitude", "f8", dimensions)[:] = 0
        tai93 = (
            float((np.datetime64(DAY) - np.datetime64("1993-01-01")) / np.timedelta64(1, "s")) + 10
        )
        handle.createVariable("Scan_Start_Time", "f8", dimensions)[:] = tai93 + 60
        for surface, aod, qa in [
            ("land", [[0.1, 0.2, -999], [0.4, 6, 0.8]], [[1, qa_fill, qa_fill], [3, 3, 2]]),
            ("ocean", [[0.3, 0.5, 0.6], [0.7, 0.9, 1]], [[1, 3, 3], [3, 3, 1]]),
        ]:
            variables = recipe.surfaces[surface]
            variable = handle.createVariable(variables.aod.name, "f8", dimensions, fill_value=-999)
            variable.valid_range = [0, 5]
            variable[:] = aod
            handle.createVariable(variables.qa.name, "i4", dimensions, fill_value=qa_fill)[:] = qa
            sigma = handle.createVariable(variables.sigma.name, "f8", dimensions, fill_value=-999)
            sigma.valid_range = [-0.5, 5]
            sigma[:] = [[-0.0005, 0.1, 0.2], [-999, 0.3, 0.4]]
    for preset in ("retrieval_team", "cam7_v2"):
        batch = read_granule(path, [recipe], qc=QCConfig(preset=preset), day=DAY)[recipe.stream_id]
        expected_aod = [0.5, 0.6, 0.4, 0.9, 0.8]
        expected_surface = [OCEAN, OCEAN, LAND, OCEAN, LAND]
        expected_sigma = [0.1, 0.2, np.nan, 0.3, 0.4]
        if preset == "cam7_v2":
            expected_aod.insert(0, 0.1)
            expected_surface.insert(0, LAND)
            expected_sigma.insert(0, 0.01)
        np.testing.assert_array_equal(batch.aod, expected_aod)
        np.testing.assert_array_equal(batch.surface, expected_surface)
        np.testing.assert_allclose(batch.sigma, expected_sigma, equal_nan=True)
        assert batch.counts["n_sigma_missing"] == 1
        assert batch.counts["n_accepted"] == len(expected_aod)
        assert len(batch.rejected_high) == 0  # Packed/native valid_range excludes 6.


@pytest.mark.real_data
@pytest.mark.parametrize(
    "product,version", [(PRODUCTS[0], "2"), (PRODUCTS[1], "2"), (PRODUCTS[1], "2.1")]
)
@pytest.mark.parametrize("preset", ["retrieval_team", "cam7_v2"])
def test_real_viirs_db_qc_count_identity(product, version, preset):
    repo = Path(__file__).resolve().parents[1]
    config = load_config(repo / "config/runs/modis_viirs_l2_202009.yaml")
    catalog = config.load_catalog()
    directory = granule_dir(config.paths, catalog, product, version, DAY)
    paths = sorted(directory.glob("*.nc"))
    if not paths:
        pytest.skip("no staged VIIRS DB granules under the configured profile's data root")
    # These daylight granules cover western North America; fall back when a
    # partially staged installation has only a different granule available.
    hhmm = "2024" if product == PRODUCTS[0] else "1936"
    path = next((candidate for candidate in paths if f".{hhmm}." in candidate.name), paths[0])
    recipe = resolve_stream(product, version, "db", catalog=catalog, preset=preset)
    batch = read_granule(path, [recipe], qc=QCConfig(preset=preset), day=DAY)[recipe.stream_id]
    with Dataset(path) as handle:

        def physical(name):
            return np.asarray(np.ma.asarray(handle[name][:], dtype=np.float64).filled(np.nan))

        lat, lon = physical("Latitude"), physical("Longitude")
        times = tai93_to_unix_seconds(physical("Scan_Start_Time"))
        edges = day_time_edges_seconds(DAY)
        located = (
            np.isfinite(lat)
            & np.isfinite(lon)
            & np.isfinite(times)
            & (times >= edges[0])
            & (times < edges[-1])
        )
        masks, aods = {}, {}
        for surface in ("land", "ocean"):
            variables = recipe.surfaces[surface]
            aod = physical(variables.aod.name)
            qa = np.asarray(handle[variables.qa.name][:].filled(0))
            minimum = 2 if preset == "retrieval_team" else 1
            masks[surface] = (
                np.isfinite(aod) & (aod >= 0) & (aod <= 5) & (qa >= minimum) & (qa <= 3) & located
            )
            aods[surface] = aod
        land, ocean = masks["land"], masks["ocean"]
        assert len(batch) == int(land.sum() + ocean.sum() - (land & ocean).sum())
        expected = np.where(land, aods["land"], aods["ocean"])
        np.testing.assert_array_equal(batch.aod, expected[land | ocean])
        np.testing.assert_array_equal(batch.surface, np.where(land, LAND, OCEAN)[land | ocean])
        assert batch.counts["n_land"] == int(land.sum())
        assert batch.counts["n_ocean"] == int((ocean & ~land).sum())
