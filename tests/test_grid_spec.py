"""f09 geometry and file-grid compatibility with the CAM7 consumer."""

from pathlib import Path

import netCDF4
import numpy as np
import pytest

from sarb_aod.grid.spec import (
    DLON,
    NLAT,
    NLON,
    f09_lat,
    f09_lat_bnds,
    f09_lon,
    f09_lon_bnds,
    lat_edges,
    load_grid,
    lon_edges,
    normalize_longitude,
)


def test_canonical_f09_matches_cam7_coordinates_and_bounds() -> None:
    """Independent arithmetic for the pole-centered CAM7 f09 contract."""
    assert (NLAT, NLON, DLON) == (192, 288, 1.25)
    expected_lat = np.array([-90.0 + i * (180.0 / 191) for i in range(192)])
    expected_lon = np.array([i * 1.25 for i in range(288)])
    np.testing.assert_allclose(f09_lat(), expected_lat, rtol=0, atol=3e-14)
    np.testing.assert_array_equal(f09_lon(), expected_lon)
    expected_lat_edges = np.array(
        [-90.0] + [-90.0 + (i + 0.5) * 180 / 191 for i in range(191)] + [90.0]
    )
    expected_lon_edges = np.array([i * 1.25 - 0.625 for i in range(289)])
    np.testing.assert_allclose(lat_edges(), expected_lat_edges, rtol=0, atol=3e-14)
    np.testing.assert_array_equal(lon_edges(), expected_lon_edges)
    np.testing.assert_allclose(f09_lat_bnds()[:, 0], expected_lat_edges[:-1], rtol=0, atol=3e-14)
    np.testing.assert_allclose(f09_lat_bnds()[:, 1], expected_lat_edges[1:], rtol=0, atol=3e-14)
    np.testing.assert_array_equal(f09_lon_bnds()[:, 0], expected_lon_edges[:-1])
    np.testing.assert_array_equal(f09_lon_bnds()[:, 1], expected_lon_edges[1:])
    grid = load_grid()
    assert grid.shape == (192, 288)
    np.testing.assert_array_equal(grid.lat_bnds, f09_lat_bnds())
    np.testing.assert_array_equal(grid.lon_bnds, f09_lon_bnds())
    assert np.all(np.diff(grid.lat_edges) > 0)
    assert np.all(np.diff(grid.lon_edges) > 0)


def _write_grid(path: Path, lat: np.ndarray, lon: np.ndarray) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("lat", lat.size)
        dataset.createDimension("lon", lon.size)
        dataset.createDimension("bnds", 2)
        dataset.createVariable("lat", "f8", ("lat",))[:] = lat
        dataset.createVariable("lon", "f8", ("lon",))[:] = lon
        # An accepted grid must derive all bounds from canonical f09, even
        # when the coordinate file's bounds are incorrect.
        dataset.createVariable("lat_bnds", "f8", ("lat", "bnds"))[:] = 0
        dataset.createVariable("lon_bnds", "f8", ("lon", "bnds"))[:] = 0


def test_file_grid_accepts_tolerance_but_returns_exact_f09(tmp_path: Path) -> None:
    path = tmp_path / "grid.nc"
    _write_grid(path, f09_lat() + 0.5e-6, f09_lon() - 0.5e-6)
    grid = load_grid(f"file:{path}")
    np.testing.assert_array_equal(grid.lat, f09_lat())
    np.testing.assert_array_equal(grid.lon, f09_lon())
    np.testing.assert_array_equal(grid.lat_bnds, f09_lat_bnds())
    np.testing.assert_array_equal(grid.lon_bnds, f09_lon_bnds())


@pytest.mark.parametrize("kind", ["lat_shift", "lon_shift", "nan", "size", "reversed"])
def test_file_grid_rejects_incompatible_centers(tmp_path: Path, kind: str) -> None:
    lat, lon = f09_lat(), f09_lon()
    if kind == "lat_shift":
        lat += 1.1e-6
    elif kind == "lon_shift":
        lon[-1] += 1.1e-6  # Absolute tolerance, never NumPy's default relative tolerance.
    elif kind == "nan":
        lat[3] = np.nan
    elif kind == "size":
        lat = lat[:-1]
    else:
        lon = lon[::-1]
    path = tmp_path / "bad-grid.nc"
    _write_grid(path, lat, lon)
    with pytest.raises(ValueError, match="centers must equal CAM f09"):
        load_grid(f"file:{path}")


def test_file_grid_rejects_missing_coordinates(tmp_path: Path) -> None:
    path = tmp_path / "empty.nc"
    with netCDF4.Dataset(path, "w"):
        pass
    with pytest.raises(ValueError, match="no 'lat' coordinate"):
        load_grid(f"file:{path}")


@pytest.mark.parametrize("source", ["", "f19", "file:", "file: ", "grid.nc"])
def test_unknown_grid_source(source: str) -> None:
    with pytest.raises(ValueError, match="grid.source"):
        load_grid(source)


def test_longitude_seam_and_final_edge_bin_assignment() -> None:
    values = np.array([-0.625, -0.6, -1e-4, 359.4, 359.375, -180, 180, 0, 360, -360])
    wrapped = normalize_longitude(values)
    columns = np.clip(np.searchsorted(lon_edges(), wrapped, side="right") - 1, 0, NLON - 1)
    np.testing.assert_array_equal(columns, [0, 0, 0, 0, 287, 144, 144, 0, 0, 0])
    assert wrapped[0] == -0.625
    assert wrapped[4] == 359.375


def test_longitude_preserves_shape_and_maps_nonfinite_to_nan() -> None:
    values = np.array([[np.nan, np.inf], [-np.inf, 180]])
    with np.errstate(all="raise"):
        wrapped = normalize_longitude(values)
    assert wrapped.shape == (2, 2)
    np.testing.assert_array_equal(np.isnan(wrapped), [[True, True], [True, False]])
    assert normalize_longitude(180).shape == ()
    assert normalize_longitude([]).shape == (0,)
