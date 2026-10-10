import pytest
from pydantic import ValidationError

from sarb_aod.config.schema import WeightingConfig


def test_default_weighting_and_count_option():
    cfg = WeightingConfig()
    assert cfg.method == "gls" and cfg.rho == 0.5 and cfg.birge and cfg.min_pixels == 1
    assert WeightingConfig(method="count", single_pixel_sigma="zero").single_pixel_sigma == "zero"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"rho": -0.1},
        {"rho": 1.1},
        {"rho": float("nan")},
        {"min_pixels": 0},
        {"min_pixels": True},
        {"method": "average"},
    ],
)
def test_invalid_weighting_config(kwargs):
    with pytest.raises(ValidationError):
        WeightingConfig(**kwargs)
