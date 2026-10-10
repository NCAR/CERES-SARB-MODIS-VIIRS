import numpy as np
import pytest
from pydantic import ValidationError

from sarb_aod.config.schema import UncertaintyConfig
from sarb_aod.uncertainty import pixel_uncertainty


@pytest.mark.parametrize(
    "family,surface,a,b",
    [
        ("modis_dt", "land", 0.05, 0.15),
        ("modis_dt", "ocean", 0.03, 0.10),
        ("viirs_dt", "land", 0.05, 0.15),
        ("viirs_dt", "ocean", 0.05, 0.15),
    ],
)
def test_envelopes(family, surface, a, b):
    x = np.array([-0.05, 0, 0.2, 5, np.nan, np.inf])
    actual = pixel_uncertainty(x, family, surface)
    np.testing.assert_allclose(actual[:4], a + b * np.abs(x[:4]))
    assert np.isnan(actual[4:]).all()


def test_retrieval_sigma_magnitude_floor_and_missing():
    x = np.array([1.0, 1, 1, 1, 1, np.nan])
    sigma = np.array([-0.0005, -0.2, 0, np.nan, np.inf, 0.2])
    actual = pixel_uncertainty(x, "viirs_db", "ocean", retrieval_sigma=sigma)
    np.testing.assert_allclose(actual, [0.01, 0.2, 0.01, np.nan, np.nan, np.nan])
    assert np.isnan(pixel_uncertainty(x, "modis_db", "land")).all()


def test_retrieval_sigma_overrides_dt_envelope_including_missing():
    np.testing.assert_allclose(
        pixel_uncertainty([1, 2], "modis_dt", "land", retrieval_sigma=[0.3, np.nan]), [0.3, np.nan]
    )


def test_masked_sigma_remains_missing():
    sigma = np.ma.array([0.0, 0.2], mask=[True, False])
    np.testing.assert_allclose(
        pixel_uncertainty([1, 1], "modis_db", "land", retrieval_sigma=sigma), [np.nan, 0.2]
    )


def test_configured_floor_and_envelope():
    cfg = UncertaintyConfig(sigma_floor=0.2, envelopes={"modis_dt": {"land": (0.1, 0.3)}})
    np.testing.assert_allclose(
        pixel_uncertainty([0, 1], "modis_dt", "land", config=cfg), [0.2, 0.4]
    )
    with pytest.raises(ValueError, match="No uncertainty envelope"):
        pixel_uncertainty([0], "modis_dt", "ocean", config=cfg)


@pytest.mark.parametrize(
    "config",
    [
        {"sigma_floor": 0},
        {"sigma_floor": float("nan")},
        {"envelopes": {"modis_dt": {"land": (-0.1, 0.1)}}},
        {"envelopes": {"viirs_dt": {"ocean": (0.1, float("inf"))}}},
        {"envelopes": {"modis_db": {"land": (0.1, 0.1)}}},
    ],
)
def test_invalid_uncertainty_config(config):
    with pytest.raises(ValidationError):
        UncertaintyConfig(**config)
