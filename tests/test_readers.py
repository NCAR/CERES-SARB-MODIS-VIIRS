"""Synthetic file readers, surface QC, packed masks, and staged-data smoke tests."""

from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from netCDF4 import Dataset

from sarb_aod.config.loader import load_config
from sarb_aod.config.schema import (
    AlgorithmSpec,
    AODCorrectionConfig,
    Geolocation,
    QCConfig,
    TimeConfig,
    VarRef,
)
from sarb_aod.paths import granule_dir
from sarb_aod.readers import LAND, OCEAN, read_granule
from sarb_aod.readers.modis_hdf4 import HDF4Source
from sarb_aod.readers.netcdf import NetCDFSource
from sarb_aod.streams import StreamRecipe, resolve_stream

DAY = date(2008, 7, 1)
DAY_START = datetime(2008, 7, 1, tzinfo=timezone.utc).timestamp()
TAI_START = (
    float((np.datetime64("2008-07-01") - np.datetime64("1993-01-01")) / np.timedelta64(1, "s")) + 6
)


@pytest.fixture
def hdf_granule(tmp_path):
    sd = pytest.importorskip("pyhdf.SD")
    path = tmp_path / "synthetic.hdf"
    handle = sd.SD(str(path), sd.SDC.WRITE | sd.SDC.CREATE)
    shape = (2, 6)

    def field(name, values, dimensions, *, fill=-9999, limits=None, scale=None):
        data = np.asarray(values)
        kind = sd.SDC.INT16 if data.dtype.kind in "iu" else sd.SDC.FLOAT64
        data = data.astype(np.int16 if kind == sd.SDC.INT16 else np.float64)
        variable = handle.create(name, kind, data.shape)
        for index, dimension in enumerate(dimensions):
            variable.dim(index).setname(dimension)
        variable[:] = data
        variable.attr("_FillValue").set(kind, fill)
        if limits is not None:
            variable.attr("valid_range").set(kind, limits)
        if scale is not None:
            variable.attr("scale_factor").set(sd.SDC.FLOAT64, scale)
        variable.endaccess()

    dims = ("Cell_Along_Swath:mod04", "Cell_Across_Swath:mod04")
    lat = np.arange(12.0).reshape(shape)
    lon = -120 + np.arange(12.0).reshape(shape)
    lat.flat[5] = -999
    lon.flat[6] = -999
    times = np.full(shape, TAI_START + 1)
    times.flat[4] = -9999  # Not the documented fill; packed valid_range must catch it.
    times.flat[7] = TAI_START + 86400
    field("Latitude", lat, dims, fill=-999, limits=[-90, 90])
    field("Longitude", lon, dims, fill=-999, limits=[-180, 180])
    field("Scan_Start_Time", times, dims, fill=-999, limits=[0, 1e9])
    field(
        "Land_Ocean_Quality_Flag",
        np.array([3, 3, -9999, 1, 3, 3, 3, 3, 2, 4, 1, 3]).reshape(shape),
        dims,
        limits=[0, 1],  # Deliberately wrong: flags must be masked by fill only.
    )
    land = np.full((3, *shape), -9999)
    land[1] = np.array(
        [-50, 5000, -9999, -9999, 100, 200, 300, 400, 500, 600, -9999, -9999]
    ).reshape(shape)
    ocean = np.full((7, *shape), -9999)
    ocean[1] = np.array(
        [-9999, -9999, 0, 300, -9999, -9999, -9999, -9999, -9999, -9999, 5000, 5001]
    ).reshape(shape)
    scale = float(np.float32(0.001))
    field(
        "Corrected_Optical_Depth_Land",
        land,
        ("Solution_3_Land:mod04", *dims),
        limits=[-100, 5000],
        scale=scale,
    )
    field(
        "Effective_Optical_Depth_Average_Ocean",
        ocean,
        ("MODIS_Band_Ocean:mod04", *dims),
        limits=[0, 5000],
        scale=scale,
    )
    ocean[1, 1, 4] = 2100
    field(
        "Effective_Optical_Depth_Best_Ocean",
        ocean,
        ("MODIS_Band_Ocean:mod04", *dims),
        limits=[0, 5000],
        scale=scale,
    )
    db_aod = np.full(shape, -9999)
    db_qa = np.zeros(shape, dtype=int)
    db_sigma = np.full(shape, -9999)
    for index, aod, qa, sigma in [(0, 100, 2, -9999), (1, 200, 3, 100), (3, 300, 1, 200)]:
        db_aod.flat[index], db_qa.flat[index], db_sigma.flat[index] = aod, qa, sigma
    field("Deep_Blue_Aerosol_Optical_Depth_550_Land", db_aod, dims, limits=[0, 5000], scale=scale)
    field("Deep_Blue_Aerosol_Optical_Depth_550_Land_QA_Flag", db_qa, dims, fill=0)
    field(
        "Deep_Blue_Aerosol_Optical_Depth_550_Land_Estimated_Uncertainty",
        db_sigma,
        dims,
        limits=[0, 10000],
        scale=scale,
    )
    handle.end()
    return path


def test_hdf_packed_endpoints_flags_geolocation_time_and_missing_sigma(hdf_granule):
    batches = read_granule(
        hdf_granule,
        [resolve_stream("MOD04_L2", "6.1", algorithm) for algorithm in ("dt", "db")],
        day=DAY,
    )
    dt, db = batches["terra_dt"], batches["terra_db"]
    np.testing.assert_array_equal(dt.aod, [-0.05, 5, 0.3, 5])
    np.testing.assert_array_equal(dt.lat, [0, 1, 3, 10])
    np.testing.assert_array_equal(dt.t, np.full(4, DAY_START + 1))
    np.testing.assert_array_equal(dt.surface, [LAND, LAND, OCEAN, OCEAN])
    np.testing.assert_allclose(dt.sigma, [0.0575, 0.8, 0.06, 0.53])
    assert dt.counts["n_negative"] == 1
    assert dt.counts["n_accepted"] == len(dt) == 4
    assert dt.counts["n_located"] == 8
    assert len(dt.rejected_high) == 0
    np.testing.assert_array_equal(db.aod, [0.1, 0.2])
    np.testing.assert_allclose(db.sigma, [np.nan, 0.1], equal_nan=True)
    assert db.counts["n_sigma_missing"] == 1


def test_hdf_legacy_scale_and_ocean_alternate(hdf_granule):
    qc = QCConfig(preset="cam7_v2")
    recipe = resolve_stream("MOD04_L2", "6.1", "dt", preset=qc.preset)
    batch = read_granule(
        hdf_granule, [recipe], qc=qc, time=TimeConfig(leap_seconds="ignore"), day=DAY
    )["terra_dt"]
    np.testing.assert_array_equal(batch.lat, [3, 8, 10])
    np.testing.assert_allclose(batch.aod, np.array([300, 500, 2100]) * float(np.float32(0.001)))
    np.testing.assert_array_equal(batch.t, np.full(3, DAY_START + 7))
    assert len(batch.rejected_high) == 1  # The unrounded packed 5000 land pixel is >5.


@pytest.mark.parametrize("source", ["hdf", "netcdf"])
def test_aod_correction_after_qc_preserves_pixels_and_uncertainties(source, hdf_granule, netcdf_dt):
    if source == "hdf":
        path, day = hdf_granule, DAY
        recipes = [resolve_stream("MOD04_L2", "6.1", a) for a in ("dt", "db")]
    else:
        path, day, recipes = netcdf_dt, date(2020, 9, 8), [_netcdf_recipe()]
    baseline = read_granule(path, recipes, day=day)
    corrected = read_granule(
        path,
        recipes,
        day=day,
        corrections={r.stream_id: AODCorrectionConfig(scale=2, offset=0.2) for r in recipes},
    )
    for stream, original in baseline.items():
        actual = corrected[stream]
        np.testing.assert_allclose(actual.aod, 2 * original.aod + 0.2)
        np.testing.assert_array_equal(actual.sigma, original.sigma)
        np.testing.assert_array_equal(actual.lat, original.lat)
        np.testing.assert_array_equal(actual.t, original.t)
        np.testing.assert_array_equal(actual.rejected_high.lat, original.rejected_high.lat)
        assert actual.counts["n_accepted"] == original.counts["n_accepted"]
        assert actual.counts["n_sigma_missing"] == original.counts["n_sigma_missing"]
        assert actual.counts["n_negative"] == 0


def test_correction_is_isolated_by_satellite_and_algorithm(hdf_granule):
    corrections = {"terra_dt": AODCorrectionConfig(scale=2, offset=0.2)}
    for product in ("MOD04_L2", "MYD04_L2"):
        recipes = [resolve_stream(product, "6.1", a) for a in ("dt", "db")]
        baseline = read_granule(hdf_granule, recipes, day=DAY)
        corrected = read_granule(hdf_granule, recipes, day=DAY, corrections=corrections)
        for stream, original in baseline.items():
            expected = 2 * original.aod + 0.2 if stream == "terra_dt" else original.aod
            np.testing.assert_array_equal(corrected[stream].aod, expected)
            np.testing.assert_array_equal(corrected[stream].sigma, original.sigma)


def test_high_aod_coordinates_require_qa_and_day(hdf_granule):
    batch = read_granule(
        hdf_granule, [resolve_stream("MOD04_L2", "6.1", "dt")], qc=QCConfig(aod_max=1), day=DAY
    )["terra_dt"]
    np.testing.assert_array_equal(batch.aod, [-0.05, 0.3])
    np.testing.assert_array_equal(batch.rejected_high.lat, [1, 10])
    assert batch.counts["n_rejected_high"] == 2
    without_day = read_granule(hdf_granule, [resolve_stream("MOD04_L2", "6.1", "dt")])["terra_dt"]
    assert 7 in without_day.lat


def test_hdf_source_reuses_decoded_selected_field(hdf_granule):
    reference = resolve_stream("MOD04_L2", "6.1", "dt").surfaces["land"].aod
    with HDF4Source(hdf_granule) as source:
        first = source.read(reference)
        assert first.shape == (2, 6)
        assert source.read(reference) is first
        np.testing.assert_allclose(first.flat[:2], [-0.05, 5])
    with pytest.raises(RuntimeError, match="context manager"):
        source.read(reference)


def test_qa_diagnostic_requires_an_aod_retrieval_on_the_same_surface(hdf_granule):
    recipe = resolve_stream("MOD04_L2", "6.1", "dt")
    batch = read_granule(hdf_granule, [recipe], day=DAY)["terra_dt"]
    # The QA=2 land pixel fails land QC; its absent ocean retrieval cannot
    # pass under ocean's looser QA rule. A packed-invalid ocean is also absent.
    assert batch.counts["n_qa_passed"] == 4
    assert batch.counts["n_accepted"] == 4


def _netcdf_recipe(family="viirs_dt"):
    algorithm = "dt" if family.endswith("dt") else "db"
    surfaces = {}
    for surface in ("land", "ocean"):
        surfaces[surface] = {
            "aod": (
                {"name": f"science/{surface}", "select": {"wavelength": 1}}
                if algorithm == "dt"
                else {"name": f"science/{surface}"}
            ),
            "qa": {"name": "science/qa" if algorithm == "dt" else f"science/{surface}_qa"},
            "sigma": None if algorithm == "dt" else {"name": f"science/{surface}_sigma"},
        }
    return StreamRecipe(
        stream_id=f"snpp_{algorithm}",
        product=f"AER{algorithm.upper()}",
        collection="2.1",
        algorithm=algorithm,
        platform="SNPP",
        instrument="VIIRS",
        file_format="netcdf4",
        geolocation=Geolocation(
            lat="geo/lat",
            lon="geo/lon",
            time=None if algorithm == "dt" else "geo/time",
            time_kind="coverage_attrs" if algorithm == "dt" else "tai93",
        ),
        algorithm_spec=AlgorithmSpec.model_validate({"family": family, "surfaces": surfaces}),
    )


@pytest.fixture
def netcdf_dt(tmp_path):
    path = tmp_path / "dt.nc"
    with Dataset(path, "w") as handle:
        handle.createDimension("row", 2)
        handle.createDimension("col", 3)
        handle.createDimension("wavelength", 3)
        handle.time_coverage_start = "2020-09-08T20:24:00.000Z"
        handle.time_coverage_end = "2020-09-08T20:30:00.000Z"
        geo = handle.createGroup("geo")
        science = handle.createGroup("science")
        for name, values in [("lat", np.arange(6).reshape(2, 3)), ("lon", np.zeros((2, 3)))]:
            variable = geo.createVariable(name, "f4", ("row", "col"), fill_value=-999)
            variable[:] = values
        for surface in ("land", "ocean"):
            variable = science.createVariable(
                surface, "i2", ("row", "col", "wavelength"), fill_value=-9999
            )
            variable.scale_factor = np.float32(0.001)
            variable.valid_range = np.array([-100, 5000], dtype=np.int16)
            variable.set_auto_maskandscale(False)
            values = np.full((2, 3, 3), -9999, dtype=np.int16)
            values[:, :, 1] = (
                [[100, -50, -9999], [100, -9999, -9999]]
                if surface == "land"
                else [[-9999, -9999, 200], [-9999, 300, 5000]]
            )
            variable[:] = values
        qa = science.createVariable("qa", "i2", ("row", "col"), fill_value=-9999)
        qa.valid_range = np.array([0, 1], dtype=np.int16)
        qa[:] = [[3, 3, 1], [0, 3, -9999]]
    return path


def test_netcdf_group_paths_last_wavelength_axis_flags_and_coverage(netcdf_dt):
    recipe = _netcdf_recipe()
    batch = read_granule(netcdf_dt, [recipe], day=date(2020, 9, 8))["snpp_dt"]
    np.testing.assert_array_equal(batch.aod, [0.1, -0.05, 0.2, 0.3])
    np.testing.assert_array_equal(batch.surface, [LAND, LAND, OCEAN, OCEAN])
    start = datetime(2020, 9, 8, 20, 24, tzinfo=timezone.utc).timestamp()
    np.testing.assert_array_equal(batch.t, start + np.array([90, 90, 90, 270]))
    with NetCDFSource(netcdf_dt) as source:
        qa = source.read(VarRef(name="science/qa"), flag=True)
        assert qa[1, 2] == 0
        assert qa[0, 0] == 3  # Wrong QA valid_range cannot mask this good flag.
        assert source.read(recipe.surfaces["land"].aod).shape == (2, 3)


@pytest.mark.parametrize(
    "start,end",
    [
        ("2020-09-08T20:24:01Z", "2020-09-08T20:30:01Z"),
        ("2020-09-08T20:24:00Z", "2020-09-08T20:29:59Z"),
        ("2020-09-08T20:23:00Z", "2020-09-08T20:29:00Z"),
    ],
)
def test_netcdf_malformed_coverage_fails(netcdf_dt, start, end):
    with Dataset(netcdf_dt, "a") as handle:
        handle.time_coverage_start = start
        handle.time_coverage_end = end
    with pytest.raises(ValueError, match="six-minute boundary and last 360 s"):
        read_granule(netcdf_dt, [_netcdf_recipe()])


@pytest.mark.parametrize("fill", [0, -999])
def test_netcdf_deep_blue_surface_priority_and_qa_versions(tmp_path, fill):
    path = tmp_path / "db.nc"
    with Dataset(path, "w") as handle:
        handle.createDimension("row", 2)
        handle.createDimension("col", 2)
        geo = handle.createGroup("geo")
        science = handle.createGroup("science")
        geo.createVariable("lat", "f8", ("row", "col"))[:] = [[0, 1], [2, 3]]
        geo.createVariable("lon", "f8", ("row", "col"))[:] = 0
        geo.createVariable("time", "f8", ("row",))[:] = [TAI_START + 1, TAI_START + 2]
        for surface, aod, qa in [
            ("land", [[0.1, 0.2], [-999, 0.4]], [[1, fill], [fill, 3]]),
            ("ocean", [[0.3, 0.5], [0.6, 0.7]], [[1, 3], [3, 3]]),
        ]:
            science.createVariable(surface, "f8", ("row", "col"), fill_value=-999)[:] = aod
            science.createVariable(f"{surface}_qa", "i2", ("row", "col"), fill_value=fill)[:] = qa
            sigma = science.createVariable(
                f"{surface}_sigma", "f8", ("row", "col"), fill_value=-999
            )
            sigma.valid_range = [-0.5, 5]
            sigma[:] = [[-0.0005, 0.1], [0.2, -999]]
    batch = read_granule(
        path, [_netcdf_recipe("viirs_db")], qc=QCConfig(preset="cam7_v2"), day=DAY
    )["snpp_db"]
    np.testing.assert_array_equal(batch.aod, [0.1, 0.5, 0.6, 0.4])
    np.testing.assert_array_equal(batch.surface, [LAND, OCEAN, OCEAN, LAND])
    np.testing.assert_allclose(batch.sigma, [0.01, 0.1, 0.2, np.nan], equal_nan=True)
    assert len(batch) == 4
    assert batch.counts["n_sigma_missing"] == 1


def test_inconsistent_recipe_selection_rejected_before_opening():
    terra = resolve_stream("MOD04_L2", "6.1", "dt")
    aqua = resolve_stream("MYD04_L2", "6.1", "dt")
    with pytest.raises(ValueError, match="one product"):
        read_granule("missing", [terra, aqua])
    with pytest.raises(ValueError, match="once per granule"):
        read_granule("missing", [terra, terra])
    assert read_granule("missing", []) == {}


@pytest.mark.real_data
@pytest.mark.parametrize("product", ["MOD04_L2", "MYD04_L2"])
def test_real_modis_smoke_from_configured_profile(product):
    repo = Path(__file__).resolve().parents[1]
    config = load_config(repo / "config/runs/modis_l2_200807.yaml")
    catalog = config.load_catalog()
    directory = granule_dir(config.paths, catalog, product, "6.1", config.period.start)
    paths = sorted(directory.glob("*.hdf"))
    if not paths:
        pytest.skip("no MODIS C6.1 granules under the configured profile's data root")
    pytest.importorskip("pyhdf.SD")
    recipes = [
        resolve_stream(product, "6.1", algorithm, catalog=catalog) for algorithm in ("dt", "db")
    ]
    batches = read_granule(paths[min(20, len(paths) - 1)], recipes, day=config.period.start)
    assert set(batches) == {recipe.stream_id for recipe in recipes}
    for batch in batches.values():
        assert np.all(np.isfinite(batch.t) & np.isfinite(batch.lat) & np.isfinite(batch.lon))
        assert np.all(np.isfinite(batch.aod))
        assert np.all((batch.aod >= -0.05) & (batch.aod <= 5))
        assert batch.counts["n_accepted"] == len(batch)
