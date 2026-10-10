"""Hand-calculated DAVINCI definitions and aerosol-specific diagnostics."""

import numpy as np
import pytest

from sarb_aod.evaluation.metrics import (
    ALL_METRICS,
    DEFAULT_EE,
    METRIC_REGISTRY,
    EEEnvelope,
    compute_metric,
    expected_error,
    get_metric,
    list_metrics,
    regression,
    uncertainty_calibration,
)

# For x=(1,2,4), y=(2,1,7): errors=(1,-1,3), sum(x)=7,
# sum(y)=10, centered sums xx=14/3, yy=62/3 and xy=26/3.
# Agreement denominators are 137/3 (IOA), 29/3 (d1), 10/3 (E1).
GOLDEN = {
    "N": 3.0,
    "MX": 7 / 3,
    "MY": 10 / 3,
    "STDX": np.sqrt(7 / 3),
    "STDY": np.sqrt(31 / 3),
    "MdnX": 2.0,
    "MdnY": 2.0,
    "MB": 1.0,
    "MdnB": 1.0,
    "NMB": 300 / 7,
    "NMdnB": 50.0,
    "FB": 600 / 17,
    "MNB": 125 / 3,
    "ME": 5 / 3,
    "MdnE": 1.0,
    "RMSE": np.sqrt(11 / 3),
    "NME": 500 / 7,
    "FE": 6200 / 99,
    "MNE": 75.0,
    "R": 13 / np.sqrt(217),
    "R2": 169 / 217,
    "IOA": 104 / 137,
    "d1": 14 / 29,
    "E1": -0.5,
    "AC": 13 / np.sqrt(217),
    "RM": 43 / 42,
    "RMdn": 4 / 7,
}


@pytest.mark.parametrize("name,expected", GOLDEN.items())
def test_all_27_definitions_against_hand_calculation(name: str, expected: float) -> None:
    # Invalid entries must be removed as pairs, not independently in x and y.
    x = np.array([1, 2, 4, np.nan, 100, np.inf])
    y = np.array([2, 1, 7, 100, np.inf, 100])
    assert compute_metric(name, x, y) == pytest.approx(expected, rel=1e-14, abs=1e-14)


def test_registry_names_and_result_metadata() -> None:
    assert len(ALL_METRICS) == len(METRIC_REGISTRY) == 27
    assert set(ALL_METRICS) == set(GOLDEN)
    assert list_metrics() == sorted(GOLDEN)
    result = get_metric("NMB")(np.array([1, 2, 4]), np.array([2, 1, 7]))
    assert (result.name, result.long_name, result.units) == ("NMB", "Normalized Mean Bias", "%")
    assert result.value == pytest.approx(300 / 7)
    with pytest.raises(KeyError):
        get_metric("not_a_metric")


def test_normalization_and_ratio_denominator_zero_rules() -> None:
    x, y = np.array([0, 1, 2]), np.array([0, 2, 0])
    expected = {"MNB": 0, "MNE": 100, "RM": 0.5, "RMdn": 0.5, "NMB": -100 / 3, "FE": 400 / 3}
    for name, value in expected.items():
        assert compute_metric(name, x, y) == pytest.approx(value)


@pytest.mark.parametrize("name", ALL_METRICS)
def test_empty_pairs(name: str) -> None:
    value = compute_metric(name, np.array([np.nan]), np.array([1]))
    assert value == 0 if name == "N" else np.isnan(value)


@pytest.mark.parametrize(
    "name",
    [
        "R",
        "R2",
        "AC",
        "IOA",
        "d1",
        "E1",
        "NMB",
        "NME",
        "FB",
        "FE",
        "RM",
        "RMdn",
        "MNB",
        "MNE",
        "NMdnB",
    ],
)
def test_zero_denominator_is_nan(name: str) -> None:
    assert np.isnan(compute_metric(name, np.zeros(3), np.zeros(3)))


def test_dt_ocean_asymmetric_ee_and_inclusive_boundaries() -> None:
    # x=.5 -> lower=-.07, upper=.09. Four within, one on either side.
    result = expected_error([0.5] * 6, [0.43, 0.59, 0.42, 0.60, 0.5, 0.53], "dt_ocean")
    assert result["N_EE"] == 6
    assert result["pct_ee"] == pytest.approx(200 / 3)
    assert result["fraction_above_ee"] == pytest.approx(1 / 6)
    assert result["fraction_below_ee"] == pytest.approx(1 / 6)
    assert result["pct_ee"] / 100 + result["fraction_above_ee"] + result[
        "fraction_below_ee"
    ] == pytest.approx(1)


@pytest.mark.parametrize(
    "family,upper",
    [("dt_land", 0.2), ("dt_ocean", 0.14), ("db_land", 0.25), ("viirs_db_water", 0.13)],
)
def test_expected_error_uses_reference_aod_and_separate_family_table(
    family: str, upper: float
) -> None:
    result = expected_error([1, 1, np.nan], [1 + upper, 1 + upper + 0.001, 1], family)
    assert result["pct_ee"] == 50
    assert result["fraction_above_ee"] == 0.5
    assert result["N_EE"] == 2
    assert family in DEFAULT_EE


def test_configurable_envelope_and_empty_result() -> None:
    assert expected_error([1], [1.05], EEEnvelope(0.01, 0, 0.1, 0))["pct_ee"] == 100
    result = expected_error([], [])
    assert result["N_EE"] == 0 and np.isnan(result["pct_ee"])
    with pytest.raises(ValueError, match="coefficients"):
        EEEnvelope(-0.01, 0, 0.1, 0)
    with pytest.raises(ValueError, match="identical shapes"):
        expected_error([1, 2], [1])


@pytest.mark.parametrize("slope,intercept", [(2.0, 1.0), (-2.0, 7.0)])
def test_ols_and_signed_rma_on_exact_lines(slope: float, intercept: float) -> None:
    x = np.array([1, 2, 3, np.nan])
    result = regression(x, slope * x + intercept)
    for method in ("ols", "rma"):
        assert result[f"{method}_slope"] == pytest.approx(slope)
        assert result[f"{method}_intercept"] == pytest.approx(intercept)


def test_ols_and_rma_differ_for_scattered_pairs() -> None:
    result = regression([1, 2, 4], [2, 1, 7])
    assert result["ols_slope"] == pytest.approx(13 / 7)
    assert result["ols_intercept"] == pytest.approx(-1)
    assert result["rma_slope"] == pytest.approx(np.sqrt(31 / 7))
    assert result["rma_intercept"] == pytest.approx(10 / 3 - np.sqrt(31 / 7) * 7 / 3)


def test_regression_degenerate_samples() -> None:
    assert all(np.isnan(value) for value in regression([1, 1, 1], [1, 2, 3]).values())
    constant = regression([1, 2, 3], [5, 5, 5])
    assert constant["ols_slope"] == 0 and constant["ols_intercept"] == 5
    assert np.isnan(constant["rma_slope"])
    assert all(np.isnan(value) for value in regression([1], [2]).values())


def test_uncertainty_calibration_known_z_values_and_strict_thresholds() -> None:
    result = uncertainty_calibration(np.zeros(5), [-2, -1, 0, 1, 2], 0.6, 0.8)
    assert result["N_z"] == 5
    assert result["z_mean"] == 0
    assert result["z_std"] == pytest.approx(np.sqrt(2.5))
    assert result["fraction_abs_z_lt_1"] == 0.2
    assert result["fraction_abs_z_lt_2"] == 0.6


def test_uncertainty_calibration_standard_normal_coverage() -> None:
    rng = np.random.default_rng(1309)
    n = 100_000
    x = np.full(n, 0.2)
    product_error = rng.normal(0, 0.04, n)
    reference_error = rng.normal(0, 0.03, n)
    result = uncertainty_calibration(x + reference_error, x + product_error, 0.04, 0.03)
    assert 0.99 < result["z_std"] < 1.01
    assert 0.675 < result["fraction_abs_z_lt_1"] < 0.690
    assert 0.949 < result["fraction_abs_z_lt_2"] < 0.960


def test_uncertainty_missing_negative_and_zero_values_are_excluded() -> None:
    result = uncertainty_calibration([0] * 5, [1] * 5, [1, -1, np.nan, 0, np.inf], [0] * 5)
    assert result["N_z"] == 1 and result["z_mean"] == 1
    assert np.isnan(result["z_std"])
    empty = uncertainty_calibration([0], [1], 0, 0)
    assert empty["N_z"] == 0 and np.isnan(empty["fraction_abs_z_lt_1"])
    with pytest.raises(ValueError, match="uncertainties"):
        uncertainty_calibration([0, 1], [0, 1], [1, 2, 3], 0.01)
