"""Canonical CAM-FV f09 (0.9x1.25) grid: pole-centered, lon starts at 0.

Vendored from NCAR/CERES-SARB-CAM7 sp1_aod_pipeline/f09_grid.py,
commit cd1b5d45dbf3ddfb737c3a7153b67cdf2adef254. Behavior unchanged.

Authoritative reference file (centers verified to 1e-9):
/glade/campaign/cesm/cesmdata/cseg/inputdata/atm/cam/coords/fv_0.9x1.25.nc
"""

import numpy as np

NLAT = 192
NLON = 288
DLON = 1.25


def f09_lat():
    return np.linspace(-90.0, 90.0, NLAT)


def f09_lon():
    return np.arange(NLON) * DLON


def lat_edges():
    lat = f09_lat()
    mids = 0.5 * (lat[:-1] + lat[1:])
    return np.concatenate([[-90.0], mids, [90.0]])


def lon_edges():
    return np.arange(NLON + 1) * DLON - 0.5 * DLON


def f09_lat_bnds():
    edges = lat_edges()
    return np.stack([edges[:-1], edges[1:]], axis=1)


def f09_lon_bnds():
    edges = lon_edges()
    return np.stack([edges[:-1], edges[1:]], axis=1)
