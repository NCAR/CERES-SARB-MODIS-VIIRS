"""QA thresholds, packed endpoints, masks, and configurable preset behavior."""

import numpy as np
import pytest
from pydantic import ValidationError

from sarb_aod.config.schema import Catalog, QCConfig, load_catalog
from sarb_aod.qc import decode_packed, qa_mask, quality_masks, resolve_rule
from sarb_aod.streams import resolve_stream


@pytest.mark.parametrize(
    "family,surface,expected",
    [
        ("modis_dt", "land", 3),
        ("modis_dt", "ocean", 1),
        ("modis_db", "land", 2),
        ("viirs_dt", "land", 3),
        ("viirs_dt", "ocean", 1),
        ("viirs_db", "land", 2),
        ("viirs_db", "ocean", 2),
    ],
)
@pytest.mark.parametrize("preset", ["retrieval_team", "cam7_v2"])
def test_threshold_tables(family, surface, expected, preset):
    config = QCConfig(preset=preset)
    rule = resolve_rule(family, surface, config)
    assert rule.qa_min == (expected if preset == "retrieval_team" else 1)
    assert rule.aod_min == (-0.05 if preset == "retrieval_team" else 0)
    assert rule.aod_max == 5
    flags = np.arange(-1, 6)
    np.testing.assert_array_equal(
        qa_mask(flags, family, surface, config=config),
        (flags >= rule.qa_min) & (flags <= 3),
    )


def test_qa_override_and_explicit_aod_bounds():
    config = QCConfig.model_validate(
        {"overrides": {"modis_dt": {"ocean": {"qa_min": 3}}}, "aod_min": -0.02, "aod_max": 4}
    )
    assert resolve_rule("modis_dt", "ocean", config).qa_min == 3
    assert resolve_rule("viirs_dt", "ocean", config).qa_min == 1
    masks = quality_masks([-0.02, 0, 4, 4.1], [3, 1, 3, 3], "modis_dt", "ocean", config=config)
    np.testing.assert_array_equal(masks.accepted, [True, False, True, False])
    np.testing.assert_array_equal(masks.rejected_high, [False, False, False, True])


@pytest.mark.parametrize(
    "data",
    [
        {"overrides": {"modis": {"land": {"qa_min": 3}}}},
        {"overrides": {"modis_dt": {"water": {"qa_min": 3}}}},
        {"overrides": {"modis_dt": {"land": {"qa_min": 4}}}},
        {"overrides": {"modis_dt": {"land": {"qa_min": -1}}}},
        {"overrides": {"modis_dt": {"land": {"qa_min": 1.5}}}},
        {"aod_min": 1, "aod_max": 0},
        {"aod_min": float("nan")},
        {"aod_max": float("inf")},
        {"preset": "cam7_v2", "aod_min": -0.05},
    ],
)
def test_invalid_qc_config(data):
    with pytest.raises(ValidationError):
        QCConfig.model_validate(data)


def test_masks_reject_fill_noninteger_nonfinite_and_out_of_range_qa():
    flags = np.ma.array([3, -9999, 0, 4, 1.5, np.nan, np.inf, 3], mask=[0] * 7 + [1])
    np.testing.assert_array_equal(
        qa_mask(flags, "modis_dt", "land", fill_value=-9999),
        [True] + [False] * 7,
    )
    assert not qa_mask([3], "modis_dt", "ocean", fill_value=3)[0]
    for fill in (0, -999):
        np.testing.assert_array_equal(
            qa_mask([fill, 1, 2, 3], "viirs_db", "ocean", fill_value=fill),
            [False, False, True, True],
        )


def test_packed_clamp_endpoints_are_inclusive_after_rounding():
    packed = np.array([-9999, -51, -50, 0, 5000, 5001], dtype=np.int16)
    scale = float(np.float32(0.001))
    physical = decode_packed(packed, scale_factor=scale, fill_value=-9999, valid_range=[-100, 5000])
    np.testing.assert_allclose(physical, [np.nan, -0.051, -0.05, 0, 5, np.nan], equal_nan=True)
    masks = quality_masks(physical, np.full(packed.size, 3), "modis_dt", "land")
    np.testing.assert_array_equal(masks.accepted, [False, False, True, True, True, False])
    assert not masks.rejected_high.any()
    legacy = QCConfig(preset="cam7_v2")
    physical = decode_packed(packed, scale_factor=scale, valid_range=[-100, 5000], config=legacy)
    assert physical[4] > 5
    np.testing.assert_array_equal(
        quality_masks(
            physical, np.full(packed.size, 3), "modis_dt", "land", config=legacy
        ).accepted,
        [False, False, False, True, False, False],
    )


def test_packed_valid_range_and_fill_checked_before_scale_and_offset():
    actual = decode_packed(
        np.ma.array([0, 1, 2, 3, 4], mask=[0, 0, 0, 1, 0]),
        scale_factor=0.5,
        add_offset=10,
        fill_value=1,
        valid_range=[0, 3],
    )
    np.testing.assert_allclose(actual, [10, np.nan, 11, np.nan, np.nan], equal_nan=True)
    np.testing.assert_allclose(
        decode_packed([-9999, 1, np.inf], valid_range=[0, 10]), [np.nan, 1, np.nan], equal_nan=True
    )
    raw = np.array([489110405.9411111])
    np.testing.assert_array_equal(decode_packed(raw, round_values=False), raw)


@pytest.mark.parametrize("limits", [[2, 1], [0], [0, 1, 2], [0, np.inf]])
def test_invalid_packed_range(limits):
    with pytest.raises(ValueError, match="valid_range"):
        decode_packed([0], valid_range=limits)


@pytest.mark.parametrize(
    "family,surface",
    [
        ("modis_dt", "land"),
        ("modis_dt", "ocean"),
        ("modis_db", "land"),
        ("viirs_dt", "land"),
        ("viirs_dt", "ocean"),
        ("viirs_db", "land"),
        ("viirs_db", "ocean"),
    ],
)
def test_cam7_range_drops_negatives_across_families(family, surface):
    masks = quality_masks(
        [-0.01, 0, 5, 5.1, 6], [3, 3, 3, 3, 0], family, surface, config=QCConfig(preset="cam7_v2")
    )
    np.testing.assert_array_equal(masks.accepted, [False, True, True, False, False])
    np.testing.assert_array_equal(masks.rejected_high, [False, False, False, True, False])


def test_cam7_swaps_only_modis_dt_ocean_variables():
    recipe = resolve_stream("MOD04_L2", "6.1", "dt", preset="cam7_v2")
    assert recipe.surfaces["ocean"].aod.name == "Effective_Optical_Depth_Best_Ocean"
    raw = load_catalog().model_dump()
    modis = raw["products"]["MOD04_L2"]["collections"]["6.1"]
    viirs = raw["products"]["AERDT_L2_VIIRS_SNPP"]["collections"]["2.1"]
    viirs["geolocation"] = {"lat": "lat", "lon": "lon", "time": None, "time_kind": "coverage_attrs"}
    viirs["algorithms"] = {
        "dt": {**modis["algorithms"]["dt"], "family": "viirs_dt", "alternates": {}}
    }
    recipe = resolve_stream(
        "AERDT_L2_VIIRS_SNPP", "2.1", "dt", catalog=Catalog.model_validate(raw), preset="cam7_v2"
    )
    assert recipe.surfaces["ocean"].aod.name == "Effective_Optical_Depth_Average_Ocean"


def test_undefined_surface_and_shape_mismatch_are_errors():
    assert resolve_rule("modis_db", "ocean").qa_min == 2
    with pytest.raises(ValueError, match="identical shapes"):
        quality_masks([0.1, 0.2], [3], "modis_dt", "land")
