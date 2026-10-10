"""Spectral methods on power laws, curved spectra, and recorded edge cases."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sarb_aod.aeronet.parse import AERONET_PREAMBLE, parse_aeronet, read_aeronet
from sarb_aod.aeronet.spectral import (
    angstrom_500,
    convert_550nm,
    quadratic_loglog,
    window_uncertainty,
)
from sarb_aod.config.schema import AeronetConfig

FIXTURES = Path(__file__).parent / "data" / "aeronet"


def test_spectral_config_defaults_and_validation() -> None:
    config = AeronetConfig()
    assert config.spectral == "quadratic_loglog"
    assert config.include_extrapolated
    assert config.instrument_sigma == 0.01
    assert (
        AeronetConfig(spectral="angstrom_500", include_extrapolated=False).spectral
        == "angstrom_500"
    )
    for sigma in (0, -0.01, np.nan, np.inf):
        with pytest.raises(ValueError):
            AeronetConfig(instrument_sigma=sigma)


def test_avignon_without_500nm_and_ocean_color_sites_interpolate() -> None:
    fixtures = (
        "AERONET_AOD20_ALLPOINTS_20080701_EDGECASES.csv",
        "AERONET_AOD20_ALLPOINTS_20200908_EDGECASES.csv",
    )
    frame = pd.concat([read_aeronet(FIXTURES / name) for name in fixtures], ignore_index=True)
    sites = frame["AERONET_Site"].isin(["Avignon", "Helsinki_Lighthouse", "Galata_Platform"])
    subset = frame.loc[sites]
    assert subset["AOD_500nm"].isna().all()
    converted = convert_550nm(subset)
    assert converted["aod_550nm"].gt(0).all()
    assert converted["spectral_flag"].eq("interpolated").all()
    assert convert_550nm(subset, method="angstrom_500")["aod_550nm"].isna().all()
    assert "aod_550nm" not in subset


def test_smoke_is_extrapolated_and_filter_preserves_flag() -> None:
    frame = read_aeronet(FIXTURES / "AERONET_AOD20_ALLPOINTS_20200908_EDGECASES.csv")
    smoke = frame.loc[frame["AERONET_Site"] == "NASA_Ames"]
    assert len(smoke) == 3
    converted = convert_550nm(smoke)
    assert converted["aod_550nm"].gt(smoke["AOD_675nm"]).all()
    assert converted["spectral_flag"].eq("extrapolated").all()
    # The two shortest long bands are 675 and 870; 1020 and 1640 are not fit.
    exponent = -np.log(smoke["AOD_870nm"] / smoke["AOD_675nm"]) / np.log(870 / 675)
    expected = smoke["AOD_675nm"] * (550 / 675) ** -exponent
    np.testing.assert_allclose(converted["aod_550nm"], expected, rtol=1e-13)
    excluded = convert_550nm(smoke, include_extrapolated=False)
    assert excluded["aod_550nm"].isna().all()
    assert excluded["spectral_flag"].eq("extrapolated").all()


@pytest.mark.parametrize("alpha", [-0.3, 0.0, 0.8, 2.5])
def test_methods_agree_on_power_laws(alpha: float) -> None:
    wavelength = np.array([340, 440, 500, 675, 870, 1020])
    tau = 0.35 * (wavelength / 550) ** -alpha
    quadratic = quadratic_loglog(wavelength, tau)
    angstrom = angstrom_500(tau[2], alpha)
    assert quadratic.aod.shape == ()
    assert quadratic.aod == pytest.approx(0.35, rel=1e-13)
    assert angstrom.aod == pytest.approx(quadratic.aod, rel=1e-13)
    assert quadratic.flag == "interpolated"


def test_quadratic_curvature_uses_all_valid_bands_and_ignores_outside_range() -> None:
    wavelength = np.array([1640, 1020, 870, 675, 500, 440, 340, 305])
    log_lambda = np.log(wavelength / 550)
    aod = np.exp(-1.0 - 0.8 * log_lambda + 0.3 * log_lambda**2)
    aod[[0, -1]] = 1e6  # outside 340--1020 nm: cannot alter the fit
    result = quadratic_loglog(wavelength, aod)
    assert result.aod == pytest.approx(np.exp(-1), rel=1e-13)


def test_missing_wavelength_patterns_and_leading_dimensions() -> None:
    wavelength = [340, 440, 500, 675, 870]
    rows = np.array(
        [
            [[0.3, 0.2, 0.18, 0.12, 0.08], [np.nan, np.nan, np.nan, 0.12, 0.08]],
            [[0.3, 0.2, 0.18, 0, -0.1], [np.nan, np.nan, 0.18, 0.12, np.nan]],
        ]
    )
    result = quadratic_loglog(wavelength, rows)
    assert result.aod.shape == (2, 2)
    np.testing.assert_array_equal(
        result.flag, [["interpolated", "extrapolated"], ["missing", "missing"]]
    )
    assert np.isnan(result.aod[1]).all()
    # A measurement at 550 plus one longer band follows the explicit fallback.
    exact = quadratic_loglog([550, 870], [0.2, 0.1])
    assert exact.flag == "extrapolated"
    assert exact.aod == pytest.approx(0.2)


def test_angstrom_rejects_missing_and_nonpositive_inputs() -> None:
    result = angstrom_500([0.2, 0, -0.01, np.nan, 0.2, 0.2], [1, 1, 1, 1, np.nan, -999])
    assert np.isfinite(result.aod[0])
    assert np.isnan(result.aod[1:]).all()
    np.testing.assert_array_equal(result.flag, ["interpolated"] + ["missing"] * 5)


def test_fallback_can_use_bands_above_quadratic_fit_limit() -> None:
    wavelength = np.array([1020, 1640])
    aod = 0.4 * (wavelength / 550) ** -1.2
    result = quadratic_loglog(wavelength, aod)
    assert result.flag == "extrapolated"
    assert result.aod == pytest.approx(0.4, rel=1e-13)


def test_conversion_happens_before_window_averaging() -> None:
    frame = pd.DataFrame({"AOD_500nm": [0.1, 1.0], "440-675_Angstrom_Exponent": [0.2, 2.0]})
    converted = convert_550nm(frame, method="angstrom_500")
    expected = (0.1 * (550 / 500) ** -0.2 + 1.0 * (550 / 500) ** -2) / 2
    assert converted["aod_550nm"].mean() == pytest.approx(expected)
    assert converted["aod_550nm"].mean() != pytest.approx(
        angstrom_500(frame["AOD_500nm"].mean(), frame["440-675_Angstrom_Exponent"].mean()).aod
    )


def test_empty_inputs_and_unavailable_bands_are_missing() -> None:
    empty = parse_aeronet(AERONET_PREAMBLE + "\n")
    assert convert_550nm(empty).empty
    assert convert_550nm(empty, method="angstrom_500").empty
    frame = pd.DataFrame({"AERONET_Site": ["GSFC"]})
    assert convert_550nm(frame)["spectral_flag"].iloc[0] == "missing"
    assert convert_550nm(frame, method="angstrom_500")["spectral_flag"].iloc[0] == "missing"
    assert quadratic_loglog([440, 500, 675], np.empty((0, 3))).aod.size == 0


@pytest.mark.parametrize(
    "wavelengths,aod",
    [([500, 500], [0.1, 0.1]), ([np.nan], [0.1]), ([-440], [0.1]), ([440], 0.1), ([440], [1, 2])],
)
def test_invalid_spectral_shapes_and_wavelengths(wavelengths: list, aod: object) -> None:
    with pytest.raises(ValueError):
        quadratic_loglog(wavelengths, aod)


def test_window_uncertainty_and_instrument_sensitivity() -> None:
    assert window_uncertainty([0.1, np.nan]) == 0.01
    assert np.isnan(window_uncertainty([np.nan]))
    assert window_uncertainty([0.1, 0.3]) == pytest.approx(np.sqrt(0.01**2 + 0.02))
    assert window_uncertainty([0.1, 0.3], instrument_sigma=0.02) == pytest.approx(
        np.sqrt(0.02**2 + 0.02)
    )
    for sigma in (0, -0.01, np.nan, np.inf):
        with pytest.raises(ValueError, match="instrument_sigma"):
            window_uncertainty([0.1], instrument_sigma=sigma)
