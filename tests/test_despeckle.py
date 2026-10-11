"""Analytic geographic neighborhoods and deterministic spatial AOD filtering."""

import math

import numpy as np
import pytest
from pydantic import ValidationError
from test_combine import _one_cell

from sarb_aod.config.loader import load_config
from sarb_aod.config.schema import DespeckleConfig
from sarb_aod.despeckle import (
    DONORS_MISSING,
    EARTH_RADIUS_KM,
    FLAG_FILL,
    ISOLATED,
    REPLACED,
    SPIKE_MASKED,
    Neighborhoods,
    NeighborRow,
    SourceFilter,
    cell_areas,
    combine_filtered,
    filter_source,
    neighborhoods,
    weighted_median,
)
from sarb_aod.grid.combine import combine
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


def ring_geometry(n, areas=None):
    return Neighborhoods(
        (1, n),
        (NeighborRow(np.zeros(n - 1, dtype=int), np.arange(1, n)),),
        np.ones((1, n)) if areas is None else np.asarray(areas)[None, :],
    )


def ring_settings(**kwargs):
    return settings(min_valid_neighbors=3, **kwargs)


def test_weighted_median_lower_tie_and_unequal_areas():
    assert weighted_median(np.array([3.0, 1.0, 2.0, 4.0]), np.ones(4)) == 2
    assert weighted_median(np.array([1.0, 2.0, 3.0]), np.array([1.0, 1.0, 8.0])) == 3


def test_isolated_high_is_masked_but_low_and_original_missing_are_preserved():
    x = np.array([[2.0, 0.1, -999.0, np.nan, -0.05]])
    original = x.copy()
    result = filter_source(x, ring_geometry(5), ring_settings())
    np.testing.assert_array_equal(result.flag, [[ISOLATED, 0, FLAG_FILL, FLAG_FILL, 0]])
    np.testing.assert_array_equal(result.mean, [[-999.0, 0.1, -999.0, -999.0, -0.05]])
    assert result.neighbor_count[0, 0] == 2
    assert result.support_fraction[0, 0] == 0.5
    np.testing.assert_array_equal(x, original)


def test_spike_replacement_uses_area_mean_excludes_center_and_retains_counts():
    x = np.array([[2.0, 0.05, 0.1, 0.15, 0.2]])
    area = np.array([50.0, 1.0, 2.0, 3.0, 4.0])
    result = filter_source(x, ring_geometry(5, area), ring_settings())
    assert result.flag[0, 0] == REPLACED
    assert result.mean[0, 0] == pytest.approx(np.average(x[0, 1:], weights=area[1:]))
    assert result.neighbor_count[0, 0] == result.donor_count[0, 0] == 4
    assert result.donor_fraction[0, 0] == 1
    np.testing.assert_array_equal(result.mean[0, 1:], x[0, 1:])


@pytest.mark.parametrize("action", ["neighbor_mean", "mask"])
def test_spike_actions_zero_mad_and_original_donors_only(action):
    x = np.array([[5.0, 6.0, 0.1, 0.1, 0.1, 0.1]])
    cfg = ring_settings(spike={"action": action})
    result = filter_source(x, ring_geometry(6), cfg)
    assert np.all(result.flag[0, :2] == (REPLACED if action == "neighbor_mean" else SPIKE_MASKED))
    assert np.all(result.mean[0, :2] == (0.1 if action == "neighbor_mean" else -999))
    if action == "neighbor_mean":
        assert np.all(result.donor_count[0, :2] == 4)
        assert np.all(result.donor_fraction[0, :2] == 0.8)
    rotated = filter_source(np.roll(x, 2, axis=1), ring_geometry(6), cfg)
    np.testing.assert_array_equal(rotated.mean, np.roll(result.mean, 2, axis=1))


@pytest.mark.parametrize(
    "x,fraction", [([5.0, 6.0, 0.1, 0.1], 0.2), ([5.0, 6.0, 0.1, 0.1, 0.1, 0.1], 0.9)]
)
def test_replacements_without_enough_unflagged_donors_become_missing(x, fraction):
    result = filter_source(
        np.array([x]), ring_geometry(len(x)), ring_settings(min_valid_area_fraction=fraction)
    )
    np.testing.assert_array_equal(result.flag[0, :2], [DONORS_MISSING] * 2)
    assert np.all(result.mean[0, :2] == -999)


def test_uniform_high_gradient_and_disabled_are_unchanged():
    for x in (np.ones((1, 6)), np.array([[0.1, 0.2, 0.3, 0.4, 0.5, 0.6]])):
        result = filter_source(x, ring_geometry(6), ring_settings())
        np.testing.assert_array_equal(result.mean, x)
        assert not result.flag.any()
    x = np.array([[5.0, 0.1, 0.1, 0.1]])
    result = filter_source(x, ring_geometry(4), DespeckleConfig())
    np.testing.assert_array_equal(result.mean, x)
    assert not result.flag.any()


def test_spike_strict_excess_threshold_and_disabled_spike():
    x = np.array([[0.5, 0.25, 0.25, 0.25]])
    exact = filter_source(x, ring_geometry(4), ring_settings(spike={"min_excess": 0.25}))
    assert exact.flag[0, 0] == 0
    result = filter_source(x * 4, ring_geometry(4), ring_settings(spike={"enabled": False}))
    np.testing.assert_array_equal(result.mean, x * 4)


def test_latitude_bands_preserve_outside_targets_and_use_outside_donors():
    grid = load_grid()
    cfg = DespeckleConfig(enabled=True, latitude_bands=((grid.lat[170], grid.lat[171]),))
    geo = neighborhoods(grid, cfg)
    values = np.full(grid.shape, 0.1)
    values[170, 0] = 5
    values[169, 140] = 5  # Outside: never altered despite local spike.
    result = filter_source(values, geo, cfg)
    assert result.flag[170, 0] == REPLACED
    assert result.mean[170, 0] == pytest.approx(0.1)
    assert result.flag[169, 140] == 0 and result.mean[169, 140] == 5
    empty = filter_source(np.full(grid.shape, -999.0), geo, cfg)
    assert np.all(empty.flag == FLAG_FILL) and np.all(empty.mean == -999)


def planted_filter(value, action):
    x = np.array([[[value]]])
    return SourceFilter(
        x,
        np.full(x.shape, action, dtype=np.int8),
        np.full(x.shape, -999.0),
        np.full(x.shape, -1),
        np.full(x.shape, -1),
        np.full(x.shape, -999.0),
    )


@pytest.mark.parametrize("method", ["count", "gls"])
def test_frozen_source_combination_and_changed_sigma(method):
    acc = _one_cell(([2.0, 2.0], [0.2, 0.2]), ([0.1], [0.1]))
    base = combine(acc, method=method, rho=0.5)
    before = base.mean.copy()
    first = planted_filter(0.2, REPLACED)
    second = planted_filter(0.1, 0)
    filtered = combine_filtered(base, [first, second])
    weights = base.stream_weight.ravel()
    expected_weights = (
        [2, 1] if method == "count" else [1 / (0.5 * 0.2**2 + 0.5 / (2 / 0.2**2)), 100]
    )
    np.testing.assert_allclose(weights, expected_weights)
    assert filtered.mean.item() == pytest.approx(np.average([0.2, 0.1], weights=expected_weights))
    assert filtered.sigma.item() == -999 and filtered.flags.item() == 2
    masked = combine_filtered(base, [planted_filter(-999, ISOLATED), second])
    assert masked.mean.item() == pytest.approx(0.1) and masked.flags.item() == 1
    assert (
        combine_filtered(base, [planted_filter(-999, ISOLATED), second], min_pixels=2).mean.item()
        == -999
    )
    np.testing.assert_array_equal(base.mean, before)


def test_combination_noop_missing_sources_and_no_resurrection():
    base = combine(_one_cell(([0.1], [0.1]), ([0.2], [np.nan])))
    result = combine_filtered(base, [planted_filter(0.1, 0), planted_filter(-999, FLAG_FILL)])
    np.testing.assert_array_equal(result.mean, base.mean)
    np.testing.assert_array_equal(result.sigma, base.sigma)
    assert result.flags.item() == 0
    missing = combine(_one_cell(([0.1], [0.1])), min_pixels=2)
    assert combine_filtered(missing, [planted_filter(0.3, REPLACED)]).mean.item() == -999
    empty = combine(_one_cell(([0.1], [0.1])), stream_indices=[])
    assert combine_filtered(empty, []).mean.item() == -999


def test_broad_plume_interior_survives_and_thin_plume_risk_is_visible():
    grid = load_grid()
    cfg = settings()
    geo = neighborhoods(grid, cfg)
    broad = np.full(grid.shape, 0.1)
    broad[165:175, 20:50] = 2.0
    result = filter_source(broad, geo, cfg)
    assert result.flag[170, 35] == 0 and result.mean[170, 35] == 2
    thin = np.full(grid.shape, 0.1)
    thin[170, 20:50] = 2.0
    narrow = filter_source(thin, geo, cfg)
    # The robust-spike rule cannot distinguish a thin real plume from artifacts.
    # Keep this limitation explicit; baseline data must remain untouched.
    assert np.any(narrow.flag[170, 20:50] == REPLACED)
    assert np.all(thin[170, 20:50] == 2)


@pytest.mark.parametrize("layout", ["C", "F", "strided"])
def test_filter_handles_memory_layout_without_losing_output_writes(layout):
    x = np.full((2, 1, 8), 0.1)
    x[:, 0, 2] = 5
    if layout == "strided":
        x = x[:, :, ::2]
    else:
        x = np.array(x, order=layout)
    original = x.copy()
    filtered = filter_source(x, ring_geometry(x.shape[-1]), ring_settings())
    spikes = x == 5
    assert np.all(filtered.flag[spikes] == REPLACED)
    np.testing.assert_allclose(filtered.mean[spikes], 0.1)
    np.testing.assert_array_equal(x, original)
