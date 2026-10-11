"""Analytic geographic neighborhoods and deterministic spatial AOD filtering."""

import math

import numpy as np
import pytest
from pydantic import ValidationError

from sarb_aod.config.loader import load_config
from sarb_aod.config.schema import DespeckleConfig
from sarb_aod.despeckle import EARTH_RADIUS_KM, cell_areas, neighborhoods
from sarb_aod.grid.spec import load_grid
from sarb_aod.pipeline import processing_sha256


def settings(**kwargs):
    return DespeckleConfig(enabled=True, latitude_bands=((65, 90),), **kwargs)


@pytest.mark.parametrize(
    "values",
    [
        {"enabled": True},
        {"latitude_bands": [(91, 95)]},
        {"latitude_bands": [(20, 10)]},
        {"latitude_bands": [(0, 0)]},
        {"latitude_bands": [(float("nan"), 90)]},
        {"latitude_bands": [(-90, float("inf"))]},
        {"radius_km": 0},
        {"radius_km": 21000},
        {"radius_km": float("nan")},
        {"aod_min": -1},
        {"min_valid_area_fraction": 0},
        {"min_valid_area_fraction": 1.01},
        {"min_valid_neighbors": True},
        {"min_valid_neighbors": 2},
        {"spike": {"min_excess": 0}},
        {"spike": {"mad_multiplier": float("inf")}},
        {"spike": {"action": "smooth"}},
    ],
)
def test_invalid_settings(values):
    with pytest.raises(ValidationError):
        DespeckleConfig.model_validate(values)


def test_bands_are_a_canonical_union_and_inclusive():
    cfg = DespeckleConfig(enabled=True, latitude_bands=((70, 90), (-90, -65), (65, 75), (-70, -60)))
    assert cfg.latitude_bands == ((-90, -60), (65, 90))
    grid = load_grid()
    cfg = DespeckleConfig(enabled=True, latitude_bands=((grid.lat[100], grid.lat[101]),))
    geo = neighborhoods(grid, cfg)
    assert [i for i, row in enumerate(geo.rows) if row is not None] == [100, 101]
    assert len(geo.indices(99, 0)) == 0
    assert np.any(geo.indices(100, 0) // 288 == 99)  # Support beyond the target band.


def test_spherical_areas_include_both_polar_caps():
    grid = load_grid()
    area = cell_areas(grid)
    assert area.shape == grid.shape and np.all(area > 0)
    assert area.sum() == pytest.approx(4 * math.pi * EARTH_RADIUS_KM**2)
    assert area[0].sum() == pytest.approx(
        2 * math.pi * EARTH_RADIUS_KM**2 * (1 + np.sin(np.deg2rad(grid.lat_edges[1])))
    )
    np.testing.assert_allclose(area[0], area[-1])


def test_neighborhoods_wrap_longitude_and_include_polar_wedges():
    grid = load_grid()
    geo = neighborhoods(grid, DespeckleConfig(enabled=True, latitude_bands=((-90, -65), (65, 90))))
    for row in (0, 180, 191):
        first = geo.indices(row, 0)
        assert row * 288 not in first and len(first) == len(set(first))
        assert row * 288 + 287 in first
        shifted = geo.indices(row, 15)
        expected = (first // 288) * 288 + (first % 288 + 15) % 288
        np.testing.assert_array_equal(shifted, expected)
    assert np.count_nonzero(geo.indices(0, 0) // 288 == 0) == 287
    assert np.count_nonzero(geo.indices(191, 0) // 288 == 191) == 287
    assert not geo.areas.flags.writeable


def test_radius_membership_matches_independent_spherical_dot_products():
    grid = load_grid()
    geo = neighborhoods(grid, settings())
    row, col = 169, 128
    lat, lon = np.deg2rad(grid.lat), np.deg2rad(grid.lon)
    cos_distance = np.sin(lat[:, None]) * np.sin(lat[row]) + np.cos(lat[:, None]) * np.cos(
        lat[row]
    ) * np.cos(lon[None, :] - lon[col])
    distance = EARTH_RADIUS_KM * np.arccos(np.clip(cos_distance, -1, 1))
    selected = distance <= 150
    selected[row, col] = False
    assert set(geo.indices(row, col)) == set(np.flatnonzero(selected))


def test_geometry_cache_ignores_thresholds_and_includes_radius():
    grid = load_grid()
    original = neighborhoods(grid, settings())
    assert original is neighborhoods(grid, settings(aod_min=1))
    assert original is not neighborhoods(grid, settings(radius_km=100))


def test_disabled_retains_existing_hash_and_enabled_definition_changes():
    base = load_config("config/runs/viirs_noaa20_c2_l2_202009_f09_3hr_gls_rho05.yaml")
    assert (
        processing_sha256(base)
        == "1dd872ae13a6698d73f14da8036f04f05b2c654428751af8e430d3388c97c3cf"
    )
    disabled = base.model_copy(update={"despeckle": DespeckleConfig(latitude_bands=((65, 90),))})
    assert processing_sha256(disabled) == processing_sha256(base)
    enabled = base.model_copy(update={"despeckle": settings()})
    assert processing_sha256(enabled) != processing_sha256(base)
    changed = enabled.model_copy(update={"despeckle": settings(radius_km=100)})
    assert processing_sha256(enabled) != processing_sha256(changed)
