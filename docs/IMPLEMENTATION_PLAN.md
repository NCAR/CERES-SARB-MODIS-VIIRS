# Implementation Plan: MODIS/VIIRS L2 → CAM7 AOD constraint files

*Written 2026-10-10. Decisions referenced here are recorded in `AGENTS.md` ("Design
decisions"). Work on `main`; each task lands with its tests.*

## Goal

Turn staged L2 aerosol granules into CAM7-ready f09, 3-hourly, 550 nm AOD files: per-source
streams, retrieval-team QC, numba gridding, configurable weighting (GLS with error
correlation by default, plain count weighting as an option), and the CAM7 obs-file contract.
MODIS-only, VIIRS-only, or mixed is a configuration choice.

Out of scope here (deferred): spatial/temporal denoising, VIIRS→MODIS bias correction.

## Status

| Phase | Content | State |
|---|---|---|
| 0 | Package, config schema/profiles, catalog, `stage`/`audit`, env | **Done.** July 2008 MODIS C6.1 and Sept 2020 MODIS+VIIRS staging |
| 1 | MODIS C6.1 → CAM7 files (July 2008) | Planned (this document) |
| 2 | VIIRS readers, MODIS+VIIRS mixing, C7 (Sept 2020) | Planned |
| 3 | AERONET validation; choose ρ and other defaults | Planned |
| 4 | Production profile (AMI), multi-year runs | Needs AMI details |

Test data: July 2008 MODIS C6.1 (9,518 granules, 23.7 GiB); Sept 2020 MODIS C6.1 + VIIRS
DB v2 (SNPP, NOAA-20), DB v2.1 (NOAA-20), DT v2.1 (SNPP, NOAA-20) (29,856 granules, 213 GiB).
MODIS C7 aerosol isn't published yet; the catalog stages it automatically once it is.

---

## Architecture

```text
sarb_aod/
  config/schema.py      + StreamConfig, QCConfig (presets), GridConfig, WeightingConfig, OutputConfig
  data/catalog.yaml     + per-product algorithm streams and variable maps (C6.1, C7, VIIRS)
  streams.py            Stream registry: product × algorithm (DT/DB) → reader recipe
  readers/
    base.py             PixelBatch (lat, lon, time, aod, sigma, surface, stream id)
    modis_hdf4.py       MOD04/MYD04 C6.1 (pyhdf)
    netcdf.py           C7 and VIIRS AERDB/AERDT (netCDF4, group-aware variable paths)
  qc.py                 per-stream, per-surface QA and range masks
  uncertainty.py        per-pixel sigma: retrieval-provided or expected-error envelope
  grid/
    spec.py             f09 (exact CAM centers), or grid read from a file; cell edges
    kernels.py          numba accumulation (ported from DAVINCI grid_binning.py, extended)
    combine.py          weighting: gls (default) | count
  windows.py            3-hourly windows, CF center time, TAI93 conversion
  contract.py           CAM7 writer + validator (ported from CAM7 sp1_aod_pipeline/contract.py)
  pipeline.py           per-day driver, day-level parallelism, resumable
  cli.py                + grid, validate, summarize
```

Data flow for one day:

```text
granules(day ± edge granules) → reader → PixelBatch per stream
  → QC mask → sigma → numba accumulate into acc[window, stream, lat, lon]
  → combine(acc, weighting) → mean, sigma, count, per-stream diagnostics
  → 8 contract files (atomic write) → validate
```

---

## Phase 1: MODIS C6.1 → CAM7 files (July 2008)

### Task 1.1: Streams and the catalog variable map

A **stream** is one platform × algorithm source: `terra_dt`, `terra_db`, `aqua_dt`, `aqua_db`
(later `snpp_db`, `noaa20_dt`, …). Each pixel also carries a surface type (land/ocean) for QC
and the expected-error envelope.

Extend `catalog.yaml` so each product collection lists its streams and variables. MODIS C6.1:

| Stream | AOD 550 | QA | Sigma | Surface |
|---|---|---|---|---|
| DT land | `Corrected_Optical_Depth_Land[1]` | `Land_Ocean_Quality_Flag` | EE envelope | `Land_sea_Flag` |
| DT ocean | `Effective_Optical_Depth_Average_Ocean[1]` | `Land_Ocean_Quality_Flag` | EE envelope | `Land_sea_Flag` |
| DB land | `Deep_Blue_Aerosol_Optical_Depth_550_Land` | `Deep_Blue_..._550_Land_QA_Flag` | `Deep_Blue_..._550_Land_Estimated_Uncertainty` | land |

Geolocation and time: `Latitude`, `Longitude`, `Scan_Start_Time` (TAI93 seconds). Scaling
comes from each SDS's `scale_factor`/`add_offset`/`_FillValue`/`valid_range`.

DT land and ocean together form one stream per platform (`terra_dt`), as in CAM7, so the
per-stream counts in the output stay the four the CAM7 files report.

The `cam7_v2` preset swaps DT ocean to `Effective_Optical_Depth_Best_Ocean`.

**Tests:** catalog validation; a stream registry lookup for every catalog entry; the
`cam7_v2` override.

### Task 1.2: QC configuration with presets

```yaml
qc:
  preset: retrieval_team          # or cam7_v2
  aod_min: -0.05                  # keep small negatives (see below)
  aod_max: 5.0
  overrides: {dt_ocean: {qa_min: 1}}   # optional per stream/surface
```

| Preset | DT land | DT ocean | DB (land, water) |
|---|---|---|---|
| `retrieval_team` (default) | QA = 3 | QA ≥ 2 | QA ≥ 2 |
| `cam7_v2` | QA ≥ 1 | QA ≥ 1 | QA ≥ 1, and AOD ≥ 0 |

**Negative AOD.** The retrievals allow small negative AOD (DT ocean `valid_range` −0.1–5.0)
so that averages over clean scenes aren't biased high. CAM7 dropped AOD < 0, which biases
clean oceans upward, the Southern Ocean included. Default: keep values ≥ `aod_min` in the
average, and clip only the final cell mean at 0 for CAM. This is a config option, and Phase 3
checks it against AERONET.

**The AOD > 5 cap** matters for the Sept 2020 smoke. `aod_max` defaults to the products'
valid-range maximum (5.0); cells with dropped high retrievals are counted in a diagnostic
(`aod_count_rejected_high`).

**Tests:** threshold tables for each preset and surface; overrides; negative-AOD handling.

### Task 1.3: Per-pixel uncertainty

- **Retrieval-provided:** DB `Estimated_Uncertainty` (MODIS C6.1, and VIIRS DB v2+
  `Aerosol_Optical_Thickness_550_Expected_Uncertainty_{Land,Ocean}`).
- **Expected-error envelope** for DT, which has no per-pixel AOD uncertainty:
  σ = a + b·|τ|, with coefficients per surface in config. Defaults (to be confirmed against
  the DT C6 references while implementing): land a = 0.05, b = 0.15; ocean taken from the
  asymmetric C6 envelope +(0.04 + 10%), −(0.02 + 10%) as its mean half-width,
  a = 0.03, b = 0.10. CAM7 used ocean (0.03, 0.05), the C5 values.
- `sigma_floor` (default 0.01) guards against tiny or zero σ.
- C7: if the products add per-pixel DT uncertainty, the variable map points to it and the
  envelope becomes the fallback.

**Tests:** envelope arithmetic; DB scaling and fill handling; floor.

### Task 1.4: Readers

- `readers/modis_hdf4.py`: pyhdf; reads only the SDS the selected streams need; applies
  scale, offset, fill, and valid range; returns one `PixelBatch` per stream.
- `readers/netcdf.py`: netCDF4, variable paths with optional group prefix
  (`geophysical_data/...`), for C7 and VIIRS; scaling via netCDF4 auto mask/scale.
- Edge granules: when processing day D, also read the last granule of D−1 and the first of
  D+1. Pixels are assigned to windows by their own `Scan_Start_Time`, never by file date.
- Lon/lat filters: finite and in range; longitudes normalized to the grid's edge convention.

**Tests:** tiny synthetic HDF4 and NetCDF4 granules written in the tests (pyhdf can write
SDS); one real-granule smoke test marked `real_data` (skipped when `~/Data` is absent).

### Task 1.5: Grid and windows

- `grid/spec.py`: f09 exactly as CAM reads it (`lat = linspace(-90, 90, 192)`,
  `lon = arange(288)·1.25`), cell edges and bounds (port CAM7 `f09_grid.py`). `grid.source:
  f09 | file:<path>` to read lat/lon from a NetCDF (CAM7's ERA5-grid script did this).
- `windows.py`: `times_per_day` (default 8), window bounds, CF center time
  (`hours since 2000-01-01`), TAI93 → datetime64 (port CAM7 `records.py`).

**Tests:** f09 centers and bounds against the values in CAM7 `f09_grid.py`; window
assignment at boundaries (00:00, 23:59:59, day edges).

### Task 1.6: Numba kernels (ported from DAVINCI)

Port `bin_swath_to_grid` and `bin_swath_to_grid_uncertainty` from DAVINCI
`davinci_monet/pairing/grid_binning.py` (with attribution), plus one extended kernel that
accumulates, per `[window, stream, lat, lon]`:

| Accumulator | Used by |
|---|---|
| N | both modes |
| Σx, Σx² | count mode (mean, standard error) |
| Σw, Σw·x, Σw·x² (w = 1/σ²) | GLS mode, within-stream scatter |
| Σσ² | GLS mode, mean pixel variance σ̄² for the correlated term |

Memory for July 2008 MODIS: 8 windows × 4 streams × 192 × 288 × 8 arrays × 8 bytes ≈ 113 MB
per day in flight. For mixed MODIS + VIIRS (8 streams), about 230 MB per day.

**Tests:** equivalence with DAVINCI's original kernels on random swaths (compared when
`davinci_monet` is importable, otherwise against stored reference arrays); edge pixels; a
benchmark (≥ 10⁷ pixels/s target, recorded rather than enforced).

### Task 1.7: Weighting (`grid/combine.py`)

```yaml
weighting:
  method: gls            # gls (default) | count
  rho: 0.5               # within-stream error correlation; 0 = pure inverse variance
  birge: true            # inflate sigma when streams disagree beyond their errors
  min_pixels: 1          # per cell and window, across streams
```

**`gls` (default): generalized least squares with a simple error-correlation model.**
For stream *s* in a cell:

- Stream mean: x̄ₛ = Σwx / Σw, where w = 1/σ²
- Stream variance: Vₛ = ρ·σ̄ₛ² + (1 − ρ)/Σwₛ, where σ̄ₛ² = Σσ²/N. With equal σ this reduces
  to σ²(ρ + (1 − ρ)/N).
- Combined: Wₛ = 1/Vₛ, mean = ΣWₛx̄ₛ / ΣWₛ, σ = (ΣWₛ)^−½
- Birge ratio: with S ≥ 2 streams, χ² = ΣWₛ(x̄ₛ − mean)². If χ²/(S − 1) > 1,
  σ ← σ·√(χ²/(S − 1)).

Limits: ρ = 0 gives pure pixel inverse-variance weighting; ρ = 1 weights each stream by
1/σ̄ₛ² regardless of count.

**`count`:** simple count weighting with no uncertainty: mean = Σx / N over all valid pixels
of all streams. `aod_uncertainty` is the standard error of the mean (sample standard
deviation /√N), as in CAM7's `prepare_modis_era5_grid.py`. Single-pixel cells get
`sigma_floor` rather than CAM7's 0, or exactly 0 under the `cam7_v2` preset.

Outputs per cell: mean, sigma, total count, per-stream counts and means, effective sample
size, χ²/ν.

**Tests (pure numpy, no I/O):**
- `count` equals the arithmetic mean; `gls` with equal σ and ρ = 0 equals `count`.
- ρ → 1 saturation: a 100-pixel stream doesn't swamp a 10-pixel stream with equal σ̄.
- Birge inflation fires only when streams disagree.
- `min_pixels` masking; all-empty cells come out as fill.

### Task 1.8: CAM7 contract writer and validator

Port CAM7 `sp1_aod_pipeline/contract.py`. The required variables and attributes stay
identical, so `aod_nudging_obs.F90` reads the files unchanged. Additions (CAM ignores extras):

- `aod_count_<stream>`, `aod_mean_<stream>` for the configured streams
- `aod_weighted_mean_modis` / `_viirs` (sensor subsets; fill if absent)
- `aod_chi2_per_dof`, `aod_count_rejected_high`
- Globals: `weighting_method`, `weighting_rho`, `qc_preset`, the QC table, streams, product
  collections, `config_sha256`, `git_commit`, `source_granule_count`

```yaml
paths:
  output: "{data_root}/CAM7/{stem}/{year}/{month}/{day}/{stem}.{yyyymmdd}.{sssss}.nc"
output:
  stem: MODIS_VIIRS_AOD_3hr_f09
```

CAM namelist to match: `aod_obs_template = '%y/%m/%d/MODIS_VIIRS_AOD_3hr_f09.%y%m%d.%s.nc'`
(confirm on derecho in a short CAM run). Writes are atomic (temp file, then rename).

**Tests:** round trip; the validator rejects a wrong grid, a missing `time`, a wrong fill;
the CAM-required variables are byte-identical in layout to a CAM7 SP1 file (structure
compared via `ncdump -h` text in a fixture).

### Task 1.9: Pipeline and CLI

- `sarb-aod grid -c run.yaml [--day YYYY-MM-DD] [--overwrite] [--workers N]`: per-day
  processing, days in parallel (`ProcessPoolExecutor`; numba kernels compiled once per
  worker, `cache=True`); skips days whose 8 files exist and validate.
- `sarb-aod validate <files|dir>`, `sarb-aod summarize -c run.yaml`: coverage %, mean,
  percentiles, per-stream contributions per day (TSV).
- Run config gains `grid`, `qc`, `weighting`, `output` sections. Staging and gridding share
  the same period and products, and gridding finds granules through the same
  `paths.granule_dir` template.

**Tests:** an end-to-end run on synthetic granules for one day → 8 valid files;
idempotent re-run; a failure on one day doesn't stop the others.

### Task 1.10: July 2008 verification

1. Run `sarb-aod grid` on July 2008 with four configurations: `gls` with ρ ∈ {0, 0.5}, `count`
   with `retrieval_team` QC, and `count` with `cam7_v2` QC (the CAM7 reproduction).
2. Regression: on 3 real granules, the `cam7_v2` + `count` result matches a direct
   re-implementation of CAM7's `prepare_modis_era5_grid.py` accumulation on the f09 grid
   to float32 tolerance.
3. Sanity numbers per configuration (CAM7 VP1 expectations): 3-hour coverage of a few
   percent, monthly mean about 0.1–0.25, values within [−0.05, ~3].
4. Quick-look maps of the monthly mean, the per-configuration differences, and counts. This
   needs `matplotlib` + `cartopy` as an optional `plot` extra in `environment.yml`; or the
   DAVINCI pipeline if you want CAM7's plotting rule.

---

## Phase 2: VIIRS, mixing, and C7 (Sept 2020)

- **Task 2.1, VIIRS Deep Blue reader:** `Aerosol_Optical_Thickness_550_{Land,Ocean}`, QA
  `Aerosol_Optical_Thickness_QA_Flag_{Land,Ocean}`, σ from
  `..._550_Expected_Uncertainty_{Land,Ocean}`; v2 and v2.1. Inspect the downloaded files to
  pin variable paths and groups.
- **Task 2.2, VIIRS Dark Target reader:** `geolocation_data/` and `geophysical_data/` groups;
  `Corrected_Optical_Depth_Land`, `Effective_Optical_Depth_Average_Ocean`,
  `Land_Ocean_Quality_Flag`; EE envelope for σ (VIIRS DT coefficients in config).
- **Task 2.3, stream selection:** `streams: [terra_dt, terra_db, aqua_dt, aqua_db]`,
  `[snpp_db, noaa20_db]`, or all. Mixed runs fill `aod_weighted_mean_modis` and `_viirs`.
- **Task 2.4, Sept 2020 verification:**
  - Smoke-plume behavior: the AOD cap, DT smoke-as-cloud gaps against DB coverage, and the
    Birge ratio where DT and DB disagree.
  - MODIS-only, VIIRS-only, and mixed fields compared.
  - NOAA-20 Deep Blue v2 against v2.1.
- **Task 2.5, C7 reader activation:** when LAADS publishes MOD04/MYD04 C7, fill in the
  NetCDF4 variable map, stage July 2008 and Sept 2020 C7, and compare with C6.1. If C7
  carries DT per-pixel uncertainty, switch DT σ to it.

## Phase 3: AERONET validation and defaults

- Stage AERONET Level 2.0 for July 2008 and Sept 2020 straight from the AERONET web service
  (no monetio dependency), under the same `paths` templates.
- Ångström-interpolate to 550 nm (port CAM7 `validation/aeronet.py`); collocate within the
  cell and window.
- Score each configuration (bias, RMSE, R, fraction within the expected error).
- Choose the defaults: ρ (sweep 0, 0.25, 0.5, 0.75, 1), negative-AOD handling,
  `min_pixels`, DT ocean Average vs Best.
- Record the evidence in `docs/`.

## Phase 4: Production (AMI)

Needs: AMI paths and archive layout, scheduler, Python and conda availability, network
access to LAADS.

- Fill `config/profiles/ami.yaml`.
- Multi-year driver (month chunks), throughput measurement, and storage estimate (L2 inputs
  versus outputs; outputs ≈ 8 files/day × ~1 MB).

---

## Open items and risks

| Item | Plan |
|---|---|
| MODIS C7 aerosol not released | Catalog and staging ready; reader is a variable map (Task 2.5) |
| DT expected-error coefficients | Confirm C6 land and ocean values against the DT references in Task 1.3 |
| ρ default | 0.5 placeholder; chosen by AERONET in Phase 3 |
| DT and DB retrieve the same scene (correlated across streams) | v1 treats streams as independent; Birge inflation partly covers it; a cross-stream ρ is a later option |
| CAM `%y/%m/%d/` template subdirectories | Confirm in a short derecho CAM run |
| AMI environment | Phase 4 |
| Southern Ocean noise | Partly addressed by retrieval-team DT ocean QA ≥ 2 and keeping negatives; denoising itself stays deferred. Useful later screens: `Average_Cloud_Pixel_Distance_Land_Ocean`, `Glint_Angle`, `Wind_Speed_Ncep_Ocean` |
