"""Numeric regression against CAM7's actual diagnostic four-stream script."""

import importlib.util
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from sarb_aod.config.loader import load_config
from sarb_aod.grid.combine import combine
from sarb_aod.grid.kernels import Accumulators, accumulate
from sarb_aod.grid.spec import load_grid, normalize_longitude
from sarb_aod.paths import granule_dir
from sarb_aod.pipeline import stream_recipes
from sarb_aod.readers import read_granule
from sarb_aod.windows import day_time_edges_seconds


@pytest.mark.real_data
def test_three_real_granules_match_cam7_including_last_scan():
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config/runs/modis_l2_200807_cam7_v2.yaml")
    paths = []
    for product, hhmm in [("MOD04_L2", "1700"), ("MYD04_L2", "1820"), ("MOD04_L2", "2355")]:
        directory = granule_dir(
            config.paths, config.load_catalog(), product, "6.1", date(2008, 7, 1)
        )
        files = sorted(directory.glob(f"*.{hhmm}.*.hdf"))
        if not files:
            pytest.skip(f"No staged {product} July1 {hhmm} granule in configured data root")
        paths.append((product, files[0]))
    spec = importlib.util.spec_from_file_location(
        "cam7_oracle", root / "tests/reference/cam7/prepare_modis_era5_grid.py"
    )
    oracle = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = oracle
    spec.loader.exec_module(oracle)
    assert oracle.bin_swath_to_grid is None
    grid = load_grid()
    reference_grid = oracle.Grid(grid.lat, grid.lon, grid.lat_edges, grid.lon_edges)
    recipes = stream_recipes(config)
    names = tuple(r.stream_id for r in recipes)
    ours = Accumulators.create(4)
    reference = oracle.Accumulator.empty(8, 192, 288, names)
    utc_edges = day_time_edges_seconds(date(2008, 7, 1))
    epoch93 = float(np.datetime64("1993-01-01").astype("datetime64[s]").astype(np.int64))
    tai_edges = utc_edges - epoch93
    for product, path in paths:
        batches = read_granule(
            path,
            [r for r in recipes if r.product == product],
            qc=config.qc,
            uncertainty=config.uncertainty,
            time=config.time,
            day=date(2008, 7, 1),
        )
        for stream, b in batches.items():
            accumulate(
                utc_edges,
                grid.lat_edges,
                grid.lon_edges,
                b.t,
                b.lat,
                normalize_longitude(b.lon),
                b.aod,
                b.sigma,
                names.index(stream),
                *ours.arrays(),
            )
        for b in oracle.read_modis_observation_streams(path):
            oracle.accumulate_pixels(
                reference,
                reference_grid,
                window_edges=tai_edges,
                lat=b.lat,
                lon=b.lon,
                time_seconds=b.time_seconds,
                aod=b.aod,
                qa=b.qa,
                qa_min=1,
                aod_min=0,
                aod_max=5,
                source=b.source,
            )
    expected_mean, expected_sigma, expected_count = reference.finalize()
    result = combine(ours, method="count", single_pixel_sigma="zero")
    np.testing.assert_array_equal(result.count, expected_count)
    for i, name in enumerate(names):
        np.testing.assert_array_equal(result.stream_count[:, i], reference.stream_counts[name])
    np.testing.assert_allclose(result.mean.astype("f4"), expected_mean, rtol=1e-6, atol=1e-7)
    np.testing.assert_allclose(result.sigma.astype("f4"), expected_sigma, rtol=1e-6, atol=1e-7)
    assert expected_count.sum() > 0
