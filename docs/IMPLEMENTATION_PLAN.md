# Implementation Plan: MODIS/VIIRS L2 → CAM7 AOD constraint files

*Written 2026-10-10. Decisions referenced here are recorded in `AGENTS.md` ("Design
decisions").*

## Workflow (every task)

All work happens on `main`: no branches or worktrees unless explicitly instructed. A task, or
a phase, is closed only when it is complete, tested and reviewed. Then commit and push to
`origin/main`:

1. **Complete:** the task's code, config and docs, including any `AGENTS.md` decisions it
   settles.
2. **Tested:** the task's new tests plus the full suite pass (`pytest`), `mypy sarb_aod` is
   clean, and `black --check` and `isort --check` pass. Real-data steps run on the staged
   July 2008 / Sept 2020 data where the task calls for them.
3. **Reviewed:** a correctness review of the task's diff, with findings fixed or explicitly
   deferred in "Open items". For plotting tasks, the protocol report passes and the PNG
   previews have been visually inspected.
4. **Recorded:** this plan's Status table and Open items are updated in the same commit.
5. **Commit and push:** one commit per task (message `Task N.M: <summary>`), pushed to
   `origin/main` (SSH remote) immediately. A phase-closing commit updates the Status row to
   **Done**.

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
| 3 | AERONET validation suite (fetcher, collocation, statistics, reports); choose ρ and other defaults | Planned; tasks 3.1–3.4 can start alongside Phase 1 |
| 4 | Plotting and diagnostics in DAVINCI style: maps, AERONET bias maps, site and regional time series, protocol | Planned; tasks 4.1–4.4 land before task 1.10 |
| 5 | Production profile (AMI), multi-year runs | Needs AMI details |

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
  cli.py                + grid, validate, summarize, aeronet, evaluate
  aeronet/
    fetch.py            AERONET Web Service v3 client (no monetio), rate-limited, daily cache
    parse.py            v3 CSV → tidy table (site, time, lat, lon, AOD by λ, Ångström)
    spectral.py         AOD at 550 nm: quadratic log-log fit or Ångström (CAM7 method)
  evaluation/
    metrics.py          DAVINCI metric set (ported from davinci_monet/stats/metrics.py) + aerosol metrics
    calculator.py       grouped statistics (ported from davinci_monet/stats/calculator.py)
    collocate.py        L2-pixel and gridded-product matchups (PointStrategy logic from DAVINCI)
    regions.py          DAVINCI AEROSOL_REGIONS catalog (+ Southern Ocean band)
    report.py           CSV/JSON tables (ported from davinci_monet/stats/output.py)
  plots/                DAVINCI-style plotting (ported; matplotlib + cartopy)
    style.py            NCAR theme, palette, GEOSIT AOD levels/cmap, bias/sequential cmaps
    labeling.py         titles, subtitles, axis/legend/bias labels (DAVINCI rules)
    labels.py           display-name and acronym tables
    maps.py             spatial fields, site-marker layers, global-mean subtitles
    series.py           per-site and regional time series
    scatter.py          density scatter, stats box
    summary.py          binned bias, z-score, ρ sweep, scorecard, Taylor, box plots
    protocol.py         validate_rendered_figures(), protocol reports
    suites.py           suite expansion; `sarb-aod plot`
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
4. Maps of the monthly mean, the per-configuration differences, counts, and coverage, from
   the Phase 4 `aod_maps` and `aod_differences` suites. Tasks 4.1–4.4 land before this step.

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

## Phase 3: AERONET validation suite

A complete ground-truth evaluation, drawn from DAVINCI's AERONET, pairing and statistics
code and from CAM7's SP1 validation, ported into `sarb_aod` (standalone, **no monetio**).
It answers three questions:

1. Are the L2 streams and QC settings sound?
2. Is the gridded CAM7 product accurate?
3. Is the reported `aod_uncertainty` honest?

Tasks 3.1–3.4 don't depend on Phase 1 and can start alongside it.

| Source | What we take |
|---|---|
| DAVINCI `datasets/surface/aeronet.py` | product semantics (AOD10/15/20, site filter, all-points vs daily), dataset layout |
| monetio `obs/aeronet.py` (what DAVINCI delegates to) | Web Service v3 URL rules and CSV parsing, **re-implemented**, not imported |
| DAVINCI `stats/metrics.py` | full metric set: `N, MX, MY, STDX, STDY, MdnX, MdnY, MB, MdnB, NMB, NMdnB, FB, MNB, ME, MdnE, RMSE, NME, FE, MNE, R, R2, IOA, d1, E1, AC, RM, RMdn` |
| DAVINCI `stats/calculator.py`, `stats/output.py` | grouped statistics with `min_samples`, CSV/JSON tables |
| DAVINCI `pairing/strategies/point.py` | point ↔ grid matching within a radius of influence and time tolerance |
| DAVINCI `geography/domains.py` (`develop`) | 12-region `AEROSOL_REGIONS` catalog |
| CAM7 `sp1_aod_pipeline/validation/` | Ångström 550 nm conversion, seasonal stats, VP2 acceptance gates and the July 2008 baseline |

### Task 3.1: AERONET fetcher (`aeronet/fetch.py`)

- **Endpoint:** `https://aeronet.gsfc.nasa.gov/cgi-bin/print_web_data_v3` with
  `year, month, day, hour, year2, month2, day2, hour2`, `AOD10|AOD15|AOD20=1`, `AVG=10`
  (all points) or `AVG=20` (daily), optional `site=` or `lat1/lat2/lon1/lon2`, and
  `if_no_html=1`.
- **One request per UTC day, all sites.** Pace requests at no more than 10 per minute (the
  service's limit, which monetio honors with a 6 s sleep). Retry with backoff.
- **Recognize the service's non-data replies:**
  - a 1-line "no data" body;
  - an HTML error page;
  - a request whose `hour2` end drops the last hour (the plan verifies the boundary
    behavior with a live test).
- **Cache layout:** the raw CSV is the source of truth, written atomically with a manifest
  (bytes, row count, sites), at
  `{data_root}/AERONET/{product}/{year}/{month}/{day}/AERONET_{product}_ALLPOINTS_{yyyymmdd}.csv`.
  The path template and product (`AOD20` default; `AOD15` for recent periods without
  Level 2.0) come from config.
- `sarb-aod aeronet stage -c run.yaml [--dry-run]` is idempotent and resumable, using the
  same profile and paths machinery as granule staging.

**Tests:** URL construction against monetio's rules; a fake HTTP server serving a recorded
v3 CSV (fixture from the 2008-07-01 GSFC probe); handling of empty, HTML, and truncated
responses; the rate limiter. One `network`-marked live test.

### Task 3.2: Parser and spectral conversion (`aeronet/parse.py`, `aeronet/spectral.py`)

- **Parse the v3 CSV:** skip the 5 information lines; −999 → NaN; combine
  `Date(dd:mm:yyyy)` + `Time(hh:mm:ss)` into UTC time; carry `AERONET_Site`,
  `Site_Latitude(Degrees)`, `Site_Longitude(Degrees)`, `Site_Elevation(m)`,
  `Data_Quality_Level`, `Solar_Zenith_Angle(Degrees)`, `AOD_{440,500,675,870}nm`,
  `440-675_Angstrom_Exponent`, `440-870_Angstrom_Exponent`.
- **AOD at 550 nm, configurable:**
  - `quadratic_loglog` (default): fit ln τ against ln λ with a second-order polynomial
    over the available 440/500/675/870 nm, the usual practice in MODIS validation;
  - `angstrom_500`: τ₅₀₀·(550/500)^(−α₄₄₀₋₆₇₅), the CAM7 method, kept for regression.
- **AERONET uncertainty** for uncertainty calibration: σ ≈ 0.01–0.02 (instrument; config),
  plus within-window temporal variability.

**Tests:** parse the recorded fixture (column set, times, fill); both interpolations on
synthetic spectra with known exponents; the two methods agree for pure power laws.

### Task 3.3: Metrics and grouped statistics (`evaluation/metrics.py`, `calculator.py`)

- Port DAVINCI's metric classes and registry (`get_metric`, `STANDARD_METRICS`,
  `ALL_METRICS`, the percentage-metric set) with their numerical definitions unchanged.
- **Add aerosol-validation metrics:**
  - `%EE`: fraction within the expected-error envelope for the stream and surface (from the
    Task 1.3 coefficients);
  - regression slope and intercept, both OLS and reduced-major-axis;
  - bias binned by AERONET AOD;
  - uncertainty calibration: z = (sat − aeronet) / √(σ_sat² + σ_aer²), reporting
    std(z) and the fractions with |z| < 1 and |z| < 2, ideally ≈ 1, 68% and 95%.
- **Grouped statistics:** port DAVINCI's calculator (grouping, `min_samples`, rounding).
  Groups:
  - configuration (QC preset, weighting method, ρ), sensor and stream, product collection
    (C6.1 vs C7, DB v2 vs v2.1);
  - surface (land, ocean, coastal), region, season, day, site;
  - AOD bin; and a **Southern Ocean band** (< 40° S).

**Tests:** each metric against DAVINCI's definitions on fixed vectors (and against
`davinci_monet` itself when importable); EE envelope; RMA on exact lines; z-score
calibration on synthetic data with known σ.

### Task 3.4: Regions (`evaluation/regions.py`)

Port DAVINCI's aerosol-regime catalog `AEROSOL_REGIONS` (`geography/domains.py` on
`develop`; extents are lon_min, lon_max, lat_min, lat_max) with its aliases, its reference
(doi:10.5194/gmd-11-2333-2018), and CAM7's display names (`scripts/prepare_2008_regional_aod.py`):

| Key | Display name | Regime |
|---|---|---|
| `north_atlantic_ocean` | North Atlantic Ocean | dust outflow / marine |
| `south_atlantic_ocean` | South Atlantic Ocean | biomass-burning outflow |
| `north_indian_ocean` | North Indian Ocean | dust and pollution outflow |
| `north_africa` | North Africa (Sahara) | dust source |
| `southern_africa` | Southern Africa | biomass burning |
| `eastern_us` | Eastern US | pollution |
| `western_us` | Western US | smoke and dust |
| `canada` | Canada | boreal smoke |
| `south_america` | South America | biomass burning |
| `middle_east` | Middle East | dust |
| `east_asia` | East Asia | pollution and dust |
| `india` | India | pollution and dust |

Additions here:
- `global`;
- `southern_ocean` (< 40° S; clean marine, where the noise problem is);
- `labor_day_2020_smoke` (western US coast and adjacent Pacific, extent fixed in Task 4.6
  from the observed plume).

Regions can be extended in config.

### Task 3.5: Collocation (`evaluation/collocate.py`)

Two levels, each producing a tidy matchup table (one row per site × time unit), written to
NetCDF for reuse (no new Parquet dependency):

- **L2 stream level** (checks the readers and QC): satellite pixels within a radius of the
  site (default 25 km for MODIS 10 km and VIIRS 6 km products; config), averaged over the
  QC-passed pixels with a minimum count. They are matched to the AERONET mean within ±30 min
  of the pixels' scan time, with at least 2 AERONET points. One row per site × overpass ×
  stream. This is the retrieval teams' standard validation design, so `%EE` is comparable
  to published values.
- **Gridded product level** (checks the CAM7 files and weighting): for each 3-hour window,
  the f09 cell containing the site (DAVINCI `PointStrategy` nearest-cell logic, cyclic
  longitude), matched to the AERONET mean over the same window. One row per site × window ×
  configuration. Carries `aod_count`, `aod_uncertainty` and per-stream counts for
  conditional statistics.
- **Representativeness:** flag cells with several sites, coastal sites, and high-elevation
  sites (`Site_Elevation(m)` above a threshold) so they can be stratified or excluded.

**Tests:** synthetic swaths and grids with planted values at known sites; radius and time
windows at their boundaries; dateline and polar cells; no double counting across windows.

### Task 3.6: Reports (`evaluation/report.py`, `sarb-aod evaluate`)

- `sarb-aod evaluate -c run.yaml --level {l2,grid} [--configs ...]` builds the matchups,
  computes grouped statistics, and writes CSV and JSON tables (DAVINCI formatter) under
  `{data_root}/evaluation/{run}/`, with the provenance of every input.
- **Acceptance gates** (CAM7 VP2), reported pass/fail per configuration: |MB| ≤ 0.03,
  RMSE ≤ 0.10, R ≥ 0.8. The **baseline to beat** is CAM7's July 2008 MODIS-only result:
  N = 8875, MB = 0.0195, RMSE = 0.1464, R = 0.8555.
- **Plots:** come from the Phase 4 suites `aeronet_site_bias`, `aeronet_site_timeseries`,
  `aeronet_regional_timeseries`, `aeronet_scatter` and `evaluation_summary`, rendered from
  the same matchup tables.

### Task 3.7: Evaluation campaign and default selection

1. Stage AERONET AOD20 for July 2008 and September 2020.
2. **L2 level:** compare each stream under the `retrieval_team` and `cam7_v2` QC presets;
   DT ocean Average vs Best; NOAA-20 DB v2 vs v2.1; C6.1 vs C7 once C7 is available.
3. **Grid level:** sweep weighting (`count`; `gls` with ρ ∈ {0, 0.25, 0.5, 0.75, 1}),
   negative-AOD handling, `min_pixels` ∈ {1, 3, 5}, and MODIS-only vs VIIRS-only vs mixed
   (Sept 2020).
4. **Choose ρ** where std(z) ≈ 1 without losing RMSE, then set the other defaults from the
   tables. Separate reports for the Southern Ocean band and the Sept 2020 smoke box.
5. Record the evidence and chosen defaults in `docs/validation/` and update `AGENTS.md`.

## Phase 4: Plotting and diagnostics (DAVINCI style)

Publication-quality plots that follow DAVINCI's plotting practice exactly, ported into
`sarb_aod/plots/` (standalone: no `davinci_monet` or monetio import). Matplotlib and
Cartopy join `environment.yml` at the DAVINCI pins (`matplotlib=3.11.*`, `cartopy=0.25.*`).

Sources:

| DAVINCI (`origin/develop`) | What we take |
|---|---|
| `plots/style.py` | NCAR theme (`apply_ncar_style`, Poppins font with Helvetica/Arial fallback, `default`/`presentation`/`publication` font contexts, 300 dpi), NCAR colors and palette, `geosit_aod_levels()`, `get_geosit_aod_cmap()` (`turbo`), `get_bias_cmap()` (`RdBu_r`), sequential `viridis` |
| `plots/labeling.py`, `labels.py`, `titles.py` | all title, axis, legend, colorbar and subtitle composition |
| `plots/renderers/spatial/{base,field,bias}.py` | `draw_spatial_field`, area-weighted global mean in the subtitle, nice robust limits, symmetric limits, site-marker bias maps |
| `plots/renderers/timeseries.py` | paired series, per-site lines with legend, 45° date ticks |
| `plots/renderers/scatter.py`, `_stats.py` | density scatter (rasterized), 1:1 and regression lines, in-axes stats box |
| `plots/renderers/{scorecard,taylor,boxplot}.py` | regional summary figures |
| `plots/suites.py` (`gridded_aod_diagnostics`) | suite expansion pattern and AOD field titles |
| DAVINCI "Plot Protocol Enforcement" | `validate_rendered_figures()` protocol reports and the visual inspection gate |

### Rules (enforced in code and tests)

**AOD color scale.** Absolute AOD maps always use `geosit_aod_levels()`:
- 0.05 bins up to a plotted cap of 1.0, plus low-end levels 0.005, 0.01, 0.02, 0.03 and 0.04;
- `turbo`, with `BoundaryNorm(levels, cmap.N, extend="max")`;
- one colorbar with those discrete levels.

Never use ad-hoc min/max scaling for AOD.

**Other fields.**
- Signed differences and biases use the diverging `RdBu_r` with robust symmetric limits
  (`calculate_symmetric_limits`, 98th percentile) and `TwoSlopeNorm` centered on 0, so
  isolated outliers don't wash the map out.
- Counts, uncertainty and coverage use `viridis` with nice robust limits.

**Rasterization (PDF).** Dense filled layers are rasterized: `pcolormesh`, `contourf`,
`fill_between`, and large scatters (`rasterized=True` or `set_rasterized(True)`). Axes,
text, colorbars and contour *lines* stay vector; rasterizing a contour line set raises a
UserWarning, which the test suite treats as an error.
- Gridded AOD fill is configurable: `pcolormesh` on the exact f09 cells (DAVINCI
  `SpatialPlotter` default) or `contourf` on the GEOSIT levels. Both use the same
  `BoundaryNorm` and are rasterized.
- DAVINCI's comments say `ContourSet` can't be rasterized on matplotlib < 3.9. The pin is
  3.11, where `ContourSet` is a `Collection`; a test confirms rasterized `contourf` emits no
  warning and embeds as an image in the PDF.

**Titles.**
- Terse: the quantity, plus an operation word only when it changes meaning (`AOD Bias`,
  `AOD Difference`, `AOD Time Series`).
- Single-source titles put the source first (`sourced_title`): `MODIS AOD`, `AERONET AOD`.
- Audience-facing title case, preserving acronyms: AOD, MODIS, VIIRS, AERONET, CAM, SNPP,
  NOAA-20, DT, DB.
- **Never in titles:** dates, statistics, `Mean ± Std`, `vs <source>`, raw variable names
  (`aod_weighted_mean`), internal labels (phase or task numbers, `gls`, `rho`, `cam7_v2`),
  or `x`/`y`.

**Subtitles.**
- The date subtitle (`subtitle_text`: `YYYY-MM-DD – YYYY-MM-DD`).
- On maps, ` | Mean: <value>`, the cos-latitude area-weighted mean of finite cells.
- Configuration provenance (weighting, QC preset, collection) goes in the subtitle only when
  the audience needs it, and always in the filename and the PDF metadata.

**Labels.**
- Only through `labeling.py`: `axis_label(quantity + format_units(units))`, with
  dimensionless AOD showing no unit.
- `bias_label` produces `<Y source> − <X source>` with U+2212.
- Legend entries use `legend_label`.
- Sources are always display names (MODIS, VIIRS, AERONET, Terra Dark Target, …), never raw
  keys and never ALL-CAPS. Units are never baked into config label strings.

**Maps.**
- Global PlateCarree by default; regional extents only when a suite asks for a region.
- Coastlines and solid lat/lon gridlines; no political borders.
- Tight layout.

**Time series.** Date ticks rotated 45°; reference (AERONET) in NCAR gray `X_COLOR`,
satellite in NCAR blue `Y_COLOR`, extra sources from `NCAR_PALETTE`; positive bias red,
negative blue.

**Output.**
- PDF is the deliverable, with a PNG preview per figure for inspection.
- Dates, configuration and region live in filenames and directories, e.g.
  `{data_root}/plots/{run}/{suite}/{name}.{yyyymmdd}-{yyyymmdd}.pdf` (template in `paths`).
- Remove stale PNGs when replacing products.

### Task 4.1: Style and labeling core (`plots/style.py`, `plots/labeling.py`, `plots/labels.py`)

- Port the DAVINCI modules with their functions and tables unchanged in behavior.
- Extend the acronym and source tables for this project: MODIS, VIIRS, Terra, Aqua, SNPP,
  NOAA-20, AERONET, CAM, Dark Target, Deep Blue, and stream display names
  (`terra_dt` → "Terra Dark Target").

**Tests:** port DAVINCI `test_labeling.py` cases; title rules (a rejected-pattern list:
dates, `vs`, raw keys, `x`/`y`, internal labels); `geosit_aod_levels` values; unit
formatting.

### Task 4.2: Map renderers (`plots/maps.py`)

- `draw_spatial_field` (pcolormesh or contourf on cells, scatter for points), coordinate
  handling (0–360 → −180–180 for display), area-weighted mean subtitle, robust, symmetric
  and nice limits, colorbar with discrete GEOSIT ticks.
- Site-marker layer for AERONET maps; markers scaled by N within a fixed range.
- Optional regional extent from the region catalog (Task 3.4).

**Tests:** artist checks (BoundaryNorm with GEOSIT levels and `turbo` for AOD,
`TwoSlopeNorm` with `RdBu_r` for differences, rasterized mesh, vector text, coastlines
present, no borders feature); PDF inspection (the filled layer embedded as an image).

### Task 4.3: Line, scatter and summary renderers (`plots/series.py`, `plots/scatter.py`, `plots/summary.py`)

- Time series: per-site and regional; full AERONET record (light) plus matched points
  (bold); satellite as markers, since overpasses are sparse.
- Density scatter: 1:1, EE envelope lines for the stream or surface, OLS and RMA
  regression, stats box (N, MB, RMSE, R, %EE).
- Binned bias against AOD; z-score histogram with a unit-normal reference; ρ-sweep curves;
  region × metric scorecard; Taylor diagram; box plots by region.

**Tests:** artist and label checks for each figure type; date-tick rotation; stats-box
values against `evaluation.metrics`.

### Task 4.4: Plot protocol and inspection (`plots/protocol.py`)

- Port DAVINCI's `validate_rendered_figures()` pattern. Before saving, draw the canvas and
  reject clipped or overlapping titles, subtitles, panel headings, axis labels, legends and
  colorbar ticks.
- Check the rules above: norms and colormaps per field kind, rasterized dense layers, title
  and label patterns.
- Write a JSON protocol report per suite. A suite fails if any figure fails.
- Inspection gate: every production suite's PNG previews are viewed (multimodal inspection)
  before the suite is accepted. A passing protocol report is necessary but not sufficient.

**Tests:** a negative case per rule proving that a violation fails the suite.

### Task 4.5: Plot suites and CLI (`plots/suites.py`, `sarb-aod plot`)

Config-driven suites, in the style of DAVINCI `plot_suites` presets:

```yaml
plots:
  context: publication           # default | presentation | publication
  aod_fill: pcolormesh           # or contourf
  suites: [aod_maps, aod_differences, aeronet_site_bias, aeronet_site_timeseries,
           aeronet_regional_timeseries, aeronet_scatter, evaluation_summary]
  sites: {select: top_n, n: 20}  # or an explicit list of AERONET site names
  regions: all                   # or a list of region keys
```

`sarb-aod plot -c run.yaml [--suite ...]` renders from the CAM7 files and the Phase 3
matchup tables, never re-reading L2.

| Suite | Figures |
|---|---|
| **`aod_maps`** (spatial patterns) | **Mean AOD** for the period (count-weighted over windows, as CAM7's monthly analysis) and per day: product, per sensor (MODIS, VIIRS, mixed), per stream (Terra/Aqua × DT/DB, SNPP/NOAA-20 × DB/DT). **Uncertainty**, **observation count**, **coverage fraction** (fraction of 3-hour windows with data), **stream disagreement** (χ²/ν). Global by default; per-region panels when `regions` is set. |
| **`aod_differences`** | Configuration and product differences: `gls` − `count`, `retrieval_team` − `cam7_v2` QC, ρ variants, C7 − C6.1, NOAA-20 DB v2.1 − v2, VIIRS − MODIS. Diverging, symmetric robust limits, `bias_label` colorbars. |
| **`aeronet_site_bias`** (spatial bias against AERONET) | Site markers colored by mean bias (satellite − AERONET), plus RMSE and R maps per site; for each level (L2, grid) and configuration; global and per region. |
| **`aeronet_site_timeseries`** (per site, satellite subsampled to the site) | One figure per selected site: AERONET 550 nm (all points light, window means bold) against satellite values subsampled to the site, i.e. L2 pixels within the radius at overpass and the f09 cell value for each 3-hour window. One line per sensor or stream when requested. |
| **`aeronet_regional_timeseries`** (regional groups of sites) | For each region, daily regional AERONET mean using CAM7's method: average matched hours within each site, then give each active site equal weight. Plotted against the satellite subsampled to the same site-times (MODIS, VIIRS, mixed). A third line shows the full regional area-weighted satellite mean, so the sampling effect is visible. Panel subtitles carry active-site counts. |
| **`aeronet_scatter`** | Density scatter of satellite against AERONET per configuration, per region (small multiples) and per stream at L2 level. |
| **`evaluation_summary`** | Binned bias against AOD, z-score histograms, ρ-sweep curves, region × metric scorecard, Taylor diagram, regional box plots. |

**Tests:** each suite expands to its logical product list; an end-to-end render on
synthetic CAM7 files and matchups passes the protocol; filenames carry dates and
configuration.

### Task 4.6: Production plot runs

1. July 2008: all suites for the four Phase 1 configurations, plus the CAM7 reproduction.
   Compare the July maps visually with CAM7's accepted July 2008 plots.
2. Sept 2020: all suites, plus the smoke-box regional maps and time series (fixing the box
   extent from the observed plume). Show MODIS-only, VIIRS-only and mixed side by side.
3. Southern Ocean focus: maps and regional time series for `southern_ocean`, comparing QC
   presets and negative-AOD handling. This is the evidence base for the deferred denoising
   work.
4. Inspect every preview, record the accepted plot set in `docs/validation/`, and fix any
   protocol or visual failures before acceptance.

## Phase 5: Production (AMI)

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
| AMI environment | Phase 5 |
| Cartopy Natural Earth data | Cartopy downloads coastlines on first use; pre-fetch into a configured `cartopy.config['data_dir']` (a profile key) so batch and offline runs work |
| Poppins font | Used if installed, else the Helvetica/Arial fallback (DAVINCI behavior); install it for publication figures |
| AERONET Level 2.0 lags real time | `AOD15` fallback per run config; report which level each matchup used |
| AERONET service limits | ≤ 10 requests/min, one request per day for all sites, raw CSV cached so re-runs never re-fetch |
| Southern Ocean noise | Partly addressed by retrieval-team DT ocean QA ≥ 2 and keeping negatives; denoising itself stays deferred. Useful later screens: `Average_Cloud_Pixel_Distance_Land_Ocean`, `Glint_Angle`, `Wind_Speed_Ncep_Ocean` |
