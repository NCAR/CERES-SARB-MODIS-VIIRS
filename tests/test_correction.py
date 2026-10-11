import numpy as np
import pytest
from pydantic import ValidationError

from sarb_aod.config.loader import load_config
from sarb_aod.config.schema import AODCorrectionConfig, RunConfig
from sarb_aod.correction import correct_aod
from sarb_aod.pipeline import processing_sha256


def test_identity_preserves_values_and_existing_processing_hash():
    config = load_config("config/runs/viirs_noaa20_c2_l2_202009_f09_3hr_gls_rho05.yaml")
    assert config.aod_correction == {}
    values = np.array([-0.05, 0.0, 1.0])
    assert correct_aod(values, AODCorrectionConfig()) is values
    # Recorded by the pre-feature AMI run, not calculated from the new implementation.
    assert (
        processing_sha256(config)
        == "1dd872ae13a6698d73f14da8036f04f05b2c654428751af8e430d3388c97c3cf"
    )
    identity = config.model_copy(update={"aod_correction": {"noaa20_dt": AODCorrectionConfig()}})
    assert processing_sha256(identity) == processing_sha256(config)
    hashes = {processing_sha256(config)}
    for stream in ("noaa20_dt", "noaa20_db"):
        changed = config.model_copy(
            update={"aod_correction": {stream: AODCorrectionConfig(offset=0.1)}}
        )
        hashes.add(processing_sha256(changed))
    assert len(hashes) == 3


def test_yaml_source_settings_and_coefficient_defaults(tmp_path):
    import yaml

    config = load_config("config/runs/viirs_noaa20_c2_l2_202009_f09_3hr_gls_rho05.yaml")
    raw = config.model_dump(mode="json")
    raw["profile"] = None  # Paths are already resolved; this YAML is self-contained.
    raw["aod_correction"] = {"noaa20_dt": {"offset": 0.1}, "noaa20_db": {"scale": 0.8}}
    path = tmp_path / "run.yaml"
    path.write_text(yaml.safe_dump(raw))
    actual = load_config(path).aod_correction
    assert actual["noaa20_dt"] == AODCorrectionConfig(scale=1, offset=0.1)
    assert actual["noaa20_db"] == AODCorrectionConfig(scale=0.8, offset=0)


@pytest.mark.parametrize("stream", ["noaa20_typo", "terra_dt"])
def test_correction_requires_a_selected_stream(stream):
    raw = load_config("config/runs/viirs_noaa20_c2_l2_202009_f09_3hr_gls_rho05.yaml").model_dump()
    raw["aod_correction"] = {stream: {"scale": 2}}
    with pytest.raises(ValidationError, match="selected grid.streams"):
        RunConfig.model_validate(raw)


@pytest.mark.parametrize("key", ["scale", "offset"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_coefficients_must_be_finite(key, value):
    with pytest.raises(ValidationError):
        AODCorrectionConfig.model_validate({key: value})


def test_correction_overflow_fails_instead_of_silently_dropping_pixels():
    with pytest.raises(ValueError, match="nonfinite AOD"):
        correct_aod(np.array([5.0]), AODCorrectionConfig(scale=1e308))
