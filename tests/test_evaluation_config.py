import pytest
from pydantic import ValidationError

from sarb_aod.config.schema import EvaluationConfig, UncertaintyConfig


def test_expected_error_table_is_separate_from_pixel_sigma():
    cfg = EvaluationConfig()
    assert cfg.min_samples == 3 and cfg.round_precision == 4
    ocean = cfg.ee["dt_ocean"]
    assert (ocean.lower_intercept, ocean.upper_intercept) == (0.02, 0.04)
    assert UncertaintyConfig().envelopes["modis_dt"]["ocean"] == (0.03, 0.10)
    assert cfg.radius_km == 25 and cfg.min_aeronet_grid == 2


def test_invalid_evaluation_options():
    for kwargs in [{"min_samples": 0}, {"min_aeronet": 0}, {"radius_km": float("nan")}, {"ee": {}}]:
        with pytest.raises(ValidationError):
            EvaluationConfig(**kwargs)
