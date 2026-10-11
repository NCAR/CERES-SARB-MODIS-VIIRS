# Implementation Plan: MODIS/VIIRS L2 → CAM7 AOD constraint files

*Written 2026-10-10; revised after a six-lens readiness review that checked every
claim against real July 2008 / Sept 2020 granules, the DAVINCI and CAM7 sources, CAM's
`filenames.F90`, and the live AERONET service. Decisions are recorded in `AGENTS.md` ("Design
decisions"). The 2026-10-11 user revision makes independent daily regional means the
AERONET acceptance basis. Where this plan cites a verified fact, it was measured on real data.*

## Workflow (every task)

All work happens on `main`: no branches or worktrees unless explicitly instructed. A task (or
phase) is closed only when it is **complete, tested and reviewed**; then it is committed and
pushed to `origin/main`.

1. **Complete:** the task's code, config, tests and docs, including any `AGENTS.md` decision it
   settles. Each task adds its own config section to `RunConfig` with defaults, so existing run
   configs keep loading.
2. **Tested (gates):**
   ```bash
   E=~/miniforge3/envs/sarb-aod/bin
   $E/python -m pytest -m "not network"     # network tests run separately when network is on
   $E/mypy sarb_aod
   $E/black --check . && $E/isort --check-only .
   ```
   Real-data steps run on the staged data where the task calls for them.
3. **Reviewed:** `codex review --uncommitted` (Codex) or `/code-review` (Claude) on the task's
   diff. Fix the findings or record them in "Open items", and list any that remain in the commit
   body. Plotting tasks also need a passing protocol report and visually inspected previews.
4. **Recorded:** update this plan's task table (State, Commit) and Open items in the same commit.
5. **Commit and push:** stage files explicitly (`git add <paths>`, never `git add -A`), message
   `Task N.M: <summary>`, push to `origin/main` (SSH remote) right away. Codex: git writes need
   a sandbox escalation (see `AGENTS.md`, "Running as Codex"); if it is not approved, stop with
   the work staged and say so.

## Goal

Turn staged L2 aerosol granules into CAM7-ready f09, 3-hourly, 550 nm AOD files: per-source
streams, retrieval-team QC, numba gridding, configurable weighting (GLS with an
error-correlation model by default; plain count weighting as an option), and the CAM7 obs-file
contract. MODIS-only, VIIRS-only, or mixed is a configuration choice.

Out of scope (deferred by the user): spatial/temporal denoising, VIIRS→MODIS bias correction.

## Status

| Phase | Content | State |
|---|---|---|
| 0 | Package, config schema/profiles, catalog, `stage`/`audit`, env, tooling | **Done.** July 2008 audited 9,518/9,518; September 2020 audited 29,856/29,856 after resumable recovery of four DNS failures. |
| 1 | MODIS C6.1 → CAM7 files (July 2008) | Implemented and verified; VP1 scientific acceptance open |
| 2 | VIIRS readers, MODIS+VIIRS mixing, C7 | C7 reader and native diagnostics implemented from AMI samples; monthly operational test and C7 science comparison tracked below |
| 3 | AERONET validation suite; choose ρ and other defaults | Daily campaign: 24 verified, 18 global passes including both baselines; regional failures and daily uncertainty remain open |
| 4 | Plotting and diagnostics (DAVINCI style) | All 1,552 figures pass protocols and visual inspection; separate science/SP1 acceptance remains open |
| 5 | Production profile (AMI), multi-year runs | July C7 operational test complete: 31 days, 248 validated files; multi-year production deferred |

### Tasks, dependencies and order

Work top to bottom (one agent); independent rows may run in parallel. **Recommended first code
task: 1.5** (no dependencies; reference code in CAM7 `sp1_aod_pipeline/f09_grid.py`,
`records.py`, `aggregate.py`; fully testable).

| Task | Title | Depends on | State | Commit |
|---|---|---|---|---|
| 0.1 | Tooling hygiene, env (pandas/matplotlib/cartopy/seaborn), AERONET fixtures | — | Done | 43d059e |
| 1.5 | Grid, windows, TAI93 → UTC (leap seconds) | — | Done | e00f9bd |
| 1.6 | Numba kernels | 1.5 | Done | 1613bbb |
| 1.1 | Catalog variable maps, stream registry | — | Done | ebddfbb |
| 1.2 | QC presets | 1.1 | Done | d357b79 |
| 1.3 | Per-pixel uncertainty | 1.1 | Done | c9f966a |
| 1.4 | MODIS HDF4 reader (+ generic NetCDF reader core) | 1.1, 1.2, 1.3, 1.5 | Done | 5853522 |
| 1.7 | Weighting (GLS, count) | 1.6 | Done | 3862a2e |
| 1.8 | CAM7 contract writer/validator, output paths | 1.5, 1.7 | Done | bb40c69, fb1af85 |
| 1.9 | Pipeline, run-config sections, CLI | 1.4, 1.6, 1.7, 1.8 | Done | 83979d9 |
| 3.4 | Regions | — | Done | 9433481 |
| 3.1 | AERONET fetcher | — | Done | 1388a75 |
| 3.2 | AERONET parser, 550 nm conversion | — (fixtures) | Done | ed2acd5 |
| 3.3 | Metrics, grouped statistics | — | Done | 28a15bc |
| 4.1 | Style, labeling, display tables | — | Done | 9a4b2c4 |
| 4.2 | Map renderers | 4.1, 1.5, 3.4 | Done | c471097 |
| 4.3 | Series, scatter, summary renderers | 4.1, 3.3 | Done | 8ff4cba |
| 4.4 | Plot protocol | 4.2, 4.3 | Done | 7af6d67 |
| 4.5a | Map suites (`aod_maps`, `aod_differences`) and `sarb-aod plot` | 4.4, 1.9 | Done | de7b22f |
| 1.10 | July 2008 verification | 1.9, 4.5a | Verified; VP1 acceptance open | fa0aad0 |
| 3.5 | Collocation (L2 and grid matchups) | 1.4, 1.9, 3.2, 3.4 | Done | 538ac0f |
| 4.5b | AERONET suites | 4.5a, 3.5 | Done | b06bb1f |
| 3.6 | Reports, `sarb-aod evaluate` | 3.3, 3.5, 4.5b | Done | 12a1ac9 |
| 2.1 | VIIRS Deep Blue reader | 1.4 | Done | d84ce20 |
| 2.2 | VIIRS Dark Target reader | 1.4 | Done | f4756c8 |
| 2.3 | Streams name their collection; mixing | 1.9, 2.1, 2.2 | Done | 64b71ab |
| 2.4 | Sept 2020 verification | 2.3, Sept 2020 staging | Verified; rejection-gate acceptance open | a733ece |
| 3.7 | Evaluation campaign, default selection | 3.6, 2.4 | Campaign complete; calibration open | 9b496b2 |
| 4.6 | Full plot runs | 3.7 | Plots accepted; science/SP1 acceptance open | 761c2da |
| 4.7 | Mean AOD comparison layout | 4.6 | Done | 35c6041 |
| 4.8 | Daily-mean validation and regional time-series comparisons | 3.6, 4.5b, 4.7 | Done | Task 4.8 (this commit) |
| 2.5 | MODIS C7 activation and supplied combined-product diagnostics | AMI C7 samples | Done; C7 science acceptance separate | 579b5d0 |
| 5.1 | Portable monthly driver, throughput and storage | 1.9 | Done | 675a38f |
| 5.2 | AMI profiles, tracked config naming and scheduler scripts | 2.5, 5.1 | Done | d69d688 |
| 5.3 | Deploy and process July 2008 C7 on AMI | 5.2 | Done; operational acceptance | Task 5.3 (this commit) |
| 5.4 | Multi-year production | 5.3, selected periods and science settings | Deferred | |
| 2.6 | C7 July/September science comparison and uncertainty assessment | 2.5, available inputs | Open | |

Test data: July 2008 MODIS C6.1 (9,518 granules, 23.7 GiB); Sept 2020 MODIS C6.1 + VIIRS DB v2
(SNPP, NOAA-20), DB v2.1 (NOAA-20), DT v2.1 (SNPP, NOAA-20) (29,856 granules, ~213 GiB). MODIS
C7 public acquisition was unavailable at the initial check; AMI has local July 2008
C7 aerosol files, independently verified on 2026-10-11.

---

## Architecture

```text
sarb_aod/
  config/schema.py      + catalog variable maps; Grid/QC/Uncertainty/Weighting/Time/Output/
                          AERONET/Plots config models; new path templates
  data/catalog.yaml     + per-collection geolocation and algorithm variable maps
  streams.py            stream registry: (product, collection, algorithm) → reader recipe
  readers/
    base.py             PixelBatch (t, lat, lon, aod, sigma, surface, stream)
    modis_hdf4.py       MOD04/MYD04 C6.1 (pyhdf)
    netcdf.py           C7 and VIIRS AERDB/AERDT (netCDF4, group paths, dimension-name selection)
  qc.py                 QC presets, per stream family and surface
  uncertainty.py        retrieval σ or expected-error envelope, σ floor
  timeconv.py           TAI93 → UTC with leap seconds; VIIRS DT coverage-attribute times
  windows.py            3-hourly half-open windows, CF center time
  grid/
    spec.py             f09 grid (CAM centers, clamped bounds), longitude normalization
    kernels.py          numba kernels (DAVINCI grid_binning.py port + extended accumulator)
    combine.py          weighting: gls (default) | count
  contract.py           CAM7 writer + validator (ported from CAM7 sp1_aod_pipeline/contract.py)
  pipeline.py           per-day driver, day-level parallelism, resumable
  cli.py                + grid, validate, summarize, aeronet stage/audit, evaluate, plot
  aeronet/
    fetch.py            Web Service v3 client (stdlib urllib; injectable hooks), daily cache
    parse.py            v3 CSV → table (by header name)
    spectral.py         550 nm: quadratic log-log (default) or Ångström from 500 nm (CAM7)
  evaluation/
    metrics.py          DAVINCI metric set (port) + aerosol metrics (%EE, OLS/RMA, z)
    calculator.py       grouped statistics (DAVINCI port), regional aggregations
    collocate.py        L2-pixel and gridded-product matchups; CAM7 pairing mode
    regions.py          DAVINCI AEROSOL_REGIONS + additions
    report.py           CSV/JSON tables (DAVINCI output.py port)
  plots/                DAVINCI-style plotting (port; matplotlib, cartopy, seaborn)
    style.py, labeling.py, labels.py, titles.py, _stats.py
    maps.py, series.py, scatter.py, summary.py
    protocol.py         validate_rendered_figures(), layout checks, protocol reports
    suites.py           suite expansion; `sarb-aod plot`
tests/
  data/aeronet/         AERONET fixtures (committed in Task 0.1)
  reference/            vendored CAM7 reference code (attribution) for regression tests
```

Data flow for one day D:

```text
granules of day D (+ last granule of D−1 if grid.edge_granules) → reader → PixelBatch per stream
  → QC → σ → UTC time → numba accumulate into acc[window, stream, lat, lon]
  → combine (gls | count) → mean, σ, counts, diagnostics → clip negative means at 0
  → 8 contract files (atomic write) → validate
```

---

## Phase 1: MODIS C6.1 → CAM7 files (July 2008)

### Task 1.1: Catalog variable maps and stream registry

A **stream** is one platform × algorithm source; its id is `<stream_prefix>_<algorithm>`:
`terra_dt`, `terra_db`, `aqua_dt`, `aqua_db` (Phase 2 adds `snpp_db`, `noaa20_db`, `snpp_dt`,
`noaa20_dt`). Each pixel also carries its surface (land/ocean) for QC and σ.

**Schema** (`config/schema.py`; all `StrictModel`):

```python
class VarRef(StrictModel):
    name: str                                  # SDS name, or "group/var" for NetCDF
    select: dict[str, int] | None = None       # {dimension name: index}; never positional

class SurfaceVars(StrictModel):
    aod: VarRef
    qa: VarRef
    sigma: VarRef | None = None                # None → expected-error envelope (Task 1.3)

class AlgorithmSpec(StrictModel):
    family: Literal["modis_dt", "modis_db", "viirs_dt", "viirs_db"]   # keys QC and σ tables
    surfaces: dict[Literal["land", "ocean"], SurfaceVars]
    alternates: dict[str, dict[Literal["land", "ocean"], SurfaceVars]] = {}  # QC preset → swap

class Geolocation(StrictModel):
    lat: str
    lon: str
    time: str | None                           # None → coverage attributes (VIIRS DT)
    time_kind: Literal["tai93", "coverage_attrs"]

# CollectionSpec gains:  geolocation: Geolocation | None = None
#                        algorithms: dict[Literal["dt", "db"], AlgorithmSpec] = {}
# ProductSpec gains:     stream_prefix: str    # terra | aqua | snpp | noaa20
```

**Catalog entry for MOD04_L2 and MYD04_L2 "6.1"** (pyhdf dimension names verified):

```yaml
geolocation: {lat: Latitude, lon: Longitude, time: Scan_Start_Time, time_kind: tai93}
algorithms:
  dt:
    family: modis_dt
    surfaces:
      land:  {aod: {name: Corrected_Optical_Depth_Land, select: {"Solution_3_Land:mod04": 1}},
              qa: {name: Land_Ocean_Quality_Flag}}
      ocean: {aod: {name: Effective_Optical_Depth_Average_Ocean, select: {"MODIS_Band_Ocean:mod04": 1}},
              qa: {name: Land_Ocean_Quality_Flag}}
    alternates:
      cam7_v2:
        ocean: {aod: {name: Effective_Optical_Depth_Best_Ocean, select: {"MODIS_Band_Ocean:mod04": 1}},
                qa: {name: Land_Ocean_Quality_Flag}}
  db:
    family: modis_db
    surfaces:
      land: {aod: {name: Deep_Blue_Aerosol_Optical_Depth_550_Land},
             qa: {name: Deep_Blue_Aerosol_Optical_Depth_550_Land_QA_Flag},
             sigma: {name: Deep_Blue_Aerosol_Optical_Depth_550_Land_Estimated_Uncertainty}}
```

Verified facts the map relies on:
- Index 1 is 0.55 µm on both wavelength axes ("0.47, 0.55, 0.66" land; "0.47, 0.55, 0.66,
  0.86, 1.24, 1.63, 2.13" ocean). The wavelength axis is first in MODIS and **last** in VIIRS DT,
  hence selection by dimension name.
- **Surface = whichever DT field is valid.** DT land and ocean never coexist in a pixel
  (0 overlaps). `Land_sea_Flag` is **not** used: its `valid_range` [0, 1] is wrong (value 2 =
  coastal occurs), so range-masking it would drop coastal retrievals.
- `Land_Ocean_Quality_Flag` is one 0–3 flag for whichever DT retrieval exists. The DB QA flag is
  a plain 0–3 integer (`_FillValue` 0); DB AOD is finite exactly where DB QA ≥ 1.
- MODIS C6.1 Deep Blue has no water retrieval.

Phase 1 fills only MOD04/MYD04 6.1 (VIIRS: Tasks 2.1/2.2; C7: Task 2.5). The registry raises
`no reader defined for <product> <collection>` for a collection without `algorithms`.

**Tests:** catalog validation; registry resolution for every collection with `algorithms` and
the clear error otherwise; stream ids from `stream_prefix`; dimension-name selection.

### Task 1.2: QC presets

```yaml
qc:
  preset: retrieval_team     # retrieval_team | cam7_v2
  aod_min: -0.05             # inclusive, on rounded physical values
  aod_max: 5.0               # inclusive
  overrides: {}              # e.g. {modis_dt: {ocean: {qa_min: 3}}} for the Phase 3 comparison
```

| Preset | DT land | DT ocean | DB land | DB water (VIIRS and MODIS C7) | AOD range |
|---|---|---|---|---|---|
| `retrieval_team` (default) | QA = 3 | **QA ≥ 1** | QA ≥ 2 | QA ≥ 2 (water QA takes only 1 or 3, so = 3) | [−0.05, 5] |
| `cam7_v2` | QA ≥ 1 | QA ≥ 1, `..._Best_Ocean` (MODIS only) | QA ≥ 1 | QA ≥ 1 | [0, 5], all streams |

- **DT ocean QA ≥ 1** follows Levy et al. (2013, AMT 6:2989): "QAC = 3 over land, and QAC ≥ 1
  over ocean", and C6 deliberately gives clean, no-retrieval scenes (AOD = 0) QAC = 1. It equals
  the products' own combined SDS `Optical_Depth_Land_And_Ocean` ("ocean (Average) (Quality
  flag = 1, 2, 3) and land (corrected) (Quality flag = 3)"), verified identical on 4.25 M MODIS
  pixels and stated in the VIIRS DT file. The darktarget "Level 2 product contents" web page
  says ocean QA 2–3, but QA = 2 is practically unused over ocean (31 of 3.2 M pixels), so that
  rule means QA = 3 and drops ~60% of ocean pixels (87% south of 40° S), the cleanest ones,
  biasing clean oceans high. Phase 3 compares QA ≥ 1 with QA = 3 via the override.
- **A QA fill value fails QC.** Example: 3,623 DT-ocean pixels on Terra 2008-07-01 have AOD 0
  and QA −9999.
- **Negative AOD.** In MODIS C6.1 only DT land is negative. It is clamped at −0.05 (raw −50):
  15.5% of QA = 3 land pixels are negative and 4% sit exactly at −0.05. DT ocean is floored at
  0, and DB's valid range starts at 0. Default: keep values ≥ `aod_min` (−0.05, inclusive) in
  the average and clip only the final cell mean at 0 for CAM (Task 1.8). CAM7 dropped AOD < 0,
  removing ~10% of DT land pixels and nothing over ocean. Over ocean the clean-scene bias comes
  from the QA threshold, not from negatives.
- **Rounding.** The HDF4 `scale_factor` is the float64 0.0010000000474974513, so raw −50 → −0.0500000024
  and raw 5000 → 5.0000002, which falsely fail inclusive bounds. `valid_range` is checked on raw
  integers; physical values are rounded (`round(raw*scale + offset, 6)`) before the AOD-range
  test. `cam7_v2` skips rounding (as CAM7 did, which dropped AOD = 5.0 pixels).
- **AOD > 5** matters for the Sept 2020 smoke. `aod_count_rejected_high` counts QA-passing
  pixels strictly above `aod_max` after rounding (0 for C6.1, whose valid range ends at 5.0).

**Tests:** threshold tables per preset, family and surface; overrides; QA fill rejected; raw
−50 and 5000 kept under `retrieval_team`; `cam7_v2` drops negatives on all streams and swaps
the MODIS DT ocean variable; the `cam7_v2` swap is skipped for VIIRS DT (no Best SDS).

### Task 1.3: Per-pixel uncertainty

```yaml
uncertainty:
  sigma_floor: 0.01
  envelopes:                    # σ = a + b·|τ|, treated as 1σ, per family and surface
    modis_dt: {land: [0.05, 0.15], ocean: [0.03, 0.10]}
    viirs_dt: {land: [0.05, 0.15], ocean: [0.05, 0.15]}
```

- **Retrieval-provided σ:** MODIS DB `Estimated_Uncertainty` (its long_name says "one-sigma
  confidence envelope") and VIIRS DB `Expected_Uncertainty` (1σ per the Deep Blue team).
  σ = max(|σ_raw|, `sigma_floor`), since VIIRS DB ocean values can be slightly negative.
- **Envelope σ** for Dark Target (no per-pixel AOD uncertainty). MODIS C6 (Levy et al. 2013):
  land ±(0.05 + 15%); ocean +(0.04 + 10%)/−(0.02 + 10%), used as its mean half-width
  (0.03, 0.10). VIIRS DT: ±(0.05 + 15%) over land and ocean (Sawyer et al. 2025, Front.
  Environ. Sci., doi:10.3389/fenvs.2025.1602145). The EE envelope contains ≥ 67% of AERONET
  matchups, so it approximates 1σ. CAM7 SP1 (the files CAM read in 2008) used the C5 ocean
  envelope (0.03, 0.05).
- **Missing σ** where AOD is valid (28 MODIS DB pixels on Terra 2008-07-01): the pixel counts in
  count mode, is excluded from GLS sums, and is tallied in `n_sigma_missing`.
- C7: if the product adds per-pixel DT uncertainty, the variable map points to it.
- The evaluation's `%EE` uses a separate envelope table (Task 3.3), not these σ coefficients.

**Tests:** envelope arithmetic; DB scaling and fill; |σ| and floor (EU = −0.0005 → 0.01);
missing-σ handling.

### Task 1.4: Readers

- **`readers/modis_hdf4.py` (pyhdf):** read only the SDS the selected streams need; select
  spectral bands by dimension name. Mask by `_FillValue` **and** `valid_range` on raw integers
  for AOD, σ, latitude, longitude and time; `Scan_Start_Time` uses −9999 even though its
  `_FillValue` is −999, and `valid_range` catches it. Never range-mask flag SDS (QA is masked by
  fill only). Scale per Task 1.2 rounding. Drop pixels with non-finite lat, lon or time.
- **`readers/netcdf.py` (netCDF4):** "group/var" paths, dimension-name selection, auto
  mask/scale; QA read with `.filled(0)` so v2/v2.1 fill conventions agree.
- **Granules:** day D's directory (via `paths.granule_dir`). With `grid.edge_granules: true`, also
  the last granule of D−1 when its directory exists; a missing D−1 directory is not an error,
  and D+1 is never read. Default `false`: with leap-second-correct times, no MODIS C6.1 or VIIRS
  pixel falls outside its file's UTC day (verified; the "crossings" seen before came from naive
  TAI93 conversion). Pixels outside [D 00:00, D+1 00:00) UTC are dropped.
- Returns one `PixelBatch` per stream: UTC time, lat, lon, aod, σ (NaN when absent), surface.

**Tests:** synthetic HDF4 granules written with pyhdf (int16 with scale and fill) and NetCDF4
with groups; clamp values kept; QA fill and time −9999 dropped; dimension-name selection; one
`real_data` smoke test that reads a real granule and skips when none is found under the
`data_root` of the profile named by `$SARB_AOD_PROFILE` (default `mac`). No hard-coded paths.

### Task 1.5: Grid, windows, time

- **`grid/spec.py`:** f09 exactly as CAM reads it (port CAM7 `f09_grid.py`):
  `lat = linspace(-90, 90, 192)`, `lon = arange(288)·1.25`; latitude edges at midpoints,
  clamped to ±90; longitude edges `arange(289)·1.25 − 0.625`. `grid.source: f09 | file:<path>`;
  a file grid is accepted only if its centers equal f09 to 1e-6 (CAM aborts on anything else),
  and bounds are always written from f09.
- **Longitude normalization:** `lon = ((lon − lon_edges[0]) % 360) + lon_edges[0]`, i.e.
  `((lon + 0.625) % 360) − 0.625` (CAM7 `grid.py`). Never `lon % 360`, which drops pixels in
  (−0.625, 0).
- **`windows.py`:** `times_per_day` 8; half-open windows [start, start + 3 h); CF center time
  with `units = "hours since 2000-01-01 00:00:00 UTC"` and `calendar = "gregorian"`, exactly as
  CAM7 `contract.py`.
- **`timeconv.py`:** TAI93 → UTC by subtracting the leap seconds inserted since 1993-01-01
  (1993-07-01, 1994-07-01, 1996-01-01, 1997-07-01, 1999-01-01, 2006-01-01, 2009-01-01,
  2012-07-01, 2015-07-01, 2017-01-01: 6 s in 2008, 10 s from 2017). `time.leap_seconds:
  apply | ignore` (default `apply`; the `cam7_v2` run uses `ignore`, as CAM7 did). Naive
  conversion moves 0.056% of pixels to the wrong window.

**Tests:** f09 centers and bounds against CAM7 values; a pixel at exactly D+1 00:00:00 lands
only in D+1 window 0; the 23:55 granule's last scan (TAI93 489110405.94) → 2008-07-01
23:59:59.94, window 7, with `apply`, and outside day D with `ignore`; leap-second table edges.

### Task 1.6: Numba kernels (ported from DAVINCI)

- Port `bin_swath_to_grid`, `bin_swath_to_grid_uncertainty`, `normalize_grid`,
  `normalize_uncertainty_grid` and `edges_from_centers` from DAVINCI
  `davinci_monet/pairing/grid_binning.py` (identical on `main` and `origin/develop`) with
  attribution, verbatim except `isnan` → `isfinite` in `bin_swath_to_grid` (recorded in the
  header). They keep DAVINCI's `(time, lon, lat)` order and argument order.
- **Extended kernel** (`nopython`, `cache=True`):
  `accumulate(time_edges, lat_edges, lon_edges, t, lat, lon, x, sigma, stream, acc...)`. All
  accumulators have shape `(nwin, nstream, nlat, nlon)`; `stream` is a scalar for the batch.
  - int64: `N`, `N_neg` (x < 0), `N_sigma_missing`.
  - float64: `Σx`, `Σx²`, `Σw`, `Σw·x`, `Σ(1/σ)`, with w = 1/σ²; pixels with NaN σ skip the w-sums.
  - Time bins are half-open (t ≥ last edge is rejected; no time clamp). Spatial edges keep
    DAVINCI's inclusive last edge and clamp.
- `N_rejected_high` comes from a second call of the count kernel on QA-passing pixels above
  `aod_max`.
- Memory: 9 arrays × 8 windows × 4 streams × 55,296 cells × 8 B ≈ 127 MB per day (MODIS);
  about 255 MB with 8 streams.

**Tests:** against a pure-NumPy reference (`searchsorted(side="right") − 1`, clip, `np.add.at`,
as in CAM7 `prepare_modis_era5_grid.py`) on 1e5 random pixels with ~5% NaN/inf and edge
coordinates; integers exact, floats rtol 1e-12; no `davinci_monet` dependency. Edge cases:
lon −0.625 → col 0, −0.6 → 0, −1e-4 → 0, 359.4 → 0, 359.375 → 287, ±180 → 144; lat −90 → row
0, 90 → 191; lat −999 dropped; t = 10800 → window 1. Benchmark recorded (target ≥ 10⁷ pixels/s).

### Task 1.7: Weighting (`grid/combine.py`)

```yaml
weighting:
  method: gls                # gls | count
  rho: 0.5                   # within-stream pairwise error correlation (Phase 3 chooses)
  birge: true
  min_pixels: 1              # per cell and window, all streams
  single_pixel_sigma: floor  # count mode, N = 1: floor (sigma_floor) | zero (CAM7)
```

**`gls` (default): generalized least squares with an error-correlation model.** For stream
*s* in a cell, with pixel errors Cov(eᵢ, eⱼ) = ρ·σᵢ·σⱼ:
- x̄ₛ = Σw·x / Σw, with w = 1/σ²
- sₛ = Σ(1/σ) / Σw (the inverse-variance-weighted mean pixel σ)
- **Vₛ = ρ·sₛ² + (1 − ρ)/Σwₛ.** This is the exact variance of x̄ₛ:
  Var(Σwᵢeᵢ/W) = (1 − ρ)/W + ρ·(Σ1/σᵢ)²/W². With equal σ it reduces to σ²(ρ + (1 − ρ)/N);
  N = 1 gives σ². (An earlier draft used the mean of σ², which overstates uncertainty by up to
  28% when σ varies; corrected after Monte Carlo checks.)
- S = number of streams with Nₛ ≥ 1 and Σwₛ > 0; Wₛ = 1/Vₛ; mean = ΣWₛx̄ₛ/ΣWₛ; σ = (ΣWₛ)^−½.
- Birge: if S ≥ 2, χ² = ΣWₛ(x̄ₛ − mean)², ν = S − 1, and σ ← σ·√(χ²/ν) when χ²/ν > 1.
  `aod_chi2_per_dof` = χ²/ν, or −999 when S < 2.
- nₑff,ₛ = sₛ²/Vₛ; `aod_n_effective` = Σₛ nₑff,ₛ (count mode writes N).
- `aod_mean_<stream>` = x̄ₛ.

**`count`:** simple count weighting with no uncertainty, matching CAM7 `prepare_modis_era5_grid.py`:
- mean = Σx/N over all valid pixels of all streams;
- `aod_uncertainty` = standard error from Σx and Σx² (ddof 1, variance clipped at 0);
- N = 1 → `sigma_floor` (`floor`) or 0 (`zero`);
- `aod_mean_<stream>` = per-stream arithmetic mean.

Both methods: cells with N_total < `min_pixels` get −999 for mean, σ and diagnostics, and
`aod_count` = 0.

**Tests (pure NumPy):**
- `count` equals the arithmetic mean.
- `gls` with equal σ and ρ = 0 gives the same mean as `count` (to 1e-12). Its σ is
  σ/√N_total (× the Birge factor), not the count-mode standard error.
- Monte Carlo with heterogeneous σ and Cov = ρσᵢσⱼ: std(x̄/√V) within 0.97–1.03 for
  ρ ∈ {0, 0.5, 0.9} and N ∈ {1, 10, 100}.
- ρ → 1 saturation; Birge fires only when streams disagree; `min_pixels` masking; empty cells
  give fill.

### Task 1.8: CAM7 contract writer, validator, output paths

Port CAM7 `sp1_aod_pipeline/contract.py` and `f09_grid.py`. Required variables and attributes
stay identical, so `aod_nudging_obs.F90` reads the files unchanged. CAM reads only `lat`/`lon`
(192/288, centers checked to 1e-3), `aod_weighted_mean` (any value > −900 is "valid") and the
scalar `time` (hours); everything else is for us.

- **Clip before writing:** `aod_weighted_mean` (and `_modis`/`_viirs`) = max(mean, 0) when
  `output.clip_negative_mean` (default true). Per-stream means and σ are written unclipped.
  The `aod_count_clipped_negative` diagnostic counts clipped cells; NaN/inf → −999.
- **Extra variables** (CAM ignores them):
  - `aod_count_<stream>`, `aod_mean_<stream>`;
  - `aod_weighted_mean_modis` / `_viirs` (fill when absent);
  - `aod_chi2_per_dof`, `aod_n_effective`;
  - `aod_count_negative`, `aod_count_rejected_high`, `aod_count_clipped_negative`.
- **Required globals:**

  | Attribute | Value |
  |---|---|
  | `Conventions` | `CF-1.10` |
  | `title` | `MODIS/VIIRS L2 AOD (550 nm) on the CAM f09 grid, 3-hourly` |
  | `institution` | `NCAR` |
  | `source` | the products and collections, plus streams |
  | `history` | `created by sarb_aod <version>` |
  | `references` | `CERES-SARB-MODIS-VIIRS docs/IMPLEMENTATION_PLAN.md; CAM7 obs contract (CERES-SARB-CAM7 sp1_aod_pipeline/contract.py)` |
  | `qc_thresholds` | the rendered QC table |
  | `merge_method` | `gls (pixel inverse variance, within-stream correlation rho, Birge inflation)` or `count (arithmetic mean of all valid pixels)` |
  | `merge_version` | `sarb_aod-<version>` |
  | `bias_correction_method`, `bias_correction_version` | `none` |
  | `grid` | `f09 FV 0.9x1.25 (192x288)` |
  | `aod_wavelength_nm` | int32 550 |
  | `date_created` | UTC ISO 8601 with `Z` |
  | `git_commit` | the HEAD SHA |

- **Extra globals:** `weighting_method`, `weighting_rho`, `qc_preset`, `streams`,
  `product_collections`, `leap_seconds`, `config_sha256`, `source_granule_count`,
  `edge_granules_missing`.
- **Bounds:** `lat_bnds`/`lon_bnds` always come from f09 (lat clamped to ±90).
- **Paths:** `RunConfig.name` (default: the run-config file stem) is available as `{run}`.
  `paths.output` (allowed fields `data_root, run, stem, year, month, day, doy, yyyymmdd, sssss`;
  it must contain `{sssss}` and a date) defaults to
  `{data_root}/CAM7/{stem}/{run}/{year}/{month}/{day}/{stem}.{yyyymmdd}.{sssss}.nc`, so separate
  configurations never overwrite each other. `output.stem` defaults to
  `MODIS_VIIRS_AOD_3hr_f09` (pattern `^[A-Za-z0-9_.-]+$`).
- **CAM namelist (verified in CAM `filenames.F90`, tag cam6_4_161):**
  - `aod_obs_path = '<data_root>/CAM7/<stem>/<run>/'`. The trailing slash is required, because
    CAM concatenates path and template with no separator.
  - `aod_obs_template = '%y/%m/%d/<stem>.%y%m%d.%s.nc'`. `/` is copied literally, `%y` is 4
    digits and `%s` 5 digits.
  - Limits: template ≤ 80 characters (`character(len=cs)`); full path < 256.
  - `sarb-aod show-config` prints both values, derived from `paths.output`.
  - **A missing file makes CAM run free silently** ("AODNUDGE: obs file absent, running free").
    Confirmation on derecho: `atm.log` shows "reading" for every window and no "absent" lines.
- Writes are atomic (temp file, then rename).

**Tests:**
- Vendored CAM7 `contract.py`/`f09_grid.py` (`tests/reference/cam7_sp1/`, with attribution)
  write a reference file from synthetic input. Compare its structure with ours:
  - dims;
  - `lat, lon, lat_bnds, lon_bnds, time, time_bnds, aod_weighted_mean, aod_uncertainty, aod_count`:
    dtype, dims and order, `_FillValue`, units, standard_name, bounds, calendar;
  - all 15 `REQUIRED_GLOBAL_ATTRS` present, `Conventions == CF-1.10`.
- Our files pass the vendored `validate_contract_file` unchanged.
- A cell averaging −0.02 is written as 0.0 in `aod_weighted_mean`, and −0.02 in its stream mean.
- Round trip.
- The validator rejects a wrong grid, a missing `time` and a wrong fill.
- `paths.output` placeholder validation.

### Task 1.9: Pipeline, run-config sections, CLI

**Config** (all new `RunConfig` sections have defaults; `grid` is required only by
`sarb-aod grid`):

```yaml
grid:
  source: f09
  times_per_day: 8
  edge_granules: false
  streams:                      # id → product and ONE collection
    terra_dt: {product: MOD04_L2, collection: "6.1"}
    terra_db: {product: MOD04_L2, collection: "6.1"}
    aqua_dt:  {product: MYD04_L2, collection: "6.1"}
    aqua_db:  {product: MYD04_L2, collection: "6.1"}
time: {leap_seconds: apply}
output: {stem: MODIS_VIIRS_AOD_3hr_f09, clip_negative_mean: true}
```

**Validators:**
- Each stream id equals `<stream_prefix>_<algorithm>` of its product.
- (product, collection) exists in the catalog with that algorithm and appears in `stage.products`.
- All streams of one product use the same collection. Comparing collections (C6.1 vs C7,
  DB v2 vs v2.1) takes separate run configs; mixing them would double-count scenes.

**Commands:**
- `sarb-aod grid -c run.yaml [--day D] [--overwrite] [--workers N]`: per-day processing, days in
  parallel (`ProcessPoolExecutor`). It skips days whose 8 files exist and validate. Exit codes:
  - 0 only if every requested day has 8 valid files;
  - otherwise 1, listing the failed days and their exceptions.

  A day with no primary granules fails. Missing edge granules are warnings, recorded in
  `edge_granules_missing`.
- `sarb-aod validate <files|dir>`.
- `sarb-aod summarize -c run.yaml` writes a TSV:
  - per-window coverage: the fraction of the 55,296 cells with `aod_count` > 0;
  - the period mean: the cos(lat)-weighted mean of per-cell period means;
  - percentiles;
  - per-stream contributions.
- `sarb-aod show-config` also prints the CAM namelist values.

**Tests:** an end-to-end run on synthetic granules gives 8 valid files; an idempotent re-run;
one failing day gives exit 1 with the other days written; schema cases (an unknown
placeholder, existing run configs still load, two collections of one product rejected).

### Task 1.10: July 2008 verification

1. **Four run configs** (each its own `{run}`):
   - `config/runs/modis_l2_200807_gls_rho0.yaml`;
   - `..._gls_rho05.yaml`;
   - `..._count.yaml` (`retrieval_team` QC);
   - `..._cam7_v2.yaml` (the CAM7 reproduction).
2. **CAM7 reproduction (`cam7_v2` run), exactly:**
   - DT ocean = `Effective_Optical_Depth_Best_Ocean[0.55]`; DT AOD = land where finite, else ocean;
   - DT QA ≥ 1; DB QA ≥ 1; 0 ≤ AOD ≤ 5 for all streams;
   - fill and `valid_range` on raw counts; unrounded float64 scaling;
   - `weighting.method: count` with `single_pixel_sigma: zero`;
   - `edge_granules: false`; `leap_seconds: ignore`; window [00:00, 24:00) half-open;
   - longitude `((lon + 0.625) % 360) − 0.625`.

   **Oracle:** CAM7 `scripts/prepare_modis_era5_grid.py` itself, vendored under
   `tests/reference/cam7/` with attribution. Load it with `importlib.util` (register it in
   `sys.modules` before `exec_module`; its DAVINCI import falls back to `np.add.at`) and run it on
   an f09 `Grid` through `Accumulator`/`accumulate_pixels`/`finalize`. On 3 real granules, one of
   them a 23:55 granule:
   - `aod_count` and the four `aod_count_<stream>` match exactly;
   - `aod_weighted_mean` and `aod_uncertainty` pass
     `assert_allclose(rtol=1e-6, atol=1e-7)` (float32).

   This reproduces CAM7's **diagnostic** four-stream product (merge_version
   `modis-era5-grid-v2-four-stream-count-weighted`), *not* the SP1 archive CAM read in 2008
   (`AOD_f09_3hr_2008_v2`: DT/DB Combined SDS, EE σ, inverse variance, σ floor 0.001). Don't
   compare against `AOD_f09_3hr*` files.
3. **Sanity numbers** (CAM7 VP1), via `sarb-aod summarize`:
   - per-window coverage about 1–10%;
   - the July mean (cos-lat weighted, cells with ≥ 1 valid window) 0.1–0.25;
   - written `aod_weighted_mean` in [0, 5] with 99.9th percentile ≤ 3.
4. **Maps** from the Task 4.5a suites for the four runs.

---

## Phase 2: VIIRS, mixing, and C7 (Sept 2020)

Facts below were verified on real 2020-09-08 granules (SNPP DB v2, NOAA-20 DB v2 and v2.1,
SNPP and NOAA-20 DT v2.1). All VIIRS files are NetCDF4/HDF5 (signature `\x89HDF`), 6-minute
granules starting on 6-minute boundaries, so no granule straddles a 3-hour window. DB and DT for
the same platform and time share the same 8×8-aggregated swath grid.

### Task 2.1: VIIRS Deep Blue reader (AERDB v2 and v2.1; one variable map serves both)

| Item | Variable (root group; DB files have no groups) | Notes |
|---|---|---|
| dims | `Idx_Atrack` (402–406, varies) × `Idx_Xtrack` (400) | |
| lat, lon | `Latitude`, `Longitude` | float32 |
| time | `Scan_Start_Time` | TAI93 seconds, float64, constant along each row (2 rows per scan); converted with leap seconds (Task 1.5) |
| land AOD / QA / σ | `Aerosol_Optical_Thickness_550_Land` / `Aerosol_Optical_Thickness_QA_Flag_Land` / `Aerosol_Optical_Thickness_550_Expected_Uncertainty_Land` | float32, no scaling, `_FillValue` −999, AOD `valid_range` [0, 5] (no negative AOD) |
| ocean AOD / QA / σ | same names with `_Ocean` | |
| surface | whichever AOD field is valid | DB has no land/sea flag |

- **σ:** Expected Uncertainty is the Deep Blue team's 1σ; `valid_range` [−0.5, 5]; σ =
  max(|EU|, `sigma_floor`).
- **QA:** 1 = poor, 2 = moderate (land only), 3 = good; water QA takes only 1 or 3. `_FillValue`
  is 0 in v2 and −999 in v2.1: read with auto-mask and `.filled(0)`.
- **Land/ocean overlap:** hundreds of coastal pixels per granule carry both a valid land and a
  valid ocean AOD (ocean QA always 1 there). Apply QC per surface first. If a pixel passes on
  both, keep only the land retrieval (CAM7 `ingest_viirs.py` precedent), so each pixel
  contributes at most one value per stream.
- `*_Best_Estimate` = base fields restricted to QA ≥ 2 (verified). The reader uses base fields
  plus explicit QC, so presets stay configurable.

**Tests:** v2-style (fill 0) and v2.1-style (fill −999) synthetic QA through one mask; a pixel
valid on both surfaces with QA 1/1 under `cam7_v2` yields one land pixel; on a real granule
N_out = N_land_pass + N_ocean_pass − N_both_pass; EU = −0.0005 → σ = `sigma_floor`.

### Task 2.2: VIIRS Dark Target reader (AERDT v2.1)

| Item | Variable | Notes |
|---|---|---|
| dims | `number_of_lines_8x8` (≈406) × `number_of_pixels_8x8` (400) | never load the `*_1x1` (3248 × 3200) arrays |
| lat, lon | `geolocation_data/latitude`, `geolocation_data/longitude` | lowercase; float32, `_FillValue` −999 |
| time | **none in the file** | from the global attributes (below) |
| land AOD | `geophysical_data/Corrected_Optical_Depth_Land`, select `{Wavelength_Used_Land_2: 1}` (0.55 µm) | int16, scale 0.001, `valid_range` [−100, 5000] packed → [−0.1, 5.0] |
| ocean AOD | `geophysical_data/Effective_Optical_Depth_Average_Ocean`, select `{Wavelength_Used_ALL: 1}` | VIIRS has **no** `..._Best_Ocean` |
| QA | `geophysical_data/Land_Ocean_Quality_Flag` | int16, 0 bad … 3 very good, `_FillValue` −9999; one flag for whichever surface was retrieved |
| surface | whichever AOD field is valid | never overlap; `geophysical_data/Land_Sea_Flag` (0 ocean, 1 land, 2 coastal) diagnostic only |

- QA presets as MODIS DT (land QA = 3, ocean QA ≥ 1): the VIIRS combined SDS
  `Optical_Depth_Land_And_Ocean` is documented as "ocean (Average) (Quality flag = 1, 2, 3)
  and land (corrected) (Quality flag = 3)". Check:
  `Corrected_Optical_Depth_Land[..., 1] == Optical_Depth_Land_And_Ocean` at land QA = 3 pixels
  (exact on the SNPP 2020-09-08 20:24 granule).
- **Time:** t_row = t0 + (row + 0.5)/n_rows × (t1 − t0), with t0 and t1 the global attributes
  `time_coverage_start` and `time_coverage_end` (ISO UTC, so no leap-second correction). Assert
  that t0 falls on a 6-minute boundary and that t1 − t0 = 360 s, and fail loudly otherwise.
- **σ** from the `viirs_dt` envelope (Task 1.3). `cam7_v2` has no VIIRS DT precedent and keeps
  `Average_Ocean`.

**Tests:** dimension-name selection on a real granule (the identity above); every DT pixel
lands in the same window as the DB pixel on the same row of the same granule; the coverage
assertion fires on a malformed interval.

### Task 2.3: Streams name their collection; mixing

```yaml
grid:
  streams:
    snpp_db:   {product: AERDB_L2_VIIRS_SNPP,   collection: "2"}
    noaa20_db: {product: AERDB_L2_VIIRS_NOAA20, collection: "2"}
    snpp_dt:   {product: AERDT_L2_VIIRS_SNPP,   collection: "2.1"}
    noaa20_dt: {product: AERDT_L2_VIIRS_NOAA20, collection: "2.1"}
```

MODIS-only, VIIRS-only or mixed is just the stream list. Mixed runs fill
`aod_weighted_mean_modis` and `aod_weighted_mean_viirs`. Comparing NOAA-20 DB v2 with v2.1
takes a second run config that sets `noaa20_db` to collection "2.1".

### Task 2.4: Sept 2020 verification (measurable)

For 2020-09-08 to 2020-09-14 over the smoke box (−125, −116, 36, 49), report per stream the
QC-passed pixel count, p50/p90/max AOD, and the count at or above `aod_max`. Write
`docs/validation/sept2020_summary.tsv`. Accept when:

1. DB p90 > DT p90 in the box: the smoke-as-cloud gap is visible. One SNPP granule gave DB p90
   1.82 vs DT 0.33.
2. `aod_count_rejected_high` > 0 on at least one day.
3. χ²/ν > 1 in some plume cells of mixed DT + DB runs.
4. Each mixed run's gridded mean lies between the MODIS-only and VIIRS-only means wherever both
   exist.

Also compare NOAA-20 DB v2 with v2.1 (Task 2.3).

### Task 2.5: C7 activation from AMI archive samples

Activate local AMI C7 (CMR version `7`, token `007`, NetCDF4) using inspected
Terra/Aqua samples. Fill dimension-name variable maps, including DB ocean and
retrieval uncertainties, and separately grid the supplied combined AOD and
algorithm categories requested by Seiji. Preserve the CAM consumer contract.
See [MODIS C7](MODIS_C7.md) for semantics and evidence. No per-pixel DT AOD
uncertainty was found; the configured envelope remains provisional. Task 5.3
runs July operationally; Task 2.6 retains July/September science comparisons
and uncertainty assessment. Public acquisition does not gate local activation.

---

## Phase 3: AERONET validation suite

A complete ground-truth evaluation, drawn from DAVINCI's AERONET, pairing and statistics code
and CAM7's SP1 validation, ported into `sarb_aod` (standalone, **no monetio**). It answers
three questions:
1. Are the L2 streams and QC settings sound?
2. Is the gridded CAM7 product accurate?
3. Is the reported `aod_uncertainty` honest?

Tasks 3.1–3.4 have no Phase 1 dependency.

| Source | What we take |
|---|---|
| DAVINCI `datasets/surface/aeronet.py` | product semantics (AOD10/15/20, site filter, all points vs daily) |
| monetio `obs/aeronet.py` (what DAVINCI delegates to) | Web Service v3 URL and parsing rules, **re-implemented** |
| DAVINCI `stats/metrics.py` | all 27 metrics: `N, MX, MY, STDX, STDY, MdnX, MdnY, MB, MdnB, NMB, NMdnB, FB, MNB, ME, MdnE, RMSE, NME, FE, MNE, R, R2, IOA, d1, E1, AC, RM, RMdn` |
| DAVINCI `stats/calculator.py`, `stats/output.py` | grouped statistics (`min_samples` 3), CSV/JSON tables |
| DAVINCI `pairing/strategies/base.py` `_find_nearest_1d` | nearest-cell index logic only (no radius cut, no time interpolation) |
| DAVINCI `geography/domains.py` (`origin/develop`) | 12-region `AEROSOL_REGIONS` (Bhattacharjee et al. 2018, GMD 11:2333, Table 1) |
| CAM7 `sp1_aod_pipeline/validation/`, `scripts/summarize_2008_regional_aeronet_band_comparison.py` | Ångström 550 nm, VP2 gates, the July 2008 baseline, the regional aggregation method |

### Task 3.1: AERONET fetcher (`aeronet/fetch.py`)

- **Request one UTC day D** (verified 2026-10-10):
  `print_web_data_v3?year=D.y&month=D.m&day=D.d&hour=0&year2=(D+1).y&month2=(D+1).m&day2=(D+1).d&hour2=0&AOD20=1&AVG=10&if_no_html=1`.
  - The end bound is the next day at 00 UTC, exclusive.
  - **Do not use `hour2=23` with `day2=D`.** It loses all 23 UTC data and, for
    eastern-hemisphere sites, everything after about 21 UTC (570 of 13,258 rows on 2020-09-08).
  - After parsing, keep only rows dated D (defensive filter).
- **Config:**
  ```yaml
  aeronet:
    product: AOD20         # AOD10 | AOD15 | AOD20
    avg: all_points        # all_points (AVG=10; the evaluation uses this) | daily (AVG=20; optional cache)
    min_interval_s: 6.0    # service limit: 10 requests/min (monetio sleeps 6 s)
    retries: 3
    sites: null            # optional list, validated against aeronet_locations_v3.txt (unknown → error)
  paths:
    aeronet: "{data_root}/AERONET/{product}/{year}/{month}/{day}/AERONET_{product}_{avg}_{yyyymmdd}.csv"
  ```
  Allowed fields: `data_root, product, avg` (`ALLPOINTS` or `DAILY`), `year, month, day, doy, yyyymmdd`.
- **Implementation:** HTTP via stdlib `urllib.request`. Network and clock are injected through
  `AeronetHooks(fetch, sleep, now)`, mirroring `stage.runner.Hooks`. The CLI is a Typer
  sub-app: `sarb-aod aeronet stage|audit -c run.yaml [--dry-run]`, idempotent and resumable.
- **Validity check before the atomic rename.** The service sends chunked responses with no
  Content-Length, so check structurally:
  - line 1 is `AERONET Data Download (Version 3 Direct Sun)`;
  - line 3 names the requested level (`Version 3: AOD Level 2.0` or `… 1.5`);
  - the body ends with a newline;
  - every data line has the header's field count.

  A 1-line body means no data: record an empty day in the manifest so it isn't re-fetched. A
  body starting with `<html>` is an error. The manifest records bytes, rows, sites and sha256.
- Each cache file holds one level. An AOD15 fallback is a separate product, and matchups carry
  `aeronet_level`. Both test periods have AOD20: 130 sites on 2008-07-01 (6.2 MB/day) and 258
  on 2020-09-08 (14.5 MB/day, about 435 MB for the month).

**Tests:**
- URL builder: end = next day `hour2=0`, including month and year rollover (2008-07-31 →
  2008-08-01; 2020-12-31 → 2021-01-01).
- A stdlib `http.server` in a thread serves the committed fixtures in `tests/data/aeronet/`
  (see its README).
- Empty, HTML and truncated responses, derived from the fixtures.
- Pacing via the injected `sleep`/`now`.
- One `network` test: 2008-07-01 GSFC includes rows after 23:00 UTC and no 2008-07-02 rows.

### Task 3.2: Parser and 550 nm conversion (`aeronet/parse.py`, `aeronet/spectral.py`)

- **Parse by header name, never by position.** Drop the duplicated `*_Empty` columns and
  `AERONET_Site_Name`. Read −999 (written as both `-999.000000` and `-999.`) as NaN, and
  combine `Date(dd:mm:yyyy)` + `Time(hh:mm:ss)` into UTC.
- Carry `AERONET_Site`, `Site_Latitude(Degrees)`, `Site_Longitude(Degrees)`,
  `Site_Elevation(m)`, `Data_Quality_Level`, every `AOD_<λ>nm`, `440-675_Angstrom_Exponent`,
  `440-870_Angstrom_Exponent`, and `Solar_Zenith_Angle(Degrees)` when present.
- **The daily (AVG=20) layout differs:** 82 columns, no solar zenith, no exact wavelengths,
  added `N[...]` counts, and Time fixed at 12:00:00. The service also returns the day before and
  the day after, so filter Date == D.
- **550 nm, configurable (`aeronet.spectral`):**
  - `quadratic_loglog` (default; Eck et al. 1999, as used by Levy et al. 2010). Per
    observation, fit ln τ = a0 + a1·ln λ + a2·(ln λ)² by least squares over every valid
    `AOD_<λ>nm` with 340 ≤ λ ≤ 1020 nm and τ > 0, requiring ≥ 3 wavelengths with at least one
    below and one above 550 nm.
  - Otherwise, if ≥ 2 valid wavelengths ≥ 550 nm exist (smoke rows where Level 2.0 drops 440 and
    500 nm at very high AOD, e.g. NASA_Ames on 2020-09-08), extrapolate log-linearly from the two
    shortest and flag the value `extrapolated`. Otherwise NaN, flagged `missing`.
  - Convert each observation first, then average over the matchup window.
    `aeronet.include_extrapolated` (default true) and reports also show the results with those
    points excluded.
  - `angstrom_500` (CAM7): τ₅₀₀·(550/500)^(−α₄₄₀₋₆₇₅). It needs AOD_500nm > 0 and a valid
    AE, and drops about 16% of rows on 2008-07-01 (sites without 500 nm).
- **AERONET σ:** σ_aer = √(σ_inst² + s²), with σ_inst = 0.01 (config; Level 2.0 uncertainty
  0.01–0.02, Eck et al. 1999 via Levy et al. 2010) and s the sample std of the 550 nm values in
  the matchup window (0 if N = 1). Report the std(z) sensitivity to σ_inst = 0.02.

**Tests** (on the committed fixtures):
- the GSFC full-day parse (34 rows, 10:38–23:45 UTC);
- Avignon (no 500 nm): quadratic works, `angstrom_500` gives NaN;
- the AERONET-OC sites (Helsinki_Lighthouse, Galata_Platform: 443/490/667/865 nm) interpolate;
- the NASA_Ames smoke rows are flagged `extrapolated`;
- the two methods agree on pure power laws;
- a synthetic daily-layout file.

### Task 3.3: Metrics and grouped statistics (`evaluation/metrics.py`, `calculator.py`)

- **Convention** (as DAVINCI and CAM7): x = AERONET (reference), y = satellite or gridded
  product. MB = mean(y − x); bias labels read `<product> − AERONET`.
- **Port the definitions unchanged**, replacing `davinci_monet.core.registry` with a local dict
  registry. Note that:
  - RM and RMdn = mean/median(AERONET/product);
  - NMB, NME, MNB and MNE normalize by AERONET;
  - R2 = Pearson r².

  Gates are evaluated on unrounded values; tables round to 4 decimals.
- **Aerosol metrics:**
  - `%EE`: the percentage of matchups with lower ≤ (y − x) ≤ upper, plus the fractions above
    and below. The envelope uses the AERONET τ and a separate table, `evaluation.ee`:

    | Algorithm | Envelope | Source |
    |---|---|---|
    | DT land | ±(0.05 + 0.15τ) | Levy et al. 2013 (MODIS and VIIRS DT) |
    | DT ocean | +(0.04 + 0.10τ) / −(0.02 + 0.10τ) | Levy et al. 2013 (MODIS and VIIRS DT) |
    | DB land | ±(0.05 + 0.20τ) | Sayer et al. 2019 |
    | VIIRS DB water | ±(0.03 + 0.10τ) | Sayer et al. 2018 |

  - OLS and reduced-major-axis regression;
  - bias binned by AERONET AOD;
  - uncertainty calibration: z = (y − x)/√(σ_sat² + σ_aer²), reporting std(z) and the
    fractions with |z| < 1 and < 2 (ideally ≈ 1, 68% and 95%).
- **Grouped statistics** (calculator port: `min_samples` 3, rounding 4). Groups:
  configuration (QC preset, weighting, ρ), sensor and stream, product collection, surface,
  region, season, day, site, AOD bin, and the Southern Ocean band.
- **Daily validation and regional aggregation** (user revision, 2026-10-11):
  1. Calculate the AERONET daily mean independently from all eligible observations in the
     UTC day. Calculate the satellite daily mean at the site's nearest cell using valid
     window fields weighted by their observation counts. Join only after these means exist.
     Three-hour coincidence is not required: Terra and Aqua do not sample an entire
     three-hour interval, and their merged daily product has no single overpass time.
  2. For each region-day, average its paired sites equally and compute metrics over days.
     This retains CAM7's equal-site regional aggregation while replacing the old
     preselected three-hour pairs. Report N days, site-days and active-site counts.
     These daily regional means drive acceptance and the regional time-series plots.
  3. Pooled site-day statistics retain local errors as diagnostics. Historical pooled
     three-hour and individual-overpass statistics remain explicitly separate; their
     errors must not be presented as errors of the daily regional curves.

**Tests:** hand-computed expected values from the DAVINCI formulas, committed in the test (no
`davinci_monet` import, since it isn't installed anywhere); asymmetric EE; RMA on exact lines;
z calibration on synthetic data with known σ.

### Task 3.4: Regions (`evaluation/regions.py`)

DAVINCI's `AEROSOL_REGIONS` (`origin/develop:davinci_monet/geography/domains.py`; extents
lon_min, lon_max, lat_min, lat_max; aliases kept), which match Table 1 of Bhattacharjee et al.
(2018, doi:10.5194/gmd-11-2333-2018), with CAM7's display names:

| Key | Display name | Regime |
|---|---|---|
| `north_atlantic_ocean` | North Atlantic Ocean | dust outflow, marine |
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

- **Additions:**
  - `global`;
  - `southern_ocean` (< 40° S; clean marine, where the noise problem is);
  - `labor_day_2020_smoke`: default extent (−125, −116, 36, 49), active 2020-09-08 to 09-14.
    Its display name is **"Labor Day Smoke"**, with no year, because the plot protocol rejects
    years in titles. Task 2.4 may refine it in config.
- **Membership:** a site belongs to every region whose rectangle contains it (inclusive bounds,
  longitude normalized to [−180, 180)), as in DAVINCI and CAM7.
  - Regions overlap (9 sites sat in two regions on 2020-09-08), and 103 of 258 sites were in none.
  - `global` is computed separately, never as a sum of regions.
  - No land mask is applied to the "ocean" rectangles; the surface grouping covers that.

### Task 3.5: Collocation (`evaluation/collocate.py`)

Matchup tables are written as NetCDF under `paths.evaluation_dir` (default
`{data_root}/evaluation/{run}`).

- **L2 stream level** (Levy et al. 2013 / Petrenko et al. 2012 MAPSS; verified):
  - Select satellite pixels whose centers are within `radius_km` (25) great-circle km of the
    site, requiring ≥ `min_pixels` (3) QC-passed pixels.
  - Require ≥ `min_aeronet` (2) AERONET observations within ±30 min of t_sat, the mean scan time
    of the selected pixels.
  - sat = mean of the pixel AODs (also median, std, N); aer = mean of the per-observation 550 nm
    values (also std, N).
  - Load AERONET for D−1, D and D+1 so windows crossing midnight are complete.
  - z at L2: σ_sat = mean of the selected pixels' σ (not divided by √N, because pixel errors are
    correlated); σ_aer per Task 3.2.
  - Surface class from the selected pixels: land, ocean, or coastal if mixed. One row per site ×
    overpass × stream.
- **Grid level (`--pairing daily`, default CLI grid evaluation):**
  - The cell containing the site: latitude index = nearest of the 192 centers; longitude index =
    argmin |((lon_site − lon_c + 180) mod 360) − 180| (DAVINCI `_find_nearest_1d`,
    longitude=True).
  - **No radius cut.** DAVINCI `PointStrategy`'s default 12 km radius would drop almost every
    site on ~100 km cells, so port only the index logic.
  - Independently average AERONET 550 nm observations in [UTC midnight, next midnight),
    requiring ≥ `min_aeronet_grid` (2) valid observations. Count-weight the valid cell AOD
    fields from all eight daily output windows; do not require AERONET in those windows.
    Preserve observation and contributing-window counts. One row per paired site × day × run.
  - Do not infer daily satellite uncertainty from one window or independent-error division
    by √N: its temporal covariance is unspecified, so daily σ_sat and z remain unavailable.
  - `--pairing default` explicitly retains the historical three-hour comparison as a
    diagnostic in separate archives. It cannot determine acceptance of the daily product.
- **`--pairing cam7` regression mode:**
  - AERONET hourly site means (UTC hour bins) of AOD_500nm and the 440–675 AE, converted with
    `angstrom_500`;
  - each site-hour paired with the nearest f09 cell of the 3-hour window containing it, with no
    minimum counts.
  - **Gate:** if the user copies CAM7's July 2008 SP1 files from derecho (path to be supplied),
    this mode reproduces N = 8875 and MB/RMSE/R to ±0.001. Otherwise record the difference in
    Open items.
- **Representativeness flags:** `Site_Elevation(m)` > 1000 (`high_elevation_m`); more than one
  active site in the same cell and window; and coastal sites, whose majority L2 class over the
  period is mixed.

**Tests:** synthetic swaths and grids with planted values at known sites; radius and time
windows at their boundaries; dateline and polar cells; AERONET D±1 loading; no double counting
across windows.

### Task 3.6: Reports (`evaluation/report.py`, `sarb-aod evaluate`)

- `sarb-aod evaluate -c run.yaml --level {l2,grid} [--pairing daily|default|cam7]` builds the matchups,
  computes grouped statistics, and writes CSV and JSON tables (DAVINCI formatter) with the
  provenance of every input.
- **Acceptance gates** (user revision, 2026-10-11): apply |MB| ≤ 0.03, RMSE ≤ 0.10 and R ≥ 0.8
  to the **independent daily regional means**, using the same values shown in the regional
  time-series plots. The run-level gate uses equal-weight daily averages across the global set of sites;
  region-specific gate results are also reported. Global here describes available AERONET
  sites, not an area-weighted whole-Earth sample. Each day has equal weight in the metrics.
  - Require at least `evaluation.min_samples` matched days (default 3). Undefined metrics
    are insufficient data, never a pass. Tests use unrounded values.
  - Pooled site-day errors and historical three-hour matchups are diagnostics, not these
    acceptance gates. Averaging can cancel local errors, so retain those diagnostics.
  - L2 overpass validation retains ±30-minute reference matching, with explicitly named
    overpass statistics. It is a separate comparison from the merged daily product.
  - CAM7's published July SP1 statistics (N=8875, MB=0.0195, RMSE=0.1464, R=0.8555) use a
    different sampling basis. Keep `--pairing cam7` as a historical regression, not a
    numerical target for daily regional acceptance; identical input files remain unavailable.
  - Grid CLI evaluation defaults to `daily`; L2 defaults to the existing overpass pairing.
    Save daily archives/reports separately and preserve historical diagnostic evidence.
- **Plots** come from the Task 4.5b suites.

### Task 3.7: Evaluation campaign and default selection

1. Stage AERONET AOD20 for July 2008 and September 2020.
2. **L2 level:** each stream under `retrieval_team` and `cam7_v2`; DT ocean QA ≥ 1 vs = 3;
   Average vs Best ocean; NOAA-20 DB v2 vs v2.1; C6.1 vs C7 once available.
3. **Grid level:** weighting (`count`; `gls` with ρ ∈ {0, 0.25, 0.5, 0.75, 1}); DT-land
   negatives (`aod_min` −0.05 vs 0); `min_pixels` ∈ {1, 3, 5}; MODIS-only vs VIIRS-only vs
   mixed (Sept 2020).
4. **Choose ρ** where std(z) ≈ 1 without losing RMSE; set the other defaults from the tables.
   Separate reports for the Southern Ocean band and the smoke box.
5. Record the evidence and chosen defaults in `docs/validation/`; update `AGENTS.md`.

The original campaign is retained as historical diagnostics. Task 4.8 reruns the
24 grid configurations (including the separately plotted CAM7-v2 control) with
independent daily pairing and regional daily acceptance. Both baseline global
comparisons pass; six global configurations and some regions still fail individual
criteria. Daily uncertainty calibration needs a temporal covariance model and is
not inferred from the historical three-hour z statistics.

---

## Phase 4: Plotting and diagnostics (DAVINCI style)

Publication-quality plots following DAVINCI's plotting practice, ported into
`sarb_aod/plots/`. This is standalone: no `davinci_monet` or monetio import. matplotlib 3.11,
cartopy 0.25 and seaborn 0.13 are in the env (Task 0.1). Natural Earth 110m and 50m coastlines
are prefetched to `~/Data/cartopy`.

| DAVINCI (`origin/develop`) | What we take |
|---|---|
| `plots/style.py` | `apply_ncar_style` (font contexts, 300 dpi, `bbox=tight`, seaborn whitegrid), NCAR colors and palette, `geosit_aod_levels()`, `get_geosit_aod_cmap()` (`turbo`), `get_bias_cmap()` (`RdBu_r`), `get_sequential_cmap()` (`viridis`) |
| `plots/labeling.py`, `plots/labels.py`, `plots/titles.py` | all text composition; `calculate_symmetric_limits` (`labels.py:400`); `title_for_labeled_subset` |
| `plots/_stats.py` | `annotation_metrics` (stats box), bound to `sarb_aod.evaluation.metrics` |
| `plots/renderers/spatial/{base,field,bias}.py`, `plots/suites.py` (`_AOD_MAP_CONTEXT`, `gridded_aod_diagnostics`) | `draw_spatial_field`, global-mean subtitle, `_nice_robust_limits`, map context, suite field options |
| `plots/renderers/aod_correction_science.py` | multi-panel AOD maps (shared levels, `_AOD_COLORBAR_TICKS`, `_map_panels`), the reference `validate_rendered_figures` with `_layout_failures`, `_record_dense_artists`, `_reserve_publication_layout` |
| `plots/base.py:154` (hook), `pipeline/stages/plot.py:379` (called before saving; failure raises), `inspection/core.py` | protocol hook contract and report handling |
| `plots/renderers/{timeseries,scatter,scorecard,taylor,boxplot}.py` | line, scatter and summary figures |
| DAVINCI `CLAUDE.md` § Plot Label & Title Conventions and § Plot Protocol Enforcement; `docs/superpowers/specs/2026-06-15-plot-labeling-system-design.md` | rules |

### Rules (enforced by the protocol and tests)

**AOD color scale.**
- `geosit_aod_levels(values)` depends on the data: 0.05 bins up to ceil(min(max, 1.0)/0.05)·0.05
  (never above the 1.0 cap), plus 0.005–0.04 below 0.05.
- Compute the levels **once per figure**, from the concatenated finite values of all its AOD
  panels. Suites that compare products or days use the fixed full set `geosit_aod_levels([1.0])`,
  so every map shares one scale.
- Use `turbo` with `BoundaryNorm(levels, cmap.N, extend="max")`. Colorbar ticks are DAVINCI's
  `_AOD_COLORBAR_TICKS` (0, 0.02, 0.05, 0.10, 0.20, 0.50, 0.80, 0.95), truncated at the top level.
- Never use ad-hoc min/max scaling for AOD.
- Negative AOD falls in the lowest bin and is accepted on absolute maps. Negatives are shown
  instead by difference maps and by the **Negative AOD Fraction** map (from
  `aod_count_negative`).

**Other fields.**
- Differences and biases: `RdBu_r`, robust symmetric limits (`calculate_symmetric_limits`, 98th
  percentile), `TwoSlopeNorm` at 0.
- Coverage and other fractions: `gray_r` on fixed [0, 1] (DAVINCI `gridded_aod_diagnostics`).
- Counts, uncertainty, χ²/ν and per-site RMSE: `viridis` with nice robust limits (2–98%), lower
  bound 0.
- Per-site R: `viridis` on [0, 1].

**Rasterization (PDF).**
- Rasterize the dense filled layers: pcolormesh, contourf, fill_between and large scatters.
  Keep text, axes, colorbars and contour *lines* vector.
- On matplotlib 3.11, `set_rasterized(True)` on a line `ContourSet` is accepted silently, so the
  protocol checks that every line `ContourSet` (`filled is False`) is **not** rasterized and
  every dense artist **is**.
- **Gridded AOD fill:**
  - default `pcolormesh` with `shading="flat"` on the f09 cell edges from `lat_bnds`/`lon_bnds`
    (clipped to ±90; longitudes shifted to −180..180 and sorted with the data columns);
  - optional `contourf`, which on GeoAxes first converts longitudes to −180..180, sorts them,
    and closes the seam with `cartopy.util.add_cyclic_point`. Without this, shapely emits
    "invalid value encountered in create_collection" and a blank dateline strip appears.

**Titles** (DAVINCI decision D6).
- Terse: the quantity, plus an operation word only when it changes meaning (`AOD Bias`,
  `AOD Difference`). Scatter, time-series and histogram titles carry **no** plot-type word.
- Single-source titles put the source first (`MODIS AOD`, `AERONET AOD`).
- Per-site and per-region figures use `title_for_labeled_subset`: `AOD - Mauna Loa`,
  `AOD - Western US`. Site underscores become spaces.
- Title case, preserving acronyms (AOD, MODIS, VIIRS, AERONET, CAM, SNPP, NOAA-20).
- **Never in titles:** dates or years, statistics, `Mean ± Std`, `vs <source>`, raw variable
  names, internal labels (task numbers, `gls`, `rho`, `cam7_v2`), or `x`/`y`.

**Subtitles.**
- `subtitle_text` dates (`YYYY-MM-DD – YYYY-MM-DD`).
- Maps add ` | Mean: <value>`, the cos-latitude mean of finite cells (over the region's cells
  for regional panels).
- Site counts as ` | Sites: 12`.
- Configuration appears only when the audience needs it; it is always in the filename.

**Labels.**
- Only through `labeling.py`.
- Title Case for labels, legend entries and annotations; preserve acronyms, unit spelling
  (including `nm`) and lowercase particles such as `of`.
- Single-source map colorbars show `format_units(units)` only, so absolute AOD maps have no
  colorbar label.
- Scatter axes carry the source (`axis_label(q, units, source)`); time series carry sources in
  the legend.
- Append `(mean ± σ)` to a legend entry only when its band is drawn.
- `bias_label` produces `<Y> − <X>` (U+2212).
- Regression legends read `OLS: slope 1.02, intercept 0.01` / `RMA: …`, never `y = …x`.
- A whole-key display table, checked before token-wise `source_display_name`, covers every
  configuration and product key a figure can show. Examples:
  - `count` → Count-Weighted; `gls` → GLS; with ρ → GLS (ρ = 0.5);
  - `retrieval_team` → Retrieval-Team QC; `cam7_v2` → CAM7 QC;
  - `c61` → C6.1; `c7` → C7; Deep Blue v2 and v2.1;
  - `modis`/`viirs`/`mixed` → MODIS / VIIRS / MODIS + VIIRS;
  - all eight streams, e.g. `noaa20_db` → NOAA-20 Deep Blue.

  Tests assert that no figure text contains a raw config key.

**Maps.**
- DAVINCI `_AOD_MAP_CONTEXT` exactly: coastlines, solid gridlines labeled left and bottom, no
  countries or states, land and ocean fill `none`.
- Global PlateCarree by default; regional extents only when a suite asks for a region.
- Single maps: `tight_layout(pad=0.5)`, saved with `bbox_inches=fig.bbox_inches` (DAVINCI
  `BaseSpatialPlotter.save`). Panels: constrained layout with one shared horizontal colorbar
  (`_map_panels`).
- `plots.coastline_resolution`: 110m for global maps, 50m for regional ones.

**Time series.** Regional date ticks rotate 20° (user revision, 2026-10-11); site date ticks
retain 45°. Regional titles read `AOD 550 nm - <Region>` directly above the axes on the
left, with the subtitle on the right: space-separated latitude/longitude ranges before the dates
and active-site counts, all on one line. The vertical AOD axis also specifies
550 nm. `Time` sits inside the axes just above the bottom border; dates remain outside.
Both footer lines align to the plot's left edge; the band/error definitions share
one line below the statistics. AERONET in NCAR gray (`X_COLOR`), satellite in NCAR
blue (`Y_COLOR`), extras from `NCAR_PALETTE`. Positive bias red, negative blue.

**Output.**
- PDF deliverables with PNG previews.
- `paths.plot` = `{data_root}/plots/{run}/{suite}/{name}.{start}-{end}.pdf` (fields
  `data_root, run, suite, name, start, end`; start = end for daily maps); the preview is the
  same path with `.png`.
- PDF provenance uses only the standard Info keys (custom keys raise UserWarning): `Title`,
  `Subject` (suite/name, period), `Keywords` (`weighting=…; rho=…; qc=…; collections=…;
  config_sha256=…; git_commit=…`), `Creator` (`sarb_aod <version>`).
- Remove stale PNGs when replacing products.

### Task 4.1: Style, labeling, display tables

- **Port `style.py`, `labeling.py`, `labels.py` and `titles.py`, standalone:**
  - inline `paired_canonical_name` (DAVINCI `core/base.py:38-56`);
  - drop `surface_index` and `get_domain_extent`, taking extents from `evaluation/regions.py`;
  - extend the acronym tables (VIIRS, Terra, Aqua, SNPP, NOAA-20, AERONET, CAM, Dark Target,
    Deep Blue);
  - add the whole-key display table (Rules).
- **Config:** `paths.cartopy_data_dir` (profile key; mac `{data_root}/cartopy`, already
  prefetched) is applied as `cartopy.config["data_dir"]` before any map is drawn.
  `sarb-aod plot --prefetch-maps` downloads the 110m and 50m `physical/coastline` files; it is
  the only network step.
- **`pyproject.toml` `[tool.pytest.ini_options]`:** `filterwarnings = ["error::UserWarning",
  "error::cartopy.io.DownloadWarning", "ignore:The locs attribute was deprecated in Matplotlib
  3\\.11.*:matplotlib.MatplotlibDeprecationWarning:cartopy\\.mpl\\.ticker"]` (DAVINCI's filters
  plus DownloadWarning). Set `MPLCONFIGDIR=$PWD/.cache/matplotlib` in sandboxes.
- Poppins isn't installed; the fallback is Helvetica Neue (no warnings).

**Tests:** port DAVINCI `test_labeling.py` cases; title rejected patterns (dates/years, `vs`,
raw keys, `x`/`y`, internal labels); `geosit_aod_levels` (26 levels when max ≥ 1); units;
the display table.

### Task 4.2: Map renderers (`plots/maps.py`)

- `draw_spatial_field` with pcolormesh (flat, on bounds) or contourf (cyclic) for grids, and
  rasterized scatter for points.
- Global-mean and regional-mean subtitles; robust, symmetric and nice limits; GEOSIT colorbar
  ticks.
- Multi-panel layout with shared levels.
- Site-marker layer for AERONET maps, with markers scaled by N within a fixed range.
- Regional extents from Task 3.4.

**Tests:**
- Artists: BoundaryNorm with shared GEOSIT boundaries and `turbo`; `TwoSlopeNorm`/`RdBu_r` for
  differences; `gray_r` [0, 1] for fractions; rasterized mesh; vector text; coastlines present;
  no borders feature.
- The PDF contains at least one image XObject that is not an SMask; the vector version contains
  none.
- contourf on a synthetic f09 field raises no warning under `-W error` and leaves no blank
  column at ±180°.
- No `DownloadWarning` offline.

### Task 4.3: Series, scatter and summary renderers (`plots/series.py`, `scatter.py`, `summary.py`)

- **Time series:**
  - per site: AERONET all points (light) and window means (bold); the gridded value at the
    site's cell for **every** window, matched or not; L2 overpass means as markers;
  - regional: Task 3.3 method (2), with paired site-spread bands and daily-mean
    statistics as specified in Task 4.5b (user revision 2026-10-11).
- **Density scatter** (rasterized): 1:1 line, EE envelope lines, OLS and RMA regression, and a
  stats box (N, MB, RMSE, R, %EE).
- **Summary figures:** binned bias against AOD, z histogram with a unit-normal reference, ρ-sweep
  curves, region × metric scorecard, Taylor diagram, regional box plots.

**Tests:** artist and label checks per figure type; 20° regional and 45° site date ticks; stats-box values equal
`evaluation.metrics`.

### Task 4.4: Plot protocol (`plots/protocol.py`)

- Port DAVINCI's `validate_rendered_figures` pattern. Called before saving, it raises on
  failure and writes a JSON report per suite (`{protocol, passed, figures, checks}`).
- **Layout:** draw the canvas and reject clipped or overlapping titles, subtitles, panel
  headings, axis labels, legends, colorbar ticks, each axes' `ax.texts` (map subtitles) and
  cartopy gridliner labels.
- **Rules:**
  - norms and colormaps per field kind;
  - shared AOD `BoundaryNorm` boundaries within a figure;
  - dense layers rasterized and line `ContourSet`s not;
  - title, label and display-name patterns.
- **Inspection gate:** view every period figure, the daily maps for three named days per period
  (July 2008: 07-01, 07-15, 07-31; Sept 2020: 09-01, 09-09, 09-30), and every figure the
  protocol flags. A passing report is necessary but not sufficient.

**Tests:** a negative case per rule (including a rasterized contour line set and mismatched AOD
levels) proving that the suite fails.

### Task 4.5a: Map suites and `sarb-aod plot` (inputs: CAM7 files only)

```yaml
plots:
  context: publication       # default | presentation | publication
  aod_fill: pcolormesh       # or contourf
  coastline_resolution: 110m
  suites: [aod_maps, aod_differences]
  regions: all               # or a list of region keys
```

| Suite | Figures |
|---|---|
| **`aod_maps`** (spatial patterns) | **Period maps** (one figure each per run): Mean AOD (count-weighted over windows, CAM7's monthly definition) for each sensor and stream, plus the combined product only for mixed-sensor runs. Aggregates sit at left; platform rows align vertically within Dark Target and Deep Blue columns, sharing one GEOSIT scale. Global figures use panel titles without a redundant quantity heading; regional figures retain their region heading. Uncertainty (count-weighted mean of window `aod_uncertainty`); Observation Count (sum of `aod_count`); Observation Fraction (fraction of windows with data); Stream Disagreement (count-weighted mean of `aod_chi2_per_dof`); Negative AOD Fraction. **Daily maps:** product Mean AOD only (count-weighted over the 8 windows; cells without observations left blank), one figure per day. Global by default; per-region panels when `regions` is set. |
| **`aod_differences`** | Run and product differences: `gls` − `count`, `retrieval_team` − `cam7_v2`, ρ variants, C7 − C6.1, NOAA-20 DB v2.1 − v2, VIIRS − MODIS. Diverging, symmetric robust limits, `bias_label` colorbars with display names. |

**Tests:** suite expansion to its logical product list; an end-to-end render on synthetic CAM7
files passes the protocol; filenames carry period and run.

### Task 4.5b: AERONET suites (inputs: CAM7 files, Task 3.5 matchups, parsed AERONET cache)

```yaml
plots:
  suites: [aeronet_site_bias, aeronet_site_timeseries, aeronet_regional_timeseries,
           aeronet_scatter, evaluation_summary]
  sites: {select: top_n, n: 20}   # top_n = most grid-level matchups in the period, ties by name; or a list
```

| Suite | Figures |
|---|---|
| **`aeronet_site_bias`** | Site markers colored by mean bias (`<product> − AERONET`), plus per-site RMSE and R maps; per level (L2, grid) and run; global and per region. |
| **`aeronet_site_timeseries`** | One figure per selected site (`AOD - <Site>`): AERONET 550 nm from the parsed cache (all points light, window means bold); the gridded value at the site's f09 cell (Task 3.5 index logic) for every window; L2 overpass means as markers; one line per sensor or stream when requested. |
| **`aeronet_regional_timeseries`** | Per region (`AOD - <Region>`): independently calculated daily AERONET and satellite means paired by site-day, then equal-site regional averages (Task 3.3). Overlapping translucent bands show the 25th–75th percentiles across paired sites for each source. The full-region satellite curve is removed by user direction (2026-10-11). Missing days break lines and bands; one-site days have no band, and isolated multi-site days retain a short visible band. Titles and date/site-count subtitles are close together. A region/site inset stays fully inside the plot box, choosing among four corners, top center and bottom center for the most free space among the curves, bands and legend; bottom-center placement clears the Time label, and it remains beneath the bands and curves, with coastlines clipped to the exact region box and no geographic padding. Footer statistics use the displayed paired region-days: N days, MB, RMSE, R and NME = 100 × mean absolute difference / mean AERONET; `evaluation.min_samples` applies. These daily comparisons are the acceptance basis. Explicit sensor/stream requests produce separate companion figures with each component's own paired AERONET daily sample. |
| **`aeronet_scatter`** | Density scatter per run, per region (small multiples), and per stream at L2. |
| **`evaluation_summary`** | Binned bias, z histograms, ρ-sweep curves, region × metric scorecard, Taylor diagram, regional box plots. |

**Tests:** as in 4.5a, on synthetic matchups and fixtures.

### Task 4.6: Full plot runs (July 2008, Sept 2020)

1. **July 2008:** all suites for the four Task 1.10 runs; one of them is the CAM7 reproduction
   (`count` + `cam7_v2`).
   - Its acceptance is numeric: monthly Mean AOD by CAM7's analysis definition meets VP1
     (mean 0.1–0.25, values in [0, ~3]).
   - If the user copies CAM7's July 2008 f09 files to the Mac, the difference map against them
     is zero to float32 tolerance.
   - Visual comparison with derecho PDFs is optional (user).
2. **Sept 2020:** all suites, plus smoke-box maps and time series using the Task 2.4 extent,
   with MODIS-only, VIIRS-only and mixed side by side.
3. **Southern Ocean focus:** maps and regional time series for `southern_ocean`, comparing DT
   ocean QA ≥ 1 vs = 3 and the negative-AOD settings. This is the evidence base for the
   deferred denoising work.
4. Pass the inspection gate. Record the accepted plot set in `docs/validation/`.

---

## Phase 5: Production (AMI)

Needs from the user: AMI paths and archive layout, scheduler, Python/conda availability, and
network access to LAADS and AERONET.

- Fill in `config/profiles/ami.yaml`, including `paths.cartopy_data_dir` with prefetched
  coastlines.
- Task 5.1 provides `sarb-aod grid-months`, preserving the original processing
  configuration in every daily worker, with monthly summaries, atomic metrics,
  resume/failure handling, and collision checks. See `docs/AMI_PRODUCTION.md`.
  Measured output sizes are 4.04 MiB/file for MODIS and 5.74 MiB/file for mixed
  streams (11.53/16.36 GiB per 365-day run), replacing the preliminary ~1 MB estimate.
- Task 5.2 confirms the site-specific profile and scheduler, measures AMI throughput,
  and executes the requested multi-year production period after those details arrive.

---

## Implementation evidence

- Task 1.1: MODIS C6.1 recipes and named-dimension selectors pass synthetic and real
  Terra/Aqua checks. Both products use the `:mod04` wavelength dimensions, index 1
  for 550 nm. Collections without variable maps fail before opening a file.
  Offline tests, mypy, Black, isort, and `codex review --uncommitted` pass.

- Task 1.5: f09/file-grid validation, canonical bounds, UTC half-open windows and
  leap-correct TAI93 conversion pass 45 focused tests. Inserted leap-second instants
  become NaT/NaN because NumPy cannot represent 23:59:60. All offline gates and review pass.

- Task 1.6: DAVINCI ports retain their argument and axis order. The extended
  accumulator/count kernel uses half-open time and inclusive spatial edges;
  100,000 random pixels match an independent NumPy reference (integer exact,
  floating rtol/atol 1e-12). On macOS arm64, warmed one-million-pixel f09/four-stream
  runs took 0.0545, 0.0489, 0.0490 s: best 20.4 million pixels/s (target 10 million).
  Nine accumulators occupy 127.4 MB for four streams. Offline gates and review pass.

- Task 3.4: twelve DAVINCI aerosol rectangles/aliases and CAM7 display names,
  plus global, Southern Ocean (<40° S), and dated Labor Day Smoke region.
  Thirty-four tests cover overlapping membership, inclusive edges, normalized
  longitude, UTC activity dates, aliases, and explicit extent overrides.
  Offline gates and review pass; global membership is computed independently.

- Task 3.2: header-name parser and per-observation 550 nm conversion pass all fixture
  cases (GSFC 34 rows through 23:45 UTC; Avignon; ocean-color sites; NASA Ames smoke;
  daily layout). Quadratic fits use 340–1020 nm; reviewed extrapolation fallback accepts
  the two shortest valid wavelengths >=550 nm, including longer near-infrared bands.
  All 31 focused tests and repository gates pass; review findings affecting later
  output/reader tasks are tracked and fixed in those tasks.

- Task 1.2: retrieval-team/cam7_v2 presets, QA overrides and raw masking/scaling
  pass 41 focused cases. Inclusive packed endpoints survive retrieval-team rounding;
  cam7_v2 retains unrounded scaling and nonnegative AOD. On 16 real July 1 granules,
  default DT QC matches the combined product exactly (76,368 finite pixels).
  Repository gates and review pass.

- Task 1.3: separate MODIS/VIIRS DT envelopes and retrieval-provided DB sigma
  pass 13 focused cases. Missing/masked sigma stays NaN; valid negative DB envelopes
  use magnitude and the configurable floor. Supplied DT sigma takes precedence.
  Repository gates pass; review prompted an explicit masked-sigma regression.

- Task 1.7: count and correlated-error GLS combinations pass 52 focused tests,
  including heterogeneous-error Monte Carlo (20,000 realizations per case), rho=1
  saturation, Birge inflation, minimum counts and independent sensor grouping.
  Product counts are zero when no GLS estimate exists; raw stream/missing-sigma/
  high-rejection diagnostics remain visible in missing-product cells.
  Repository gates and review pass.

- Task 1.4: selective HDF4/NetCDF readers pass synthetic masking/scaling/group/
  dimension/time/overlap tests and real-granule checks (105,001 accepted pixels across
  16 MODIS granules and four streams). Missing DB sigma is retained for count weighting.
  Review's QA-count finding is fixed: a surface's QA diagnostic requires its own valid
  AOD field. Fourteen reader tests and repository gates pass.

- Task 1.8: 40 contract tests compare the vendored CAM7 writer's dimensions, dtype,
  fill, coordinates, CF metadata and required globals; our files pass its unchanged
  validator. Atomic replacement, clipping and corruption cases pass. Eight path tests
  verify run isolation and CAM namelist limits; review corrected the length check to
  use expanded dates/seconds. A staging omission required an immediate follow-up
  commit for the already-tested path helpers. Repository gates and review pass. The external derecho
  CAM `atm.log` consumption check remains an open deployment verification.

- Task 1.9: end-to-end HDF4 tests produce eight valid files, exercise multi-process
  days, preserve successful days on failure, repair corrupt or wrong-window files,
  and reject stale summaries. Resume uses a processing hash including resolved
  catalog recipes; evaluation/plot settings do not invalidate gridded values.
  Review findings about catalog changes and downstream provenance are fixed.
  Fifteen pipeline tests and repository gates pass.

- Task 3.1: 21 offline fetch tests plus the live GSFC full-day request pass.
  Caches are all-site, atomically published after structural validation, with per-day
  checksum manifests; site filters do not collide. Pacing covers retries and catalog
  lookups. July/September acquisition (with boundary days) is running in detached
  `sarb-aeronet`; its completion/audit belongs to Task 3.7. Repository gates and review pass.

- Task 2.1: shared VIIRS DB v2/v2.1 maps pass synthetic fill/overlap/uncertainty
  tests and six real preset/version checks. SNPP 2020-09-08 20:24 yields 55,748
  retrieval-team pixels; cam7_v2 yields 60,293 land + 14,125 ocean - 300 overlaps
  = 74,118. NOAA-20 v2/v2.1 both yield 34,110 default pixels in the checked granule.
  Full-month science verification remains Task 2.4. Repository gates and review pass.

- Task 3.3: all 27 ported DAVINCI metric formulas were checked against the
  reference source. Analytic, NaN, degenerate, grouped, asymmetric expected-error,
  regression and uncertainty-calibration cases pass. Regional statistics use
  equal-site daily means; exported rounding preserves UTC dates and never alters
  scientific gate inputs. Full offline suite: 784 passed; mypy/format gates pass.
  Review findings affecting shared dependencies are fixed.

- Task 3.5: radius/time boundaries, adjacent-granule overpasses, midnight
  neighbors, nearest polar/dateline cells, half-open grid windows, and CAM7
  hourly pairing pass synthetic tests. Matchups preserve both extrapolation
  sensitivities, uncertainty and representativeness flags. Review corrections
  enforce grid processing provenance and expected window time. The full offline
  suite and typing/format gates pass. The SP1 archive is absent locally; its
  known source is `/glade/work/fillmore/Data/CERES-SARB-CAM7/AOD_f09_3hr/`, so
  N=8875 baseline reproduction remains explicitly unverified.

- Task 4.1: standalone DAVINCI colors/fonts/GEOSIT levels, centralized
  labels/titles/display names and local Cartopy cache configuration are in place.
  UserWarnings and coastline download warnings fail tests. The available Helvetica
  Neue fallback renders cleanly; all renderer types pass protocol checks and
  previews were inspected. Full suite: 809 passed; typing/format gates pass.
  Review found no style/labeling defects.

- Task 4.2: pcolormesh, cyclic contourf, panels and site maps use the
  prescribed scales, vector coastlines/labels and rasterized dense fills. Twenty-two
  map tests cover dateline closure, exact f09 bounds, shared scales, weighted
  subtitles, warning-free offline coastlines and PDF raster/vector content.
  Synthetic and real-data previews were inspected; protocol and repository gates
  pass. The review found no map-renderer defects.

- Task 4.3: time series retain independent grid windows and raw AERONET
  observations; scatter panels expose family EE and OLS/RMA; summary renderers
  cover binned bias, z, rho, scorecards, Taylor and regional boxes. Analytic
  renderer tests and the protocol pass; previews for all nine figure types and
  scatter panels were visually inspected. Review and repository gates pass.

- Task 4.4: every figure is drawn and audited for titles, display names,
  text/legend layout, dense rasterization, vector line contours/axes/colorbars,
  field norms and shared AOD levels/ticks. Failure reports are written before
  raising. Eighty-three positive/negative protocol tests cover every rule and
  all renderer families, including real cached coastlines. Full repository gates
  and review pass; new suite integration is tested separately.

- Task 4.5a: the public plot command writes configured PDF/PNG artifacts
  and passing JSON protocol reports. Period and daily maps use count weighting
  and a shared fixed GEOSIT scale; comparisons use coincident cells/windows.
  Nineteen suite/CLI tests and full gates pass. Review caught the missing public
  command registration; it is fixed and tested through the real CLI. All 152
  July map artifacts were rendered with passing protocols; preview inspection
  and campaign acceptance are tracked under Tasks 1.10/4.6.

- Task 2.2: both VIIRS DT platforms now use named trailing wavelength
  dimensions, group-aware fields and six-minute UTC coverage row times. Real
  SNPP/NOAA-20 smoke granules reproduce the combined SDS at the prescribed
  land/ocean QA and share DB three-hour windows; malformed interval tests fail
  loudly. Native Best Ocean is absent, so CAM7 preset keeps Average Ocean.
  Reader, catalog, repository gates and review pass.

- Task 2.3: September configs select MODIS-only, VIIRS-only, mixed, and
  mixed NOAA-20 DB v2.1 streams without code changes. Eight synthetic end-to-end
  tests verify collection identity, no double counting, sensor fills and mixed
  mean convexity under GLS/count weighting. September staging recovered four
  transient DNS failures and all 29,856 files now audit successfully. Full
  gridding/science comparisons are Task 2.4; repository gates and review pass.

- Task 1.10: all 992 July files pass contract, processing-provenance and
  expected-window validation. Three real granules, including 23:55, reproduce
  the vendored CAM7 diagnostic script with exact counts and float32 tolerance.
  All 152 map pairs pass seven protocols; required previews were inspected.
  The four monthly global means meet 0.1–0.25, but written cell-window p99.9
  is 3.176–3.489 and coverage reaches 15.05%. Investigation found both isolated
  and well-populated high cells; no unsupported filtering was added. Full
  repository gates and review pass. See `docs/validation/july2008_verification.md`
  and its numeric/artifact inventories. Verification is complete; the original
  VP1 acceptance is not claimed.

- Task 2.4: all 112 requested smoke-week windows are checked for both mixed
  versions. DB p90 exceeds DT on every platform, plume disagreement exceeds
  one, and all 404,399 mixed/sensor comparisons obey convexity. NOAA-20 DB
  v2.1 retains 6.70% fewer smoke-box pixels than v2. Native valid ranges cap
  AOD at 5, so the requested strictly-above-5 rejection gate is structurally
  unmet (850 valid endpoint pixels in the baseline). The verifier distinguishes
  complete checks from science acceptance, validates cached retrieval definitions
  and digests, and passes analytic/regression tests, full gates and review.
  See `docs/validation/sept2020_verification.md` and its exact tables/JSON.

- Task 5.1: monthly orchestration passes ten tests covering calendar boundaries,
  resume, partial failures, configuration identity, path collisions and CLI exit
  status. Fresh one-day development benchmarks produced eight validated files
  each: 310 MODIS granules in 5.165 grid seconds and 865 mixed granules in
  25.354 seconds. Storage measurements use audited manifests and real outputs.
  Full tests, typing, format checks and review pass. These measurements do not
  claim AMI performance or deployment; site-specific work remains Task 5.2.

- Task 4.5b: five AERONET suites retain independent grid/AERONET series,
  overpass markers, sampled-site regional means, EE/scatter diagnostics and
  uncertainty summaries. Verified matchups reject stale processing definitions;
  default/CAM7 and L2/grid evaluation paths have distinct artifacts and protocols.
  Review fixes preserve complete spectral-sensitivity candidates, join hourly
  CAM7 samples by containing window, prevent slug collisions, and fail the final
  suite protocol correctly. Real monthly runs exposed legacy numeric-object
  serialization and crowded geographic labels; both have regression coverage.
  The first July baseline set has 138 inspected figures and five passing suite
  protocols. Full gates and review pass. Supporting report calculations and
  loaders are included here; the public evaluation driver is Task 3.6.

- Task 3.6: the public evaluation command writes matchup NetCDF, rounded CSV
  presentation tables and full-precision JSON tables/gates with input hashes.
  Reports distinguish scientific failure from input errors and preserve failed
  gate evidence. Compatible L2 reuse verifies recipes, QC, time, uncertainty,
  matching settings and spectral-cohort completeness; exact stream subsets
  recompute representativeness flags. NumPy metadata and legacy numeric/boolean
  fields are handled explicitly. Review corrections are covered by regression
  tests; 879 offline tests, typing and format gates pass, and the live AERONET
  test passes. Real July default and CAM7 pairings are reported; the SP1 archive
  remains unavailable and no identical-input baseline reproduction is claimed.

- Task 3.7 (historical sampling; acceptance superseded by Task 4.8): all 32 primary
  reports (23 grid, nine L2) and all 23 grid summaries
  are verified against processing configuration, archive checksums and output
  arrays. Full-precision global, southern latitude-band and smoke comparisons,
  spectral extrapolation/instrument uncertainty sensitivities, and control
  configurations are recorded in `docs/validation/evaluation_campaign.md` and
  its JSON/TSVs. Negative-AOD evidence corrects the earlier MODIS-only claim:
  valid VIIRS DT land negatives also occur. Keep retrieval-team QA, Average
  Ocean, valid negatives, minimum count 1 and DB v2 as working defaults. No
  tested ρ calibrates global uncertainty, and all 32 global RMSE gates fail;
  GLS with ρ = 0.5 remains provisional. Count weighting's September accuracy
  benefit and weaker uncertainty calibration are both reported. The original
  long-running batch encountered a stale post-export import; independent
  archive/report verification and fresh plotting establish the saved evidence,
  rather than treating its nonzero exit as success. Independent scientific
  review and `codex review --uncommitted` pass; 885 offline tests, the live
  AERONET test, mypy, Black and isort pass. C7/SP1/AMI limits remain open.

- Task 4.6: accepted 1,552 PDF/PNG pairs: 414 maps and 1,138 AERONET figures.
  Every individual protocol and all 75 combined suite protocols pass; every
  preview was visually inspected, including all daily maps. July's four
  configurations, September's four sensor/version configurations, the smoke
  box, full five-point ρ sweeps and Southern Ocean QA/negative sensitivities
  are included. The final September regional-series tick-spacing defect has
  a regression test, and affected plots were regenerated. Inventories record
  current artifact, protocol and input hashes; the AERONET manifest uses
  portable `{data_root}` paths. Evidence is in
  `docs/validation/map_plot_verification.md` and
  `docs/validation/aeronet_plot_verification.md`. The full 885-test gate,
  typing/format checks and final clean review apply; the final review also
  passes 100 focused tests and independently audits inventory hashes/counts.
  Publication acceptance does not close the failed numerical science gates
  or establish comparison with the unavailable external SP1 archive.

- Task 4.7: removed the duplicate Product AOD panel from single-sensor mean
  figures and the redundant global AOD heading. The sensor aggregate is
  centered at left, with Terra above Aqua (and SNPP above NOAA-20) in fixed
  Dark Target and Deep Blue columns. Sparse streams keep their column slots;
  mixed products retain their distinct combined field and regional figures
  retain their region heading. All 18 affected mean figures were regenerated,
  passed protocols and were visually inspected. The other 396 maps retain
  their artifact hashes, and all 414 map inventories / 31 suite reports were
  audited. The 885-test offline gate, mypy, Black and isort pass; independent
  review found no remaining issue. The review's custom-catalog prefix finding
  was fixed and verified: canonical platform names affect ordering, never
  which configured streams are shown. `codex review --uncommitted` is clean;
  its localhost fixture was blocked by its nested sandbox, while all 885 tests
  pass in the task environment. Numeric inputs and science gates are unchanged.

- Task 4.8: independent UTC daily AERONET and satellite means are paired by
  site-day, then averaged equally across sites for regional daily acceptance.
  All 24 campaign reports are verified; 18 pass the global criteria, including
  both baselines. Every global daily RMSE passes, while six configurations
  retain other failed criteria and regional failures remain explicit. Historical
  three-hour comparisons remain diagnostics; individual L2 comparisons retain
  their ±30-minute overpass matching. Daily uncertainty calibration remains open.
  The 124 regional figures now show matched-site middle-50% bands and statistics
  from the same daily values used for acceptance. Geographic subtitles, Title
  Case labels, 550 nm wavelength labels and compact footers follow user direction.
  Insets choose among six internal positions, clear the Time label and clip
  coastlines to each exact region. All replacement previews were inspected;
  protocols, geometry checks and inventory hashes pass. The other 1,014 figures
  and all 1,200 historical evaluation files retain their original bytes.
  `codex review --uncommitted` found no remaining actionable regressions;
  927 offline tests pass in the task environment, with 241 focused tests
  passing after the final text revision. Mypy, Black and isort pass. The
  nested review sandbox blocks its existing localhost fixture only; the
  same test passes in the full task gate. The reviewed external-output-path
  collector defect is fixed and covered by a regression test.

## Open items and risks

| Item | Plan |
|---|---|
| Mean-map presentation | Task 4.7 removes duplicate single-sensor Product panels and global quantity headings, and aligns platform rows within algorithm columns. Existing scientific acceptance items remain independent of this presentation change. |
| Daily validation and regional presentation | Task 4.8 uses independent UTC daily means and equal-site regional daily averages for acceptance and plotting. The numeric limits remain absolute bias ≤ 0.03, RMSE ≤ 0.10 and R ≥ 0.8; the sampling basis replaces the former three-hour gate. Middle-50% site bands, daily statistics and a region/site inset describe the same cohort. Site spread is distinct from uncertainty in the regional mean. |
| Longitude final-edge exception | Task 1.6 explicitly requires input 359.375° in column 287. Normalization preserves that exact input; other longitudes use the wrapped formula. |
| July VP1 acceptance | Task 1.10 measured written p99.9 > 3 for all four runs and coverage above the approximate 1–10% range. Monthly means pass; upper-tail evidence is in `docs/validation/july2008_verification.md`. Scientific disposition of these provisional gates remains open. |
| September rejection gate | Task 2.4 passes three of four science gates; native valid ranges and QC both cap AOD at 5, making a strictly-above-5 rejection impossible. Endpoint counts remain separate. The original gate needs scientific disposition; it was not silently changed. |
| Sept 2020 staging | Complete: 29,856/29,856 audited. Four files missed during a DNS failure were recovered by resumable staging; the recovery log is `.cache/stage_september_resume.log`. |
| MODIS C7 public acquisition and science acceptance | AMI C7 samples permit reader activation independently of CMR; July operational test, September comparison and uncertainty calibration tracked separately |
| DT ocean QA source conflict | Keep QA ≥ 1: the two-month QA = 3 sensitivity loses coverage and worsens global grid RMSE. Small southern land/coastal samples do not establish open-ocean skill. Evidence: `docs/validation/evaluation_campaign.md`. |
| ρ default and scientific acceptance | Keep 0.5 provisional. Historical three-hour diagnostics did not calibrate uncertainty for any tested ρ ∈ [0, 1]; these are not daily-product acceptance failures. Task 4.8 records new daily regional acceptance separately in `docs/validation/daily_acceptance_campaign.md`. Daily uncertainty calibration requires an explicit temporal covariance model. |
| DT and DB retrieve the same scene (correlated across streams) | v1 treats streams as independent; Birge inflation partly covers it; a cross-stream ρ is a later option |
| CAM obs template | Expansion verified in CAM `filenames.F90` (cam6_4_161). Confirm only the `atm.log` check (Task 1.8) in one short derecho run |
| CAM7 baseline comparison | Needs CAM7's July 2008 SP1 files copied from derecho (path from the user) for the `--pairing cam7` gate |
| AERONET Level 2.0 lags real time | AOD15 as a separate product; matchups carry the level |
| AERONET service limits | ≤ 10 requests/min, one request per day for all sites, raw CSV cached |
| Cartopy Natural Earth | Prefetched to `~/Data/cartopy` (mac). derecho and AMI need `sarb-aod plot --prefetch-maps` once |
| Poppins font | Not installed; Helvetica Neue fallback. Install it for publication figures |
| AMI deployment | Paths, C7 July holdings, scheduler and separate sarb-aod environment confirmed. Tasks 5.2/5.3 complete: 248 C7 July files validated. External acquisition, plotting setup, multi-year production and C7 science acceptance remain separate. |
| Southern Ocean noise | QA ≥ 1 vs = 3 and DT-land negatives were compared in both periods. The region has no ocean mask and few open-ocean AERONET matchups. Denoising stays deferred. Later screens: `Average_Cloud_Pixel_Distance_Land_Ocean`, `Glint_Angle` (VIIRS: `geolocation_data/Glint_Angle`), wind speed (`Wind_Speed_Ncep_Ocean` for MODIS, `geophysical_data/Wind_Speed_GMAO_Ocean` for VIIRS DT) |

## AMI C7 implementation revision (2026-10-11)

The user authorized July 2008 C7 processing on AMI, with output beneath
`/CERES/sarb/dfillmor/MODIS_VIIRS`. Develop on the Mac, commit/push, then
fast-forward pull `$HOME/CERES-SARB-MODIS-VIIRS` on AMI. Track run and machine
configs; reconcile any site edits into the laptop source. See
[MODIS C7](MODIS_C7.md) for the inspected fields, supplied combined-product
semantics, and limits of operational acceptance.

- Task 2.5: C7 Terra/Aqua reader maps, DB ocean QA/uncertainty, product-specific
  archive discovery and read-only staging guard, independent supplied combined
  AOD/category counts, atomic output and validation. Two real AMI samples agree
  with producer DT/DB best estimates at default QA. Review findings fixed:
  unfiltered DB fields preserve QA-1 overrides; source-precision bounds retain
  native float32 -0.1. All 933 offline tests, mypy, Black and isort pass.
  `codex review --uncommitted` completed; both findings have regression tests.
  The review sandbox could not bind the HTTP fixture, but the full task gate
  passed in the primary environment. July/September scientific validation and
  C7 DT uncertainty calibration remain Task 2.6.

- Task 5.2: tracked `ami-modis-c7` archive profile and
  `modis_c7_l2_200807_f09_3hr_gls_rho05` run; documented config naming and
  local-commit/push/remote-pull workflow. `scripts/ami_grid.sh` uses Bash,
  explicit environment binaries, one scheduler slot/thread, and either a
  requested day, bounded day array, or resumable monthly driver. Read-only
  archive inputs are separated from writable grids/summaries/evaluation/plots.
  `show-config` resolves CAM namelist paths, `bash -n` passes, and the configs
  were included in the Task 2.5 review and 933-test gate. Deployment and measured
  July execution are Task 5.3; no multi-year production is implied.

- Task 5.3: deployed `d69d688` via fast-forward pull and editable install. July 1
  smoke job 5034001 processed 321 granules in 72.047 s; array 5034002 completed
  all 31 days (one resumed) with all exit statuses zero. Summary job 5034004
  validated/resumed 248 files, 1,355,006,280 bytes, and wrote monthly metrics.
  Provenance covers 9,829 unique-acquisition C7 source granules. See
  [AMI July C7 operational report](validation/ami_c7_200807.md) and its per-day
  accounting. The source count, native category counts, ranges, and processing
  hashes were checked. The recorded TSV agrees with remote evidence,
  `git diff --check` passes, and `codex review --uncommitted` found no actionable
  defects in the operational records. C7 science acceptance remains Task 2.6.
