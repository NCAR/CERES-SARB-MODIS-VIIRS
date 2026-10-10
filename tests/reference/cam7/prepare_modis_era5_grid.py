#!/usr/bin/env python
"""Prepare staged MODIS L2 AOD on the ERA5 f09 lat/lon grid.

Vendored unchanged (formatting only) from NCAR/CERES-SARB-CAM7
commit cd1b5d45dbf3ddfb737c3a7153b67cdf2adef254, scripts/prepare_modis_era5_grid.py.
Test oracle only; its optional DAVINCI dependency falls back to NumPy.

This is the MODIS-only July-2008 preparation path: Terra MOD04_L2 and Aqua
MYD04_L2 granules are filtered, binned into 3-hour windows on the ERA5 f09 grid,
and written as contract-like netCDF files for SP1/SP2 development.
"""

from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import netCDF4 as nc
import numpy as np
from pyhdf.SD import SD, SDC

try:
    from davinci_monet.pairing.grid_binning import bin_swath_to_grid, edges_from_centers
except Exception:  # pragma: no cover - only used if DAVINCI import is broken.
    bin_swath_to_grid = None
    edges_from_centers = None


ROOT = Path("/glade/work/fillmore/Data/CERES-SARB-CAM7")
MODIS_ROOT = ROOT / "MODIS_VIIRS_L2"
OUT_ROOT = ROOT / "MODIS_AOD_ERA5_GRID"
ERA5_SAMPLE = Path(
    "/glade/campaign/cgd/amp/wchapman/Nudging_ERA5/fv_L32_ncl/"
    "ERA5_fv09.cam2.i.2008-07-01-00000.nc"
)

TAI93_EPOCH = datetime(1993, 1, 1)
TIME_EPOCH = datetime(2000, 1, 1)
FILL_FLOAT = np.float32(-999.0)
FILL_COUNT = np.int32(0)


@dataclass(frozen=True)
class Grid:
    lat: np.ndarray
    lon: np.ndarray
    lat_edges: np.ndarray
    lon_edges: np.ndarray


@dataclass
class Accumulator:
    count: np.ndarray
    total: np.ndarray
    total_sq: np.ndarray
    stream_counts: dict[str, np.ndarray]

    @classmethod
    def empty(
        cls, nwin: int, nlat: int, nlon: int, stream_names: tuple[str, ...] = ()
    ) -> "Accumulator":
        shape = (nwin, nlat, nlon)
        return cls(
            count=np.zeros(shape, dtype=np.int32),
            total=np.zeros(shape, dtype=np.float64),
            total_sq=np.zeros(shape, dtype=np.float64),
            stream_counts={name: np.zeros(shape, dtype=np.int32) for name in stream_names},
        )

    def finalize(
        self, fill: float = float(FILL_FLOAT)
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        mean = np.full(self.total.shape, fill, dtype=np.float32)
        unc = np.full(self.total.shape, fill, dtype=np.float32)
        mask = self.count > 0
        mean64 = np.zeros(self.total.shape, dtype=np.float64)
        mean64[mask] = self.total[mask] / self.count[mask]
        mean[mask] = mean64[mask].astype(np.float32)

        one = self.count == 1
        unc[one] = np.float32(0.0)
        many = self.count > 1
        if np.any(many):
            variance = (self.total_sq[many] - (self.total[many] ** 2) / self.count[many]) / (
                self.count[many] - 1
            )
            variance = np.maximum(variance, 0.0)
            unc[many] = np.sqrt(variance / self.count[many]).astype(np.float32)
        return mean, unc, self.count.copy()


def _edges_from_centers(centers: np.ndarray) -> np.ndarray:
    if edges_from_centers is not None:
        return np.asarray(
            edges_from_centers(np.asarray(centers, dtype=np.float64)), dtype=np.float64
        )
    if centers.size < 2:
        raise ValueError("at least two centers are required")
    edges = np.empty(centers.size + 1, dtype=np.float64)
    edges[1:-1] = 0.5 * (centers[:-1] + centers[1:])
    edges[0] = centers[0] - 0.5 * (centers[1] - centers[0])
    edges[-1] = centers[-1] + 0.5 * (centers[-1] - centers[-2])
    return edges


def read_era5_grid(path: str | Path) -> Grid:
    with nc.Dataset(path) as ds:
        lat = np.asarray(ds["lat"][:], dtype=np.float64)
        lon = np.asarray(ds["lon"][:], dtype=np.float64)
    return Grid(
        lat=lat, lon=lon, lat_edges=_edges_from_centers(lat), lon_edges=_edges_from_centers(lon)
    )


def day_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def window_starts(times_per_day: int) -> np.ndarray:
    return np.arange(times_per_day, dtype=np.float64) * (86400.0 / times_per_day)


def git_commit() -> str:
    here = Path(__file__).resolve().parents[1]
    try:
        return (
            subprocess.check_output(
                ["git", "-C", str(here), "rev-parse", "HEAD"], stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def _read_sds(hdf: SD, name: str) -> tuple[np.ndarray, dict]:
    sds = hdf.select(name)
    data = np.asarray(sds[:])
    attrs = dict(sds.attributes())
    sds.endaccess()
    return data, attrs


def _scaled_sds(hdf: SD, name: str) -> np.ndarray:
    data, attrs = _read_sds(hdf, name)
    fill = attrs.get("_FillValue")
    valid_range = attrs.get("valid_range")
    scale = float(attrs.get("scale_factor", 1.0))
    offset = float(attrs.get("add_offset", 0.0))
    out = data.astype(np.float64)
    mask = np.isfinite(out)
    if fill is not None:
        mask &= out != float(fill)
    if valid_range is not None:
        mask &= out >= float(valid_range[0])
        mask &= out <= float(valid_range[1])
    out = out * scale + offset
    out[~mask] = np.nan
    return out


@dataclass(frozen=True)
class ObservationStream:
    source: str
    lat: np.ndarray
    lon: np.ndarray
    time_seconds: np.ndarray
    aod: np.ndarray
    qa: np.ndarray


def _select_550(data: np.ndarray) -> np.ndarray:
    if data.ndim == 3:
        return data[1, :, :]
    if data.ndim == 2:
        return data
    raise ValueError(f"cannot select 550 nm from array with shape {data.shape}")


def _platform_from_path(path: str | Path) -> str:
    name = Path(path).name
    if name.startswith("MOD04_L2"):
        return "terra"
    if name.startswith("MYD04_L2"):
        return "aqua"
    raise ValueError(f"cannot infer MODIS platform from {path}")


def _dark_target_aod(hdf: SD) -> np.ndarray:
    land = _select_550(_scaled_sds(hdf, "Corrected_Optical_Depth_Land"))
    ocean = _select_550(_scaled_sds(hdf, "Effective_Optical_Depth_Best_Ocean"))
    return np.where(np.isfinite(land), land, ocean)


def read_modis_observation_streams(path: str | Path) -> list[ObservationStream]:
    platform = _platform_from_path(path)
    hdf = SD(str(path), SDC.READ)
    try:
        lat = _scaled_sds(hdf, "Latitude")
        lon = _scaled_sds(hdf, "Longitude")
        time_seconds = _scaled_sds(hdf, "Scan_Start_Time")
        dt_aod = _dark_target_aod(hdf)
        dt_qa = _scaled_sds(hdf, "Land_Ocean_Quality_Flag")
        db_aod = _scaled_sds(hdf, "Deep_Blue_Aerosol_Optical_Depth_550_Land")
        db_qa = _scaled_sds(hdf, "Deep_Blue_Aerosol_Optical_Depth_550_Land_QA_Flag")
    finally:
        hdf.end()
    return [
        ObservationStream(
            source=f"{platform}_dt",
            lat=lat.ravel(),
            lon=lon.ravel(),
            time_seconds=time_seconds.ravel(),
            aod=dt_aod.ravel(),
            qa=dt_qa.ravel(),
        ),
        ObservationStream(
            source=f"{platform}_db",
            lat=lat.ravel(),
            lon=lon.ravel(),
            time_seconds=time_seconds.ravel(),
            aod=db_aod.ravel(),
            qa=db_qa.ravel(),
        ),
    ]


def _normalize_lon_for_grid(lon: np.ndarray, lon_edges: np.ndarray) -> np.ndarray:
    out = np.asarray(lon, dtype=np.float64).copy()
    out = np.where(out < lon_edges[0], out + 360.0, out)
    out = np.where(out > lon_edges[-1], out - 360.0, out)
    return out


def _bin_values(
    *,
    grid: Grid,
    window_edges: np.ndarray,
    time_values: np.ndarray,
    lon_values: np.ndarray,
    lat_values: np.ndarray,
    data_values: np.ndarray,
    count_out: np.ndarray,
    sum_out: np.ndarray,
) -> None:
    if bin_swath_to_grid is None:
        iw = np.searchsorted(window_edges, time_values, side="right") - 1
        ilat = np.searchsorted(grid.lat_edges, lat_values, side="right") - 1
        ilon = np.searchsorted(grid.lon_edges, lon_values, side="right") - 1
        iw = np.clip(iw, 0, count_out.shape[0] - 1)
        ilat = np.clip(ilat, 0, count_out.shape[1] - 1)
        ilon = np.clip(ilon, 0, count_out.shape[2] - 1)
        np.add.at(count_out, (iw, ilat, ilon), 1)
        np.add.at(sum_out, (iw, ilat, ilon), data_values)
        return

    count_grid = np.zeros(
        (count_out.shape[0], count_out.shape[2], count_out.shape[1]), dtype=np.int32
    )
    sum_grid = np.zeros((sum_out.shape[0], sum_out.shape[2], sum_out.shape[1]), dtype=np.float64)
    bin_swath_to_grid(
        np.asarray(window_edges, dtype=np.float64),
        np.asarray(grid.lon_edges, dtype=np.float64),
        np.asarray(grid.lat_edges, dtype=np.float64),
        np.asarray(time_values, dtype=np.float64),
        np.asarray(lon_values, dtype=np.float64),
        np.asarray(lat_values, dtype=np.float64),
        np.asarray(data_values, dtype=np.float64),
        count_grid,
        sum_grid,
    )
    count_out += count_grid.transpose(0, 2, 1)
    sum_out += sum_grid.transpose(0, 2, 1)


def accumulate_pixels(
    acc: Accumulator,
    grid: Grid,
    *,
    window_edges: np.ndarray,
    lat: np.ndarray,
    lon: np.ndarray,
    time_seconds: np.ndarray,
    aod: np.ndarray,
    qa: np.ndarray,
    qa_min: int,
    aod_min: float,
    aod_max: float,
    source: str | None = None,
) -> None:
    lon = _normalize_lon_for_grid(lon, grid.lon_edges)
    mask = (
        np.isfinite(lat)
        & np.isfinite(lon)
        & np.isfinite(time_seconds)
        & np.isfinite(aod)
        & np.isfinite(qa)
        & (qa >= qa_min)
        & (aod >= aod_min)
        & (aod <= aod_max)
        & (time_seconds >= window_edges[0])
        & (time_seconds < window_edges[-1])
        & (lat >= grid.lat_edges[0])
        & (lat <= grid.lat_edges[-1])
        & (lon >= grid.lon_edges[0])
        & (lon <= grid.lon_edges[-1])
    )
    if not np.any(mask):
        return

    lat_values = lat[mask]
    lon_values = lon[mask]
    time_values = time_seconds[mask]
    aod_values = aod[mask]

    before = acc.count.copy() if source is not None else None
    _bin_values(
        grid=grid,
        window_edges=window_edges,
        time_values=time_values,
        lon_values=lon_values,
        lat_values=lat_values,
        data_values=aod_values,
        count_out=acc.count,
        sum_out=acc.total,
    )
    dummy_count = np.zeros_like(acc.count)
    _bin_values(
        grid=grid,
        window_edges=window_edges,
        time_values=time_values,
        lon_values=lon_values,
        lat_values=lat_values,
        data_values=aod_values * aod_values,
        count_out=dummy_count,
        sum_out=acc.total_sq,
    )
    if source is not None:
        if source not in acc.stream_counts:
            acc.stream_counts[source] = np.zeros_like(acc.count)
        acc.stream_counts[source] += acc.count - before


def contract_filename(day: date, seconds_of_day: int) -> str:
    return f"MODIS_AOD_3hr_ERA5_f09.{day:%Y%m%d}.{seconds_of_day:05d}.nc"


def _hours_since_epoch(dt: datetime) -> float:
    return (dt - TIME_EPOCH).total_seconds() / 3600.0


def _lat_bnds(grid: Grid) -> np.ndarray:
    return np.stack([grid.lat_edges[:-1], grid.lat_edges[1:]], axis=1)


def _lon_bnds(grid: Grid) -> np.ndarray:
    return np.stack([grid.lon_edges[:-1], grid.lon_edges[1:]], axis=1)


def write_outputs(
    out_dir: str | Path,
    grid: Grid,
    *,
    day: date,
    window_starts: np.ndarray,
    mean: np.ndarray,
    uncertainty: np.ndarray,
    count: np.ndarray,
    stream_counts: dict[str, np.ndarray] | None = None,
    source_files: int,
    source_granules: str,
) -> list[Path]:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    lat_bnds = _lat_bnds(grid)
    lon_bnds = _lon_bnds(grid)
    if len(window_starts) > 1:
        window_len = int(round(window_starts[1] - window_starts[0]))
    else:
        window_len = 10800

    for iw, start_sec in enumerate(window_starts.astype(int)):
        path = out_path / contract_filename(day, int(start_sec))
        with nc.Dataset(path, "w") as ds:
            ds.createDimension("lat", grid.lat.size)
            ds.createDimension("lon", grid.lon.size)
            ds.createDimension("nbnds", 2)
            latv = ds.createVariable("lat", "f8", ("lat",))
            lonv = ds.createVariable("lon", "f8", ("lon",))
            latbv = ds.createVariable("lat_bnds", "f8", ("lat", "nbnds"))
            lonbv = ds.createVariable("lon_bnds", "f8", ("lon", "nbnds"))
            timev = ds.createVariable("time", "f8")
            timebv = ds.createVariable("time_bnds", "f8", ("nbnds",))
            meanv = ds.createVariable(
                "aod_weighted_mean", "f4", ("lat", "lon"), fill_value=FILL_FLOAT
            )
            modisv = ds.createVariable(
                "aod_weighted_mean_modis", "f4", ("lat", "lon"), fill_value=FILL_FLOAT
            )
            uncv = ds.createVariable("aod_uncertainty", "f4", ("lat", "lon"), fill_value=FILL_FLOAT)
            countv = ds.createVariable("aod_count", "i4", ("lat", "lon"), fill_value=FILL_COUNT)
            stream_vars = {}
            for stream_name in ("terra_dt", "terra_db", "aqua_dt", "aqua_db"):
                stream_vars[stream_name] = ds.createVariable(
                    f"aod_count_{stream_name}", "i4", ("lat", "lon"), fill_value=FILL_COUNT
                )

            latv[:] = grid.lat
            lonv[:] = grid.lon
            latbv[:, :] = lat_bnds
            lonbv[:, :] = lon_bnds
            latv.units = "degrees_north"
            lonv.units = "degrees_east"
            latv.bounds = "lat_bnds"
            lonv.bounds = "lon_bnds"

            start_dt = datetime(day.year, day.month, day.day) + timedelta(seconds=int(start_sec))
            center_dt = start_dt + timedelta(seconds=window_len / 2)
            timev[...] = _hours_since_epoch(center_dt)
            timebv[:] = [
                _hours_since_epoch(start_dt),
                _hours_since_epoch(start_dt + timedelta(seconds=window_len)),
            ]
            timev.units = "hours since 2000-01-01 00:00:00 UTC"
            timev.calendar = "gregorian"
            timev.bounds = "time_bnds"

            meanv[:, :] = mean[iw]
            modisv[:, :] = mean[iw]
            uncv[:, :] = uncertainty[iw]
            countv[:, :] = count[iw]
            if stream_counts is not None:
                for stream_name, var in stream_vars.items():
                    var[:, :] = stream_counts.get(stream_name, np.zeros_like(count))[iw]
            else:
                for var in stream_vars.values():
                    var[:, :] = np.zeros_like(count[iw])
            for var in (meanv, modisv, uncv):
                var.units = "1"
            countv.long_name = "number of valid MODIS L2 pixels in grid cell and window"
            stream_vars["terra_dt"].long_name = "number of valid Terra Dark Target observations"
            stream_vars["terra_db"].long_name = "number of valid Terra Deep Blue observations"
            stream_vars["aqua_dt"].long_name = "number of valid Aqua Dark Target observations"
            stream_vars["aqua_db"].long_name = "number of valid Aqua Deep Blue observations"
            meanv.standard_name = "atmosphere_optical_thickness_due_to_ambient_aerosol_particles"
            meanv.long_name = (
                "Count-weighted MODIS Terra+Aqua Dark Target and Deep Blue AOD at 550 nm"
            )
            modisv.long_name = (
                "Count-weighted MODIS Terra+Aqua Dark Target and Deep Blue AOD at 550 nm"
            )
            uncv.long_name = "standard error of MODIS AOD mean"

            ds.Conventions = "CF-1.10"
            ds.title = "MODIS Terra+Aqua AOD on ERA5 f09 grid"
            ds.institution = "NCAR"
            ds.source = "MODIS MOD04_L2/MYD04_L2 C6.1 Level-2 aerosol"
            ds.source_granules = source_granules
            ds.source_file_count = source_files
            ds.history = f"created by {Path(__file__).name}"
            ds.grid = "ERA5_fv09 lat/lon"
            ds.aod_wavelength_nm = 550
            ds.qc_thresholds = (
                "DT: Land_Ocean_Quality_Flag >= 1; "
                "DB: Deep_Blue_Aerosol_Optical_Depth_550_Land_QA_Flag >= 1; "
                "0 <= AOD <= 5"
            )
            ds.merge_method = (
                "count-weighted mean of valid Terra/Aqua Dark Target and Deep Blue observations "
                "per 3-hour grid cell"
            )
            ds.merge_version = "modis-era5-grid-v2-four-stream-count-weighted"
            ds.bias_correction_method = "none (MODIS-only July 2008)"
            ds.bias_correction_version = "none"
            ds.date_created = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            ds.git_commit = git_commit()
        paths.append(path)
    return paths


def files_for_day(modis_root: Path, day: date) -> list[Path]:
    ymd = day.strftime("%Y%m%d")
    files: list[Path] = []
    for dataset in ("MODIS_TERRA_AEROSOL", "MODIS_AQUA_AEROSOL"):
        files.extend(sorted((modis_root / dataset / ymd).glob("*.hdf")))
    return files


def prepare_day(
    *,
    day: date,
    modis_root: Path,
    out_root: Path,
    grid: Grid,
    times_per_day: int,
    qa_min: int,
    aod_min: float,
    aod_max: float,
    overwrite: bool,
) -> list[Path]:
    starts = window_starts(times_per_day)
    out_dir = out_root / f"{day:%Y%m%d}"
    expected = [out_dir / contract_filename(day, int(s)) for s in starts]
    if not overwrite and all(p.exists() for p in expected):
        return expected

    stream_names = ("terra_dt", "terra_db", "aqua_dt", "aqua_db")
    acc = Accumulator.empty(
        nwin=starts.size, nlat=grid.lat.size, nlon=grid.lon.size, stream_names=stream_names
    )
    day0 = datetime(day.year, day.month, day.day)
    tai93_day0 = (day0 - TAI93_EPOCH).total_seconds()
    edges = np.concatenate([starts, [86400.0]])
    files = files_for_day(modis_root, day)
    if not files:
        raise FileNotFoundError(f"no MODIS HDF files for {day:%Y%m%d} under {modis_root}")

    for idx, path in enumerate(files, start=1):
        for stream in read_modis_observation_streams(path):
            accumulate_pixels(
                acc,
                grid,
                window_edges=edges,
                lat=stream.lat,
                lon=stream.lon,
                time_seconds=stream.time_seconds - tai93_day0,
                aod=stream.aod,
                qa=stream.qa,
                qa_min=qa_min,
                aod_min=aod_min,
                aod_max=aod_max,
                source=stream.source,
            )
        if idx % 100 == 0 or idx == len(files):
            print(f"{day:%Y%m%d}: processed {idx}/{len(files)} MODIS granules", flush=True)

    mean, uncertainty, count = acc.finalize(fill=float(FILL_FLOAT))
    paths = write_outputs(
        out_dir,
        grid,
        day=day,
        window_starts=starts,
        mean=mean,
        uncertainty=uncertainty,
        count=count,
        stream_counts=acc.stream_counts,
        source_files=len(files),
        source_granules="MOD04_L2/MYD04_L2",
    )
    print(
        f"{day:%Y%m%d}: wrote {len(paths)} files; "
        f"valid_cells={int((count > 0).sum())} valid_pixels={int(count.sum())}",
        flush=True,
    )
    return paths


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", default="2008-07-01")
    p.add_argument("--end", default="2008-07-31")
    p.add_argument("--modis-root", type=Path, default=MODIS_ROOT)
    p.add_argument("--era5-sample", type=Path, default=ERA5_SAMPLE)
    p.add_argument("--out-root", type=Path, default=OUT_ROOT)
    p.add_argument("--times-per-day", type=int, default=8)
    p.add_argument("--qa-min", type=int, default=1)
    p.add_argument("--aod-min", type=float, default=0.0)
    p.add_argument("--aod-max", type=float, default=5.0)
    p.add_argument("--overwrite", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    grid = read_era5_grid(args.era5_sample)
    all_paths: list[Path] = []
    for day in day_range(start, end):
        all_paths.extend(
            prepare_day(
                day=day,
                modis_root=args.modis_root,
                out_root=args.out_root,
                grid=grid,
                times_per_day=args.times_per_day,
                qa_min=args.qa_min,
                aod_min=args.aod_min,
                aod_max=args.aod_max,
                overwrite=args.overwrite,
            )
        )
    print(f"prepared {len(all_paths)} MODIS ERA5-grid files under {args.out_root}", flush=True)


if __name__ == "__main__":
    main()
