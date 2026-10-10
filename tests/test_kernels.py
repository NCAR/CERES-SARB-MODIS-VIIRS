"""Independent NumPy reference for the streamed sufficient statistics."""

import numpy as np
import pytest

from sarb_aod.grid.kernels import (
    Accumulators,
    accumulate,
    bin_swath_to_grid,
    bin_swath_to_grid_uncertainty,
    count_pixels,
    edges_from_centers,
    normalize_grid,
    normalize_uncertainty_grid,
)


def numpy_reference(te, ye, xe, t, lat, lon, x, sigma, stream, shape):
    out = [np.zeros(shape, dtype=np.int64) for _ in range(3)]
    out += [np.zeros(shape) for _ in range(5)]
    ok = np.isfinite(t) & np.isfinite(lat) & np.isfinite(lon) & np.isfinite(x)
    ok &= (t >= te[0]) & (t < te[-1])
    ok &= (lat >= ye[0]) & (lat <= ye[-1]) & (lon >= xe[0]) & (lon <= xe[-1])
    ti = np.searchsorted(te, t[ok], side="right") - 1
    yi = np.minimum(np.searchsorted(ye, lat[ok], side="right") - 1, shape[2] - 1)
    xi = np.minimum(np.searchsorted(xe, lon[ok], side="right") - 1, shape[3] - 1)
    idx = (ti, np.full(len(ti), stream), yi, xi)
    x, sigma = x[ok], sigma[ok]
    valid_sigma = np.isfinite(sigma) & (sigma > 0)
    for arr, values in zip(out[:5], [1, x < 0, ~valid_sigma, x, x**2]):
        np.add.at(arr, idx, values)
    idxw = tuple(v[valid_sigma] for v in idx)
    inv = 1 / sigma[valid_sigma]
    for arr, values in zip(out[5:], [inv**2, inv**2 * x[valid_sigma], inv]):
        np.add.at(arr, idxw, values)
    return out


def test_accumulate_matches_numpy_random_and_edges():
    rng = np.random.default_rng(7294)
    size = 100_000
    te = np.arange(9, dtype=float) * 10800
    ye = np.linspace(-90, 90, 20)
    xe = np.linspace(-0.625, 359.375, 31)
    t = rng.uniform(-1000, 88000, size)
    lat = rng.uniform(-95, 95, size)
    lon = rng.uniform(-5, 365, size)
    x = rng.normal(0.2, 0.3, size)
    sigma = rng.uniform(0.01, 1, size)
    for a in [t, lat, lon, x, sigma]:
        a[rng.choice(size, size // 40, replace=False)] = np.nan
        a[rng.choice(size, size // 40, replace=False)] = np.inf
    t[:6] = [0, 10800, 86400, 86399.9, 0, 0]
    lat[:6] = [-90, 90, 0, 90, -999, 0]
    lon[:6] = [-0.625, 359.375, 0, 359.375, 1, 0]
    x[:6] = [-0.05, 1, 1, 1, 1, 1]
    sigma[:6] = [0.1, 0.2, 0.3, np.nan, 0.1, 0]
    a = Accumulators.create(2, nlat=19, nlon=30)
    accumulate(te, ye, xe, t, lat, lon, x, sigma, 1, *a.arrays())
    reference = numpy_reference(te, ye, xe, t, lat, lon, x, sigma, 1, a.n.shape)
    for actual, expected in zip(a.arrays(), reference):
        if actual.dtype == np.int64:
            np.testing.assert_array_equal(actual, expected)
        else:
            np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-12)
    assert a.n[:, 0].sum() == 0
    count_pixels(
        te,
        ye,
        xe,
        t[np.isfinite(x)],
        lat[np.isfinite(x)],
        lon[np.isfinite(x)],
        1,
        a.n_rejected_high,
    )
    np.testing.assert_array_equal(a.n, a.n_rejected_high)


def test_f09_longitudes_poles_and_half_open_time():
    from sarb_aod.grid.spec import load_grid, normalize_longitude

    g = load_grid()
    lon = normalize_longitude(np.array([-0.625, -0.6, -1e-4, 359.4, 359.375, -180, 180]))
    t = np.full(7, 10800.0)
    lat = np.array([-90, 90, 0, 0, 0, 0, 0.0])
    a = Accumulators.create(1)
    te = np.arange(9, dtype=float) * 10800
    accumulate(te, g.lat_edges, g.lon_edges, t, lat, lon, np.ones(7), np.ones(7), 0, *a.arrays())
    assert a.n.sum() == 7
    assert a.n[1, 0, 0, 0] == 1
    assert a.n[1, 0, 191, 0] == 1
    assert a.n[1, 0, :, 0].sum() == 4
    assert a.n[1, 0, :, 287].sum() == 1
    assert a.n[1, 0, :, 144].sum() == 2
    count_pixels(
        te,
        g.lat_edges,
        g.lon_edges,
        np.array([86400.0]),
        np.array([0.0]),
        np.array([0.0]),
        0,
        a.n_rejected_high,
    )
    assert a.n_rejected_high.sum() == 0


def test_davinci_ports_axis_order_normalization_and_nonfinite():
    te, xe, ye = np.array([0.0, 3.0]), np.array([0.0, 1.0, 3.0]), np.array([-90.0, 0.0, 90.0])
    t, lon, lat = np.array([0.0, 3, 1]), np.array([0.0, 3, 1]), np.array([-90.0, 90, 0])
    x = np.array([2.0, 4, np.inf])
    counts = np.zeros((1, 2, 2), dtype=np.int64)
    sums = np.zeros_like(counts, dtype=float)
    bin_swath_to_grid(te, xe, ye, t, lon, lat, x, counts, sums)
    assert counts.sum() == 2
    normalize_grid(counts, sums)
    assert sums[0, 0, 0] == 2 and sums[0, 1, 1] == 4
    assert np.isnan(sums[0, 0, 1])
    weights, weighted = np.zeros_like(sums), np.zeros_like(sums)
    counts.fill(0)
    bin_swath_to_grid_uncertainty(
        te, xe, ye, t, lon, lat, x, np.array([1.0, 2, 1]), counts, weights, weighted
    )
    means, sigma = normalize_uncertainty_grid(counts, weights, weighted)
    np.testing.assert_allclose(means, sums)
    assert sigma[0, 0, 0] == 1 and sigma[0, 1, 1] == 2
    np.testing.assert_allclose(edges_from_centers(np.array([2.0, 1, 0])), [-0.5, 0.5, 1.5, 2.5])


def test_invalid_stream_and_pixel_lengths_raise():
    a = Accumulators.create(1, nlat=1, nlon=1)
    edges, v = np.array([0.0, 1.0]), np.array([0.5])
    with pytest.raises(ValueError, match="stream"):
        accumulate(edges, edges, edges, v, v, v, v, v, 1, *a.arrays())
    with pytest.raises(ValueError, match="length"):
        accumulate(edges, edges, edges, v, v, v, v, v[:0], 0, *a.arrays())
