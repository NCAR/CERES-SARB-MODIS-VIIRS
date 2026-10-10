"""Configured family selection and sensor companions across all eight streams."""

from datetime import date
from pathlib import Path

import netCDF4
import numpy as np
import pytest

from sarb_aod.config.loader import load_config
from sarb_aod.config.schema import RunConfig
from sarb_aod.paths import granule_dir
from sarb_aod.pipeline import grid_day
from sarb_aod.readers import LAND, PixelBatch
from sarb_aod.readers.base import PixelCoordinates
from sarb_aod.windows import day_time_edges_seconds

DAY = date(2020, 9, 8)


@pytest.mark.parametrize("family", ["modis", "viirs", "mixed", "mixed_db21"])
@pytest.mark.parametrize("method", ["count", "gls"])
def test_selected_collections_and_sensor_companions(tmp_path, monkeypatch, family, method):
    repo = Path(__file__).resolve().parents[1]
    config = load_config(repo / f"config/runs/{family}_l2_202009_gls_rho05.yaml")
    raw = config.model_dump(mode="json")
    raw["paths"]["data_root"] = str(tmp_path)
    raw["weighting"]["method"] = method
    config = RunConfig.model_validate(raw)
    catalog = config.load_catalog()
    selections = {(s.product, s.collection) for s in config.grid.streams.values()}
    for product, collection in selections:
        directory = granule_dir(config.paths, catalog, product, collection, DAY)
        directory.mkdir(parents=True)
        suffix = ".hdf" if product in ["MOD04_L2", "MYD04_L2"] else ".nc"
        (directory / ("input" + suffix)).touch()
    seen = []

    def read(path, recipes, **kwargs):
        output = {}
        for recipe in recipes:
            seen.append((recipe.stream_id, recipe.collection))
            value = 0.1 if recipe.instrument == "MODIS" else 0.3
            times = np.full(2, day_time_edges_seconds(DAY)[0] + 60)
            empty = PixelCoordinates(np.array([]), np.array([]), np.array([]))
            output[recipe.stream_id] = PixelBatch(
                recipe.stream_id,
                times,
                np.zeros(2),
                np.zeros(2),
                np.full(2, value),
                np.full(2, 0.1),
                np.full(2, LAND),
                empty,
                {"n_accepted": 2},
            )
        return output

    monkeypatch.setattr("sarb_aod.pipeline.read_granule", read)
    result = grid_day(config, DAY)
    assert len(seen) == len(config.grid.streams)
    assert len(set(seen)) == len(seen)
    if family == "mixed_db21":
        assert ("noaa20_db", "2.1") in seen
        assert ("snpp_db", "2") in seen
    with netCDF4.Dataset(result.files[0]) as ds:
        ds.set_auto_mask(False)
        mean = ds["aod_weighted_mean"][:]
        valid = mean > -900
        assert valid.sum() == 1
        assert ds["aod_count"][:].sum() == 2 * len(seen)
        modis = ds["aod_weighted_mean_modis"][:]
        viirs = ds["aod_weighted_mean_viirs"][:]
        if family.startswith("mixed"):
            np.testing.assert_allclose(mean[valid], 0.2, rtol=1e-6)
            assert np.all((mean[valid] >= modis[valid]) & (mean[valid] <= viirs[valid]))
        else:
            absent = viirs if family == "modis" else modis
            present = modis if family == "modis" else viirs
            assert np.all(absent == -999)
            np.testing.assert_array_equal(mean, present)
