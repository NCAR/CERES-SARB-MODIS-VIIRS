"""Planted overpass and cell matchups, midnight edges, and CAM7 regression mode."""

import json
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from sarb_aod.config.schema import RunConfig
from sarb_aod.contract import write_contract_file
from sarb_aod.evaluation import collocate
from sarb_aod.evaluation.collocate import (
    EARTH_RADIUS_KM,
    CollocationOptions,
    build_matchups,
    collocate_grid,
    collocate_grid_daily,
    collocate_l2,
    flag_representativeness,
    great_circle_km,
    load_aeronet_neighbors,
    nearest_cell,
    write_matchups,
)
from sarb_aod.grid.spec import load_grid
from sarb_aod.paths import output_path
from sarb_aod.pipeline import processing_sha256
from sarb_aod.readers import LAND, OCEAN, PixelBatch, PixelCoordinates
from sarb_aod.streams import resolve_stream
from sarb_aod.windows import window_bounds

RECIPE = resolve_stream("MOD04_L2", "6.1", "dt")
DAY = date(2008, 7, 1)


def aero(times, values, *, site="Test_Site", lat=0, lon=0, elevation=0, flags=None):
    size = len(times)
    return pd.DataFrame(
        {
            "AERONET_Site": [site] * size,
            "Site_Latitude(Degrees)": [float(lat)] * size,
            "Site_Longitude(Degrees)": [float(lon)] * size,
            "Site_Elevation(m)": [float(elevation)] * size,
            "time": pd.to_datetime(times, utc=True, format="mixed"),
            "aeronet_level": ["AOD20"] * size,
            "aod_550nm": values,
            "spectral_flag": flags or ["interpolated"] * size,
        }
    )


def batch(values, *, time="2008-07-01T00:00:00Z", lat=None, lon=None, sigma=None, surface=None):
    n = len(values)
    timestamp = pd.Timestamp(time).timestamp()
    return PixelBatch(
        stream_id=RECIPE.stream_id,
        t=np.full(n, timestamp),
        lat=np.asarray(lat if lat is not None else [0] * n, dtype=float),
        lon=np.asarray(lon if lon is not None else [0] * n, dtype=float),
        aod=np.asarray(values, dtype=float),
        sigma=np.asarray(sigma if sigma is not None else [0.1] * n, dtype=float),
        surface=np.asarray(surface if surface is not None else [LAND] * n, dtype=np.uint8),
        rejected_high=PixelCoordinates(np.array([]), np.array([]), np.array([])),
        counts={},
    )


def field(value=0.4, *, sigma=0.1, count=3):
    grid = load_grid()
    return xr.Dataset(
        {
            "aod_weighted_mean": (("lat", "lon"), np.full(grid.shape, value)),
            "aod_uncertainty": (("lat", "lon"), np.full(grid.shape, sigma)),
            "aod_count": (("lat", "lon"), np.full(grid.shape, count)),
            "aod_count_terra_dt": (("lat", "lon"), np.full(grid.shape, count)),
        },
        coords={"lat": grid.lat, "lon": grid.lon},
    )


def test_l2_inclusive_radius_and_time_boundaries_across_midnight():
    boundary = np.rad2deg(25 / EARTH_RADIUS_KM)
    pixels = batch(
        [0.2, 0.4, 0.6, 9],
        lon=[0, boundary, -boundary, boundary + 0.01],
        sigma=[0.1, 0.2, 0.3, 0.4],
        surface=[LAND, OCEAN, LAND, LAND],
    )
    reference = aero(
        [
            "2008-06-30T23:30:00Z",
            "2008-07-01T00:30:00Z",
            "2008-07-01T00:30:00.001Z",
            "2008-06-30T23:29:59.999Z",
        ],
        [0.1, 0.3, 8, 8],
        elevation=1001,
    )
    matched = collocate_l2([pixels], reference, recipes=[RECIPE])
    assert len(matched) == 1
    row = matched.iloc[0]
    assert row.n_satellite == 3 and row.n_aeronet == 2
    assert row.y == pytest.approx(0.4) and row.x == pytest.approx(0.2)
    assert row.y_median == pytest.approx(0.4) and row.y_std == pytest.approx(0.2)
    assert row.sigma_y == pytest.approx(0.2)  # never divided by sqrt(N)
    assert row.sigma_x == pytest.approx(np.sqrt(0.01**2 + 0.02))
    assert row.z == pytest.approx(0.2 / np.sqrt(0.2**2 + 0.01**2 + 0.02))
    assert row.surface == "coastal" and row.coastal_site and row.high_elevation
    assert (row.stream, row.algorithm, row.sensor, row.collection) == (
        "terra_dt",
        "dt",
        "MODIS",
        "6.1",
    )
    assert row.aeronet_level == "AOD20"


def test_adjacent_granules_merge_into_one_overpass_and_later_pass_stays_separate():
    reference = aero(
        ["2008-07-01T00:00Z", "2008-07-01T00:01Z", "2008-07-01T02:00Z", "2008-07-01T02:01Z"],
        [0.1, 0.3, 0.5, 0.7],
    )
    pieces = [
        ("first.hdf", batch([0.1, 0.2])),
        ("second.hdf", batch([0.3, 0.4], time="2008-07-01T00:01Z")),
        ("third.hdf", batch([0.4, 0.6, 0.8], time="2008-07-01T02:00Z")),
    ]
    matched = collocate_l2(pieces, reference, recipes=[RECIPE])
    assert len(matched) == 2
    assert matched.n_satellite.tolist() == [4, 3]
    assert matched.iloc[0].y == pytest.approx(0.25)
    assert matched.iloc[0].time == pd.Timestamp("2008-07-01T00:00:30Z")
    assert matched.iloc[0].source_granules == '["first.hdf", "second.hdf"]'


def test_l2_minimum_counts_and_missing_uncertainty():
    reference = aero(["2008-07-01T00:00Z", "2008-07-01T00:01Z"], [0.1, 0.3])
    assert collocate_l2([batch([0.2, 0.3])], reference, recipes=[RECIPE]).empty
    assert collocate_l2([batch([0.2, 0.3, 0.4])], reference.iloc[:1], recipes=[RECIPE]).empty
    matched = collocate_l2(
        [batch([0.2, 0.3, 0.4], sigma=[0.1, np.nan, 0.1])], reference, recipes=[RECIPE]
    )
    assert len(matched) == 1
    assert np.isnan(matched.iloc[0].sigma_y) and np.isnan(matched.iloc[0].z)


def test_extrapolation_sensitivity_is_retained_and_configurable():
    reference = aero(
        ["2008-07-01T00:00Z", "2008-07-01T00:01Z", "2008-07-01T00:02Z"],
        [0.1, 0.2, 0.9],
        flags=["interpolated", "interpolated", "extrapolated"],
    )
    all_points = collocate_l2([batch([0.3] * 3)], reference, recipes=[RECIPE]).iloc[0]
    assert all_points.x == pytest.approx(0.4)
    assert all_points.x_no_extrapolated == pytest.approx(0.15)
    assert all_points.n_aeronet == 3 and all_points.n_aeronet_no_extrapolated == 2
    assert all_points.aeronet_extrapolated_count == 1
    excluded = collocate_l2(
        [batch([0.3] * 3)], reference, recipes=[RECIPE], include_extrapolated=False
    ).iloc[0]
    assert excluded.x == pytest.approx(0.15) and excluded.n_aeronet == 2
    assert excluded.x_including_extrapolated == pytest.approx(0.4)
    assert excluded.n_aeronet_including_extrapolated == 3


@pytest.mark.parametrize("level", ["l2", "grid"])
def test_extrapolation_only_smoke_candidates_survive_for_full_sensitivity(level, tmp_path):
    from sarb_aod.evaluation.report import build_report

    reference = aero(
        ["2020-09-09T00:00Z", "2020-09-09T00:05Z"],
        [1.0, 1.4],
        lat=40,
        lon=-120,
        flags=["extrapolated"] * 2,
    )
    keywords = dict(
        include_extrapolated=False, retain_sensitivity_candidates=True, recipes=[RECIPE]
    )
    if level == "l2":
        matched = collocate_l2(
            [batch([1.2] * 3, time="2020-09-09T00:00Z", lat=[40] * 3, lon=[-120] * 3)],
            reference,
            **keywords,
        )
    else:
        matched = collocate_grid(field(1.2), reference, start=datetime(2020, 9, 9), **keywords)
    assert len(matched) == 1 and not matched.primary_eligible.iloc[0]
    assert np.isnan(matched.x.iloc[0])
    assert matched.active_sites_in_cell.iloc[0] == 0
    assert matched.x_including_extrapolated.iloc[0] == pytest.approx(1.2)
    cfg = RunConfig.model_validate(
        {
            "name": "smoke",
            "stage": {"products": {"MOD04_L2": ["6.1"]}},
            "paths": {"data_root": str(tmp_path)},
            "period": {"start": "2020-09-09", "end": "2020-09-09"},
            "aeronet": {"include_extrapolated": False},
            "evaluation": {"min_samples": 1},
        }
    )
    result = build_report(matched, cfg, level=level)
    assert result.tables["summary"].N.iloc[0] == 0
    assert result.tables["by_site"].empty
    included = result.tables["sensitivity"].query("spectral_selection == 'include_extrapolated'")
    assert included.N.eq(1).all()
    assert included.MB.eq(0).all() and included.RMSE.eq(0).all()
    excluded = result.tables["sensitivity"].query("spectral_selection == 'exclude_extrapolated'")
    assert excluded.N.eq(0).all()


def test_matchup_writer_preserves_numeric_object_columns_after_empty_windows(tmp_path):
    values = pd.DataFrame(
        {
            "x": pd.Series([0.1, 0.2], dtype=object),
            "y": pd.Series([0.2, 0.4], dtype=object),
            "time": pd.Series(pd.date_range("2008-07-01", periods=2, tz="UTC"), dtype=object),
            "site": ["123", "123"],
        }
    )
    values = pd.concat([pd.DataFrame(columns=values.columns), values], ignore_index=True)
    path = write_matchups(tmp_path / "numeric.nc", values)
    with xr.open_dataset(path) as dataset:
        assert dataset.x.dtype.kind == dataset.y.dtype.kind == "f"
        assert dataset.time.dtype.kind == "M"
        assert str(dataset.site.values[0]) == "123"
        np.testing.assert_allclose(dataset.x, [0.1, 0.2])


def test_fractional_overpass_time_does_not_warn_or_lose_matchup_precision():
    pixels = batch([0.1, 0.2, 0.3])
    pixels.t[:] += np.array([0, 1, 1]) / 3
    reference = aero(["2008-07-01T00:00Z", "2008-07-01T00:01Z"], [0.1, 0.3])
    matched = collocate_l2([pixels], reference, recipes=[RECIPE])
    assert len(matched) == 1
    assert matched.time.iloc[0].nanosecond != 0


def test_dateline_and_polar_geometry_has_no_grid_radius_cut():
    grid = load_grid()
    ilat, ilon = nearest_cell(grid, [90, -90, 0, 0], [-0.1, 180, -180, 359.7])
    np.testing.assert_array_equal(ilat[:2], [191, 0])
    np.testing.assert_array_equal(ilon, [0, 144, 144, 0])
    midpoint = (grid.lat[95] + grid.lat[96]) / 2
    assert nearest_cell(grid, midpoint, 0)[0] == 96
    assert great_circle_km([0], [-179.9], 0, 179.9)[0] < 25
    assert great_circle_km([90], [180], 90, 0)[0] < 1e-8
    reference = aero(["2008-07-01T00:00Z", "2008-07-01T01:00Z"], [0.1, 0.3], lon=0.6)
    # Site is >50 km from the cell center, and therefore fails a 12-km cut.
    matched = collocate_grid(field(), reference, start=datetime(2008, 7, 1))
    assert len(matched) == 1
    assert matched.iloc[0].lon_index == 0


def test_grid_windows_are_half_open_without_double_counting():
    reference = aero(
        [
            "2008-07-01T00:00Z",
            "2008-07-01T02:59:59.999Z",
            "2008-07-01T03:00Z",
            "2008-07-01T05:59:59Z",
        ],
        [0.1, 0.3, 0.5, 0.7],
    )
    first = collocate_grid(field(), reference, start=datetime(2008, 7, 1), recipes=[RECIPE])
    second = collocate_grid(field(), reference, start=datetime(2008, 7, 1, 3), recipes=[RECIPE])
    assert first.iloc[0].x == pytest.approx(0.2)
    assert second.iloc[0].x == pytest.approx(0.6)
    assert first.iloc[0].n_aeronet == second.iloc[0].n_aeronet == 2
    assert first.iloc[0].time == pd.Timestamp("2008-07-01T01:30Z")
    assert first.iloc[0].aod_count_terra_dt == 3
    assert first.iloc[0].sigma_y == pytest.approx(0.1)
    assert first.iloc[0].sensor == "MODIS"
    assert collocate_grid(field(-999), reference, start=datetime(2008, 7, 1)).empty


def test_daily_grid_means_are_independent_before_site_day_pairing():
    fields = [field(-999, count=0) for _ in range(8)]
    fields[0] = field(0.2, count=1)
    fields[4] = field(0.8, count=3)
    # All daily AERONET observations fall outside satellite-bearing windows.
    # None would survive three-hour pairing, but the independent day is valid.
    reference = aero(
        [
            "2008-06-30T23:59:59Z",
            "2008-07-01T03:05:00Z",
            "2008-07-01T03:10:00Z",
            "2008-07-01T21:00:00Z",
            "2008-07-02T00:00:00Z",
        ],
        [8, 0.1, 0.3, 0.8, 9],
    )
    matched = collocate_grid_daily(fields, reference, start=datetime(2008, 7, 1), recipes=[RECIPE])
    assert len(matched) == 1
    row = matched.iloc[0]
    assert row.x == pytest.approx(0.4)
    assert row.y == pytest.approx(0.65)
    assert row.n_aeronet == 3 and row.n_satellite == 4 and row.n_satellite_windows == 2
    assert row.aod_count_terra_dt == 4
    assert row.pairing == "daily" and row.time == pd.Timestamp("2008-07-01T12:00Z")
    assert np.isnan(row.sigma_y) and np.isnan(row.z)
    assert np.isfinite(row.sigma_x)


def test_daily_grid_requires_complete_consistent_fields_and_minimum_daily_aeronet():
    reference = aero(["2008-07-01T01:00Z"], [0.2])
    fields = [field() for _ in range(8)]
    assert collocate_grid_daily(fields, reference, start=datetime(2008, 7, 1)).empty
    with pytest.raises(ValueError, match="all eight"):
        collocate_grid_daily(fields[:7], reference, start=datetime(2008, 7, 1))
    fields[-1] = fields[-1].assign_coords(lon=fields[-1].lon + 0.01)
    with pytest.raises(ValueError, match="identical coordinates"):
        collocate_grid_daily(fields, reference, start=datetime(2008, 7, 1))


def test_cam7_averages_bands_before_conversion_and_has_no_count_minimum():
    reference = aero(
        ["2008-07-01T00:05Z", "2008-07-01T00:10Z", "2008-07-01T01:05Z"], [0.1, 1.0, 0.2]
    )
    reference["AOD_500nm"] = [0.1, 1.0, 0.2]
    reference["440-675_Angstrom_Exponent"] = [0.2, 2.0, 1.0]
    matched = collocate_grid(field(), reference, start=datetime(2008, 7, 1), pairing="cam7")
    assert len(matched) == 2
    expected = 0.55 * (550 / 500) ** -1.1
    assert matched.iloc[0].x == pytest.approx(expected)
    assert matched.iloc[0].x != pytest.approx(np.mean([0.1 * 1.1**-0.2, 1.0 * 1.1**-2]))
    assert matched.iloc[1].n_aeronet == 1
    assert matched.iloc[0].time == pd.Timestamp("2008-07-01T00:00Z")


def test_cam7_band_means_can_have_different_missing_patterns():
    reference = aero(["2008-07-01T00:05Z", "2008-07-01T00:10Z"], [np.nan, np.nan])
    reference["AOD_500nm"] = [0.4, np.nan]
    reference["440-675_Angstrom_Exponent"] = [np.nan, 1.0]
    matched = collocate_grid(field(), reference, start=datetime(2008, 7, 1), pairing="cam7")
    assert len(matched) == 1
    assert matched.iloc[0].x == pytest.approx(0.4 / 1.1)
    assert matched.iloc[0].n_aod500 == matched.iloc[0].n_angstrom == 1


def test_shared_cells_and_coastal_majority_flags():
    times = ["2008-07-01T00:00Z", "2008-07-01T01:00Z"]
    references = pd.concat(
        [
            aero(times, [0.1, 0.3], site="A", elevation=1000),
            aero(times, [0.2, 0.4], site="B", elevation=1001),
        ],
        ignore_index=True,
    )
    matched = collocate_grid(
        field(), references, start=datetime(2008, 7, 1), coastal_sites={"A": True}
    )
    assert matched.multiple_sites.all() and matched.active_sites_in_cell.eq(2).all()
    assert matched.high_elevation.tolist() == [False, True]
    assert matched.coastal_known.tolist() == [True, False]
    assert matched.coastal_site.tolist() == [True, False]
    planted = pd.concat([matched.iloc[[0]]] * 3, ignore_index=True)
    planted["level"] = "l2"
    planted["surface"] = ["coastal", "land", "coastal"]
    assert flag_representativeness(planted).coastal_site.all()


def test_neighbor_loading_requests_both_adjacent_utc_dates(tmp_path):
    cfg = make_config(tmp_path)
    called = []

    def load(config, day):
        called.append(day)
        return aero([f"{day}T23:45Z"], [0.2])

    loaded = load_aeronet_neighbors(cfg, DAY, loader=load)
    assert called == [date(2008, 6, 30), DAY, date(2008, 7, 2)]
    assert len(loaded) == 3


def test_netcdf_roundtrip_retains_times_numbers_flags_and_sources(tmp_path):
    from sarb_aod.contract import code_provenance

    reference = aero(["2008-07-01T00:00Z", "2008-07-01T00:01Z"], [0.1, 0.3])
    matched = collocate_l2([("source.hdf", batch([0.2] * 3))], reference, recipes=[RECIPE])
    path = write_matchups(tmp_path / "matchups.nc", matched, attributes={"config": {"test": True}})
    with xr.open_dataset(path) as saved:
        assert saved.sizes["matchup"] == 1
        assert saved["time"].values[0] == np.datetime64("2008-07-01T00:00:00")
        assert saved["x"].values[0] == pytest.approx(0.2)
        assert saved["coastal_site"].values[0] == 0
        assert saved["source_granules"].values[0] == '["source.hdf"]'
        assert saved.attrs["time_zone"] == "UTC"
        for key, value in code_provenance().items():
            assert saved.attrs[key] == value
        assert json.loads(saved.attrs["compute_platform"])["os_name"]
        assert json.loads(saved.attrs["python_environment"])["packages"]
    empty = collocate_l2([], reference, recipes=[RECIPE])
    write_matchups(tmp_path / "empty.nc", empty)
    with xr.open_dataset(tmp_path / "empty.nc") as saved:
        assert saved.sizes["matchup"] == 0


def make_config(tmp_path: Path) -> RunConfig:
    return RunConfig.model_validate(
        {
            "paths": {"data_root": str(tmp_path)},
            "period": {"start": DAY, "end": DAY},
            "stage": {"products": {"MOD04_L2": ["6.1"]}},
            "grid": {"streams": {"terra_dt": {"product": "MOD04_L2", "collection": "6.1"}}},
        }
    )


def test_l2_driver_deduplicates_edge_granules_and_records_provenance(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    calls = []
    reference = aero(["2008-07-01T00:00Z", "2008-07-01T00:01Z"], [0.1, 0.3])

    def cached(config, day):
        calls.append(day)
        return reference if day == DAY else reference.iloc[:0]

    monkeypatch.setattr(collocate, "read_cached_day", cached)
    path = tmp_path / "source.hdf"
    monkeypatch.setattr(
        collocate, "discover_granules", lambda *_: ([(path, [RECIPE]), (path, [RECIPE])], [], 1)
    )
    reads = []

    def read(path, recipes, **kwargs):
        reads.append(path)
        return {"terra_dt": batch([0.2] * 3)}

    monkeypatch.setattr(collocate, "read_granule", read)
    result = build_matchups(cfg, level="l2", output_dir=tmp_path / "evaluation")
    assert len(result.frame) == 1 and result.frame.iloc[0].n_satellite == 3
    assert len(reads) == 1 and len(calls) == 3
    assert result.path.is_file()
    with xr.open_dataset(result.path) as saved:
        assert "config_sha256" in saved.attrs and "aeronet_cache_files" in saved.attrs
        assert json.loads(saved.attrs["config_files"]) == []  # programmatic test configuration
        assert json.loads(saved.attrs["config"]) == cfg.model_dump(mode="json")


@pytest.mark.parametrize("pairing", ["default", "daily"])
def test_grid_driver_needs_only_period_days_and_rejects_stale_processing(
    tmp_path, monkeypatch, pairing
):
    cfg = make_config(tmp_path)
    reference = aero(["2008-07-01T00:00Z", "2008-07-01T00:01Z"], [0.1, 0.3])
    calls = []

    def cached(config, day):
        calls.append(day)
        assert day == DAY  # Grid intervals never require adjacent-day observations.
        return reference

    monkeypatch.setattr(collocate, "read_cached_day", cached)
    grid = load_grid()
    for index in range(8):
        start = window_bounds(DAY, index)[0]
        write_contract_file(
            output_path(cfg, start),
            window_start=start,
            aod_weighted_mean=np.full(grid.shape, 0.4),
            aod_uncertainty=np.full(grid.shape, 0.1),
            aod_count=np.full(grid.shape, 3),
            global_attrs={"processing_sha256": processing_sha256(cfg)},
        )
    result = build_matchups(cfg, level="grid", pairing=pairing, output_dir=tmp_path / "evaluation")
    assert len(result.frame) == 1 and calls == [DAY]
    if pairing == "daily":
        assert result.frame.n_satellite_windows.iloc[0] == 8
        assert result.frame.n_satellite.iloc[0] == 24
        assert result.frame.sigma_y.isna().all()
        assert "_daily_" in result.path.name
    import netCDF4

    with netCDF4.Dataset(output_path(cfg, datetime(2008, 7, 1)), "a") as dataset:
        dataset.processing_sha256 = "stale"
    with pytest.raises(ValueError, match="processing provenance"):
        build_matchups(cfg, level="grid", pairing=pairing, output_dir=tmp_path / "stale_evaluation")
    assert not (tmp_path / "stale_evaluation").exists()
    with netCDF4.Dataset(output_path(cfg, datetime(2008, 7, 1)), "a") as dataset:
        dataset.processing_sha256 = processing_sha256(cfg)
        dataset["time"][...] = float(dataset["time"][...]) + 3
        dataset["time_bnds"][:] = dataset["time_bnds"][:] + 3
    with pytest.raises(ValueError, match="time differs from the requested window"):
        build_matchups(cfg, level="grid", pairing=pairing, output_dir=tmp_path / "wrong_window")


def test_l2_driver_rejects_day_with_only_an_edge_granule(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    cfg = cfg.model_copy(update={"grid": cfg.grid.model_copy(update={"edge_granules": True})})
    reference = aero(["2008-07-01T00:00Z", "2008-07-01T00:01Z"], [0.1, 0.3])
    monkeypatch.setattr(collocate, "read_cached_day", lambda *_: reference)
    monkeypatch.setattr(
        collocate,
        "discover_granules",
        lambda *_: ([(tmp_path / "edge.hdf", [RECIPE])], [], 0),
    )
    with pytest.raises(FileNotFoundError, match="No primary L2 granules"):
        build_matchups(cfg, level="l2", output_dir=tmp_path / "evaluation")
    assert not (tmp_path / "evaluation").exists()


@pytest.mark.parametrize(
    "kwargs", [{"radius_km": 0}, {"time_minutes": -1}, {"min_pixels": 0}, {"min_aeronet": True}]
)
def test_invalid_options(kwargs):
    with pytest.raises(ValueError):
        CollocationOptions(**kwargs)
