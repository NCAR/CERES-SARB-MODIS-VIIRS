"""Scientific checks for count weighting and correlated-error GLS."""

import numpy as np
import pytest

from sarb_aod.grid.combine import FILL_FLOAT, combine
from sarb_aod.grid.kernels import Accumulators


def _one_cell(*streams: tuple[list[float], list[float]]) -> Accumulators:
    """Independent NumPy sufficient statistics; no gridding implementation."""
    acc = Accumulators.create(nstream=len(streams), nwin=1, nlat=1, nlon=1)
    for index, (aod, uncertainty) in enumerate(streams):
        x = np.asarray(aod, dtype=np.float64)
        sigma = np.asarray(uncertainty, dtype=np.float64)
        good_sigma = np.isfinite(sigma) & (sigma > 0)
        cell = (0, index, 0, 0)
        acc.n[cell] = x.size
        acc.n_neg[cell] = np.count_nonzero(x < 0)
        acc.n_sigma_missing[cell] = np.count_nonzero(~good_sigma)
        acc.sum_x[cell] = x.sum()
        acc.sum_x2[cell] = np.dot(x, x)
        weights = 1 / sigma[good_sigma] ** 2
        acc.sum_w[cell] = weights.sum()
        acc.sum_wx[cell] = np.dot(x[good_sigma], weights)
        acc.sum_inv_sigma[cell] = (1 / sigma[good_sigma]).sum()
    return acc


def test_count_is_pixel_mean_and_pooled_standard_error() -> None:
    streams = [([-0.05, 0.1, 0.3], [0.1, 0.2, 0.3]), ([0.9], [np.nan])]
    acc = _one_cell(*streams)
    original = [array.copy() for array in (*acc.arrays(), acc.n_rejected_high)]
    result = combine(acc, method="count")
    x = np.array([-0.05, 0.1, 0.3, 0.9])
    assert result.mean.item() == pytest.approx(x.mean(), rel=1e-12)
    assert result.sigma.item() == pytest.approx(x.std(ddof=1) / np.sqrt(x.size), rel=1e-12)
    assert result.count.item() == 4
    assert result.n_effective.item() == 4
    assert result.chi2_per_dof.item() == FILL_FLOAT
    np.testing.assert_allclose(result.stream_mean.ravel(), [np.mean(streams[0][0]), 0.9])
    np.testing.assert_array_equal(result.stream_count.ravel(), [3, 1])
    assert result.count_negative.item() == 1
    assert result.count_sigma_missing.item() == 1
    for before, after in zip(original, (*acc.arrays(), acc.n_rejected_high), strict=True):
        np.testing.assert_array_equal(before, after)


def test_count_sample_variance_is_clipped_for_roundoff() -> None:
    acc = _one_cell(([0.1] * 100, [0.1] * 100))
    # A one-ULP negative centered sum can occur for identical samples.
    acc.sum_x2[:] = np.nextafter(acc.sum_x**2 / acc.n, -np.inf)
    assert combine(acc, method="count").sigma.item() == 0


@pytest.mark.parametrize("policy,expected", [("floor", 0.025), ("zero", 0.0)])
def test_single_pixel_count_sigma_and_unclipped_mean(policy: str, expected: float) -> None:
    result = combine(
        _one_cell(([-0.02], [np.nan])),
        method="count",
        single_pixel_sigma=policy,
        sigma_floor=0.025,
    )
    assert result.mean.item() == -0.02
    assert result.stream_mean.item() == -0.02
    assert result.sigma.item() == expected


def test_independent_equal_sigma_gls_matches_count_mean() -> None:
    rng = np.random.default_rng(123)
    streams = [(rng.uniform(0, 1, n).tolist(), [0.13] * n) for n in (1, 5, 19, 90)]
    acc = _one_cell(*streams)
    result = combine(acc, method="gls", rho=0, birge=False)
    counted = combine(acc, method="count")
    np.testing.assert_allclose(result.mean, counted.mean, rtol=1e-12)
    assert result.sigma.item() == pytest.approx(0.13 / np.sqrt(115), rel=1e-12)
    assert result.n_effective.item() == pytest.approx(115, rel=1e-12)
    assert result.sigma.item() != pytest.approx(counted.sigma.item(), rel=0.1)


def test_heterogeneous_pixel_sigma_uses_covariance_of_weighted_mean() -> None:
    x = np.array([0.1, 0.2, 0.4, 0.7])
    sigma = np.array([0.02, 0.06, 0.15, 0.3])
    rho = 0.5
    w = 1 / sigma**2
    covariance = rho * np.outer(sigma, sigma) + (1 - rho) * np.diag(sigma**2)
    expected_variance = (w @ covariance @ w) / w.sum() ** 2
    result = combine(_one_cell((x.tolist(), sigma.tolist())), rho=rho)
    assert result.mean.item() == pytest.approx(np.dot(w, x) / w.sum(), rel=1e-12)
    assert result.sigma.item() ** 2 == pytest.approx(expected_variance, rel=1e-12)
    expected_effective = (np.dot(w, sigma) / w.sum()) ** 2 / expected_variance
    assert result.n_effective.item() == pytest.approx(expected_effective, rel=1e-12)
    assert result.chi2_per_dof.item() == FILL_FLOAT


@pytest.mark.parametrize("rho", [0.0, 0.5, 0.9])
@pytest.mark.parametrize("n", [1, 10, 100])
def test_monte_carlo_standardized_correlated_errors(rho: float, n: int) -> None:
    rng = np.random.default_rng(702 + n)
    simulations = 20_000
    sigma = np.geomspace(0.015, 0.5, n)
    # Each row is a realization with Cov(e_i,e_j) = rho*sigma_i*sigma_j.
    common = rng.standard_normal((simulations, 1))
    independent = rng.standard_normal((simulations, n))
    errors = sigma * (np.sqrt(rho) * common + np.sqrt(1 - rho) * independent)
    weights = 1 / sigma**2
    acc = Accumulators.create(nstream=1, nwin=simulations, nlat=1, nlon=1)
    acc.n[:] = n
    acc.sum_w[:] = weights.sum()
    acc.sum_inv_sigma[:] = (1 / sigma).sum()
    acc.sum_wx[:, 0, 0, 0] = errors @ weights
    result = combine(acc, rho=rho)
    standardized = result.mean.ravel() / result.sigma.ravel()
    assert 0.97 < standardized.std(ddof=1) < 1.03


@pytest.mark.parametrize("rho", [0.0, 0.5, 0.999999, 1.0])
def test_equal_sigma_correlation_floor_and_saturation(rho: float) -> None:
    n, sigma = 100, 0.2
    result = combine(_one_cell(([0.3] * n, [sigma] * n)), rho=rho)
    variance = sigma**2 * (rho + (1 - rho) / n)
    assert result.sigma.item() ** 2 == pytest.approx(variance, rel=1e-12)
    assert result.n_effective.item() == pytest.approx(1 / (rho + (1 - rho) / n), rel=1e-12)


@pytest.mark.parametrize("other,inflate", [(0.2, False), (0.21, False), (0.8, True)])
def test_birge_inflates_only_disagreement_and_retains_diagnostic(
    other: float, inflate: bool
) -> None:
    acc = _one_cell(([0.2], [0.1]), ([other], [0.1]))
    off = combine(acc, birge=False)
    on = combine(acc, birge=True)
    expected_chi2 = ((0.2 - other) ** 2) / (2 * 0.1**2)
    assert on.chi2_per_dof.item() == pytest.approx(expected_chi2, abs=1e-14)
    np.testing.assert_array_equal(on.chi2_per_dof, off.chi2_per_dof)
    np.testing.assert_array_equal(on.mean, off.mean)
    np.testing.assert_array_equal(on.n_effective, off.n_effective)
    assert on.sigma.item() == pytest.approx(
        off.sigma.item() * (np.sqrt(expected_chi2) if inflate else 1), rel=1e-12
    )


def test_missing_sigma_excluded_only_from_gls_mean_and_min_pixels_counts_all() -> None:
    acc = _one_cell(([0.1, 0.9], [0.2, np.nan]))
    result = combine(acc, min_pixels=2)
    assert result.mean.item() == pytest.approx(0.1)
    assert result.sigma.item() == pytest.approx(0.2)
    assert result.count.item() == 2
    assert result.count_sigma_missing.item() == 1
    assert result.n_effective.item() == pytest.approx(1)
    assert combine(acc, method="count").mean.item() == 0.5


@pytest.mark.parametrize("method", ["gls", "count"])
def test_min_pixels_masks_product_and_preserves_raw_count_diagnostics(method: str) -> None:
    acc = _one_cell(([-0.02, 0.1], [0.1, np.nan]), ([0.3], [0.1]))
    acc.n_rejected_high[0, 0, 0, 0] = 7
    result = combine(acc, method=method, min_pixels=4)
    for values in (
        result.mean,
        result.sigma,
        result.n_effective,
        result.chi2_per_dof,
        result.stream_mean,
    ):
        assert np.all(values == FILL_FLOAT)
    assert result.count.item() == 0
    assert result.count_negative.item() == 1
    assert result.count_sigma_missing.item() == 1
    assert result.count_rejected_high.item() == 7
    np.testing.assert_array_equal(result.stream_count.ravel(), [2, 1])


def test_all_sigma_missing_has_no_gls_product_but_retains_diagnostics() -> None:
    acc = _one_cell(([0.1, -0.02], [np.nan, np.nan]))
    result = combine(acc)
    assert result.count.item() == 0
    assert result.count_sigma_missing.item() == 2
    assert result.stream_count.item() == 2
    assert result.count_negative.item() == 1
    for values in (
        result.mean,
        result.sigma,
        result.n_effective,
        result.chi2_per_dof,
        result.stream_mean,
    ):
        assert np.all(values == FILL_FLOAT)


@pytest.mark.parametrize("method", ["gls", "count"])
def test_empty_and_only_rejected_high_cells(method: str) -> None:
    acc = Accumulators.create(nstream=2, nwin=1, nlat=1, nlon=2)
    acc.n_rejected_high[0, :, 0, 1] = [2, 3]
    with np.errstate(all="raise"):
        result = combine(acc, method=method)
    for values in (
        result.mean,
        result.sigma,
        result.n_effective,
        result.chi2_per_dof,
        result.stream_mean,
    ):
        assert np.all(values == FILL_FLOAT)
    assert np.all(result.count == 0)
    np.testing.assert_array_equal(result.count_rejected_high.ravel(), [0, 5])


@pytest.mark.parametrize("method", ["gls", "count"])
def test_instrument_selection_and_absent_instrument(method: str) -> None:
    acc = _one_cell(([0.1], [0.1]), ([0.4, 0.6], [0.2, 0.2]), ([0.9], [0.1]))
    result = combine(acc, method=method, stream_indices=[2, 0])
    assert result.mean.item() == pytest.approx(0.5)
    assert result.count.item() == 2
    assert result.stream_indices == (2, 0)
    np.testing.assert_allclose(result.stream_mean.ravel(), [0.9, 0.1])
    empty = combine(acc, method=method, stream_indices=[])
    assert empty.mean.item() == FILL_FLOAT
    assert empty.count.item() == 0
    assert empty.stream_mean.shape == (1, 0, 1, 1)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"method": "unknown"},
        {"rho": -0.1},
        {"rho": 1.1},
        {"rho": np.nan},
        {"min_pixels": 0},
        {"min_pixels": 1.5},
        {"min_pixels": True},
        {"single_pixel_sigma": "unknown"},
        {"sigma_floor": 0},
        {"sigma_floor": np.inf},
        {"stream_indices": [0, 0]},
        {"stream_indices": [1]},
        {"stream_indices": [-1]},
        {"stream_indices": [True]},
        {"stream_indices": [0.5]},
    ],
)
def test_invalid_combination_parameters(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        combine(_one_cell(([0.1], [0.1])), **kwargs)
