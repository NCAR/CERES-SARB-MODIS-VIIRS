# CERES-SARB-MODIS-VIIRS — Agent Notes

## Project overview

Preprocess MODIS and VIIRS Level-2 aerosol retrievals into **CAM7-ready 550 nm AOD constraint
files** for CERES-SARB (CESM3/CAM7 AOD nudging). Package `sarb_aod`, CLI `sarb-aod`. The scope
is narrow: only what MODIS/VIIRS → CAM7 preprocessing needs. It keeps DAVINCI's
pydantic-schema, config-driven design, so file paths and filenames differ between machines
(macOS dev laptop, NCAR derecho, AMI production) only in configuration, never in code.

Pipeline:

1. **Stage** L2 granules from NASA Earthdata into `<product>/YYYY/MM/DD` (done: `sarb-aod stage`).
2. **Read** per-source streams: Terra/Aqua × Dark Target/Deep Blue (MODIS); SNPP/NOAA-20 ×
   DT/DB (VIIRS). MODIS and VIIRS are normally not mixed, but mixing must work.
3. **Grid** to the CAM f09 grid (192×288, pole-centered) in 3-hour windows, using GLS
   (uncertainty + error correlation) or count weighting across sources.
4. **Write** CAM7 contract files (below).
5. **Validate** against AERONET and **plot** in DAVINCI style.

Deferred by user direction (2026-10-10): spatial/temporal denoising ("despeckle") and the
AERONET-anchored VIIRS→MODIS bias correction.

Test periods:
- **July 2008:** MODIS Terra + Aqua, Collections 6.1 and 7 (MODIS-only; predates VIIRS).
- **September 2020:** MODIS + VIIRS (Labor Day 2020 western US fires: extreme AOD,
  smoke/cloud confusion, MODIS/VIIRS mixing). VIIRS Deep Blue v2 (SNPP + NOAA-20) is the
  baseline, NOAA-20 Deep Blue v2.1 a version comparison, VIIRS Dark Target v2.1.

## Implementation plan and status

`docs/IMPLEMENTATION_PLAN.md` is the tracked plan: workflow, task table with dependencies,
per-task specs and tests, and open items. Read its **Workflow** section before starting a task.
Update its task table (State, Commit) and open items in the same commit that completes a task.
At session start, also check the repo root for cross-model handoff files (`HANDOFF_*.md`,
`REVIEW_*.md`; see below).

## Design decisions (user-confirmed 2026-10-10 unless marked)

- **Standalone: port, don't import.** Copy what is needed from DAVINCI and CAM7 with
  attribution. Never import `davinci_monet` (it is not installed in any env) or **monetio**.
- **Numba binning** is ported from DAVINCI `pairing/grid_binning.py`. The extended kernel
  accumulates per window, stream and cell: N, N_neg, N_sigma_missing, Σx, Σx², Σw, Σw·x and
  Σ(1/σ), with w = 1/σ². Time bins are half-open.
- **Weighting is configurable.**
  - The default is generalized least squares with an error-correlation model
    ("superobbing"). Within a stream: x̄ = Σw·x/Σw, s = Σ(1/σ)/Σw, and
    V = ρ·s² + (1 − ρ)/Σw, which is exact for Cov(eᵢ, eⱼ) = ρσᵢσⱼ. Streams combine by inverse
    variance, and the Birge ratio inflates σ when streams disagree. ρ is a config parameter
    intended for AERONET calibration. The two-month campaign found no tested ρ ∈ [0, 1]
    that calibrates global uncertainty; 0.5 remains a provisional working value.
  - Option: **simple count weighting without retrieval-uncertainty weighting** (CAM7
    `prepare_modis_era5_grid.py`). It reports a sample standard error, with a configurable
    floor or zero for a single pixel.
- **QA defaults follow the retrieval teams** (verified on real granules and in Levy et al. 2013):
  - Dark Target land QA = 3, **ocean QA ≥ 1**. This equals the products' own
    `Optical_Depth_Land_And_Ocean` for MODIS C6.1 and VIIRS DT. One darktarget web page says
    ocean QA 2–3, but that drops ~60% of ocean pixels (87% south of 40° S), the cleanest ones.
    Phase 3 compares the two.
  - Ocean AOD from `Effective_Optical_Depth_Average_Ocean`.
  - Deep Blue QA ≥ 2 over land and water (VIIRS DB water QA takes only 1 or 3).
  - The `cam7_v2` QC preset sets QA ≥ 1 everywhere, MODIS
    `Effective_Optical_Depth_Best_Ocean`, AOD ∈ [0, 5] and unrounded scaling. The full
    `modis_l2_200807_cam7_v2.yaml` run additionally selects naive TAI93 time, count weighting,
    zero single-pixel σ and no edge granules to reproduce CAM7's diagnostic four-stream
    product (Task 1.10). The QC preset alone does not change time or weighting. This is
    *not* the SP1 archive CAM read in 2008.
- **Negative AOD** (*working policy*, tested in Phase 3): MODIS and VIIRS DT land can be negative
  (down to −0.05); the earlier MODIS-only statement was corrected by a complete September 8
  scan (`docs/validation/sept2020_negative_aod.tsv`). No negative DT ocean or DB AOD was found
  in that scan. Keep values ≥ −0.05 in averages and clip the final cell mean at 0 for CAM.
- **Time:** TAI93 → UTC subtracts leap seconds (6 s in 2008, 10 s from 2017). VIIRS DT has no
  per-pixel time; it is derived from the `time_coverage_*` attributes.
- **AERONET:** fetched directly from Web Service v3 (rules re-implemented from what DAVINCI's
  `AERONETReader` delegates to monetio). A UTC day D is requested with
  `day2 = D+1, hour2 = 0`; `hour2 = 23` loses data. The validation suite ports DAVINCI's
  metrics, calculator, output formatter, nearest-cell index logic and `AEROSOL_REGIONS`.
- **Validation timing (user direction, 2026-10-11):** merged Terra/Aqua or mixed-sensor
  products are evaluated with independent UTC daily means, not three-hour collocation.
  At each site, use all valid daily AERONET observations and the count-weighted daily
  satellite cell mean. Pair site-days, then give active sites equal weight in regional
  daily averages. Acceptance uses those regional daily averages (global for the run gate),
  with |MB| ≤ 0.03, RMSE ≤ 0.10 and R ≥ 0.8. Pooled site-day errors remain diagnostics.
  Individual L2 overpasses retain ±30-minute AERONET matching. Historical three-hour and
  CAM7 pairings remain separate diagnostics; CAM7 output files still use three-hour windows.
  Daily satellite uncertainty is unavailable until a temporal covariance model is specified.
- **Plots** follow DAVINCI plotting practice (rules below and in plan Phase 4).
- **Campaign limits** (*daily revision, 2026-10-11*): all 24 daily grid configurations
  pass the global RMSE limit, and 18 pass all global criteria, including both baselines.
  Regional failures remain; see `docs/validation/daily_acceptance_campaign.md`.
  The former three-hour RMSE failures remain historical diagnostics. Retain retrieval-team
  QA, Average Ocean, valid DT-land negatives and minimum count 1 as working defaults;
  uncertainty calibration remains open. The Southern Ocean region is a latitude band
  without an ocean mask, and its AERONET stations are mostly land/coastal.

## Layout

```text
sarb_aod/config/      pydantic schema (schema.py) and YAML/profile/env loader (loader.py)
sarb_aod/data/        catalog.yaml: products, collections (CMR version, filename token, format)
sarb_aod/paths.py     path-template rendering
sarb_aod/stage/       Earthdata search/download hooks, manifests, resumable staging runner
sarb_aod/cli.py       sarb-aod show-config | stage | audit | grid | validate | summarize |
                      aeronet stage/audit | evaluate | plot | grid-months
config/profiles/      machine profiles: mac, derecho, ami (where things live)
config/runs/          run configs (what to process)
tests/                offline pytest suite (network calls injected via Hooks)
tests/data/aeronet/   AERONET Web Service v3 fixtures (see its README)
```

## Environment and commands

`environment.yml` is a minimal subset of the DAVINCI env (same pins; conda-forge only with
`nodefaults`, so Miniconda works). It includes pandas, matplotlib, cartopy and seaborn for
Phases 3 and 4, and black is pinned (`26.*`). The `sarb-aod` env on the Mac is up to date and
the package is installed editable.

Agents (Codex) must not rely on `conda activate`; call the env's binaries directly:

```bash
E=~/miniforge3/envs/sarb-aod/bin
$E/python -m pytest -m "not network"      # the task gate; network tests only with network on
$E/mypy sarb_aod
$E/black --check . && $E/isort --check-only .
$E/sarb-aod show-config -c config/runs/modis_l2_200807.yaml
$E/sarb-aod stage -c config/runs/modis_l2_200807.yaml [--dry-run]
$E/sarb-aod audit -c config/runs/modis_l2_200807.yaml
```

Set `MPLCONFIGDIR=$PWD/.cache/matplotlib` (gitignored) so matplotlib doesn't write to
`~/.matplotlib`. Natural Earth coastlines (110m, 50m) are prefetched to `~/Data/cartopy`, the
mac profile's planned `paths.cartopy_data_dir`. Human setup elsewhere:
`conda env create -f environment.yml && conda activate sarb-aod && python -m pip install --no-deps -e .`

## Running as Codex (verified 2026-10-10, codex-cli 0.161.0)

- **Launch** from the repo root:
  `codex --sandbox workspace-write --add-dir ~/Data -c sandbox_workspace_write.network_access=true`.
  Network is needed only for staging, AERONET fetches and `network` tests.
- The sandbox reads anywhere. Writing outside the repo needs `--add-dir`, and network needs
  `network_access=true`.
- **`.git/` is always read-only in the sandbox**, so `git add`, `git commit` and `git push` must
  be run as approved escalations at each task's commit step. If an escalation is not approved,
  stop with the work staged and say so.
- Never write into the granule trees `~/Data/<Platform>/…` except through `sarb-aod stage`, and
  never into `~/Data/logs/`. Outputs go only to the run config's `paths.*` templates.
- **Review step:** `codex review --uncommitted`.

## Earthdata credentials (earthaccess)

- Staging authenticates with `earthaccess.login(strategy=...)`; the strategy is
  `stage.auth_strategy` in the run config (default `netrc`).
- `netrc`: `~/.netrc` with a `machine urs.earthdata.nasa.gov` entry, mode `600`. It is present
  and verified on the macOS dev laptop (2026-10-10).
- `environment`: `EARTHDATA_USERNAME`/`EARTHDATA_PASSWORD` (or `EARTHDATA_TOKEN`), for batch or
  production systems.
- **Never print, log, copy or commit credential contents**; check only file existence or key names.
- CMR **search** needs no login (dry runs work without credentials); **downloads** do. LAADS
  data links resolve to `data.laadsdaac.earthdatacloud.nasa.gov`.

## Data layout and status

- **Dev data root `~/Data`.** Granules go in
  `~/Data/<Platform>/<Instrument>/<SHORTNAME>.<token>/YYYY/MM/DD/`, e.g.
  `~/Data/Terra/MODIS/MOD04_L2.061/2008/07/01/`; `<SHORTNAME>.<token>` is NASA DOI naming and
  keeps collections apart. Manifests are in `.../<SHORTNAME>.<token>/manifests/`.
- **Status (2026-10-10):**
  - July 2008 MODIS C6.1 is staged and audited (9,518/9,518).
  - Sept 2020 MODIS + VIIRS is staged and audited (29,856/29,856; 213.45 GiB).
    The original `sarb-stage` session ended after four transient DNS failures;
    resumable staging recovered them. The successful recovery/audit log is
    `.cache/stage_september_resume.log`; the older `~/Data/logs/` log remains unchanged.
  - Long jobs run detached:
    `screen -dmS <name> zsh -c 'caffeinate -i <cmd> >> <log> 2>&1'`.
- **Production (AMI)** output root is `/CERES/sarb/dfillmor/MODIS_VIIRS`
  (user direction, 2026-10-11); archive inputs remain read-only. Develop locally,
  commit/push, then pull `$HOME/CERES-SARB-MODIS-VIIRS` on AMI. The generic
  `config/profiles/ami.yaml` still reads `${SARB_AOD_DATA_ROOT}`. Never hard-code
  machine paths in code; add template fields or profile keys instead.
  - `ami-modis-c7` reads the existing C7 archive; its tracked July run is
    `modis_c7_l2_200807_f09_3hr_gls_rho05.yaml`. On 2026-10-11 it processed
    9,829 granules into 248 validated files (1.2619 GiB); see
    `docs/validation/ami_c7_200807.md`. Operational acceptance does not close
    C7 AERONET validation or uncertainty calibration.
- **Run configs** choose collections per product for staging
  (`stage.products: {MOD04_L2: ["6.1", "7"], ...}`). Gridding names exactly one collection
  per stream (plan Task 1.9).
- **Collections** (`sarb_aod/data/catalog.yaml`; keys are CMR VersionIds):
  - MODIS C6.1: token `061`, HDF4.
  - MODIS C7: token `007`, NetCDF4. The initial public acquisition check found
    only C7 L1B and geolocation products;
    AMI C7 aerosol samples are available and reader activation is independent of CMR.
    See `docs/MODIS_C7.md` for C7 DB ocean, provisional DT uncertainty, and the
    separately gridded supplied combined AOD/algorithm categories.
  - VIIRS AERDB: v2 `002`, v2.1 `021` (SNPP v2.1 only from 2026-06). VIIRS AERDT: v2.1 `021`.
    All VIIRS files are NetCDF4.
- **Variable facts** (verified; details in plan Tasks 1.1, 2.1, 2.2):
  - MODIS 0.55 µm is index 1 on the leading wavelength dimension; VIIRS DT has the wavelength
    dimension last, so always select by dimension name.
  - The DT surface comes from whichever AOD field is valid; never use MODIS `Land_sea_Flag`,
    whose `valid_range` is wrong.
  - MODIS DB C6.1 has a per-pixel 1σ `Estimated_Uncertainty`; VIIRS DB has
    `Expected_Uncertainty`. Dark Target has none and uses expected-error envelopes.

## CAM7 contract (consumer: `../CERES-SARB-CAM7/src/cam/aod_nudging_obs.F90`)

- One NetCDF4 file per 3-hour window, named `<stem>.%y%m%d.%s.nc` (stem
  `MODIS_VIIRS_AOD_3hr_f09`), where `%s` is the window-start second of day (0, 10800, …, 75600).
- CAM reads `lat(192)`/`lon(288)` (f09 centers, checked to 1e-3), `aod_weighted_mean(lat, lon)`
  (values > −900 are "valid"; fill −999 means "leave the model untouched") and a scalar CF
  `time` at the window **center**, in hours. Extra variables are ignored.
- Keep the CAM7 SP1 companions: `aod_count`, `aod_uncertainty`, `aod_weighted_mean_modis`/`_viirs`,
  `lat_bnds`, `lon_bnds`, `time_bnds`, and the 15 provenance globals.
- New grid and matchup NetCDF files additionally record the repository URL, full Git SHA,
  tag-relative revision and exact HEAD tags; host/OS and Python/native package environment;
  effective configuration and original run/profile YAML snapshots (paths, text, SHA-256).
  `provenance.development_agents` carries declared LLM tool, provider, model name/version,
  reasoning effort and role. Never infer unverified model details. This describes development
  assistance, not an LLM in the numerical pipeline. See `docs/PROVENANCE.md` for attributes
  and limits. Preserve existing grid files' recorded processing history.
- CAM builds the path as `aod_obs_path // interpret_filename_spec(aod_obs_template)` with no
  separator, so `aod_obs_path` must end in `/`.
  - `%y/%m/%d/` subdirectories in the template are verified in CAM `filenames.F90` (cam6_4_161):
    `/` is literal, `%y` 4 digits, `%s` 5 digits; template ≤ 80 characters.
  - **A missing file is not an error; CAM runs free.** Confirm a run through its `atm.log`.

## Brownfield reference repositories (read-only)

Borrow from these; never modify, check out or commit in them from this repo's tasks.

- **`../DAVINCI`** (GitHub `NCAR/DAVINCI`). The working tree is `main`, **~50 commits behind
  `origin/develop`**. Read sources only with `git -C ../DAVINCI show origin/develop:<path>`;
  `AEROSOL_REGIONS` (`geography/domains.py`) and `analysis/projection_core.py` exist only on
  develop.
  - `config/parser.py` and `config/schema.py`: YAML/env patterns and schema style (ported).
  - `io/download/earthdata.py`: the hook-injected staging pattern (ported).
  - `pairing/grid_binning.py`: the numba kernels to port.
  - `stats/{metrics,calculator,output}.py`; `pairing/strategies/{point,base}.py`.
  - `plots/*`: style, labeling, titles, `_stats.py`, spatial renderers, suites, and
    `renderers/aod_correction_science.py` (the protocol reference).
  - `CLAUDE.md` § Plot Label & Title Conventions and § Plot Protocol Enforcement.
  - `analysis/projection_core.py::masked_boxcar_smooth`: a candidate for the deferred denoising.
- **`../CERES-SARB-CAM7`** (branch `develop`; GitHub `NCAR/CERES-SARB-CAM7`).
  - `scripts/prepare_modis_era5_grid.py`: the four-stream count-weighted averaging; the oracle
    for the `cam7_v2` regression.
  - `sp1_aod_pipeline/`: `contract.py` and `f09_grid.py` (to port), `aggregate.py`,
    `records.py`, `validation/` (AERONET 550 nm, VP2 gates), `bias.py` (deferred).
  - `src/cam/aod_nudging_obs.F90`: what CAM reads.
  - `docs/science-verification.md` (VP gates).
  - `scripts/summarize_2008_regional_aeronet_band_comparison.py` and
    `prepare_2008_regional_aod.py`: the regional method and display names.
  - No despeckle or smoothing code exists there.
- The legacy CAM6 prototype (`/glade/work/tvmora` on derecho) had an unarchived data-prep
  pipeline. It is seed material only.

## Plotting rules (DAVINCI practice; full rules in plan Phase 4)

- **Text:** all of it goes through `sarb_aod/plots/labeling.py`.
  - Use Title Case for labels, legend entries and annotations, retaining acronym and unit
    spelling (for example, AOD, RMSE and nm) and lowercase particles such as `of`.
  - Titles are terse: the quantity, plus an operation word only when it changes meaning
    (`AOD Bias`, `AOD Difference`). Scatter, time-series and histogram titles get no
    plot-type word. Single-source titles put the source first (`MODIS AOD`).
  - Site and region figures are titled `AOD - <Name>`.
  - Global map panels use source-specific titles without a redundant figure-wide `AOD`
    heading. Period mean maps omit the duplicate Product panel for single-sensor runs;
    the sensor aggregate sits at the upper left, with platform rows and Dark Target / Deep
    Blue columns. MODIS aggregate titles include the collection (C6.1 or C7). The lower-left
    cell holds provenance: recorded grid creation dates and processing revisions, plot date
    and revision, weighting/QA, configured L2 path templates, grid paths and PDF destination.
    Align the first line of the provenance block with the title of the map to its right. Use bold
    entry labels above indented values; wrap paths only at directory boundaries, preserving
    complete directory names and filenames. Leave clear space above the shared colorbar.
    Above that bar, center `DAVINCI - Data Analysis and Visual Intelligence for Climate & Chemistry`.
    Put the clickable repository URL above Provenance. Label the count and duration as
    `Time Windows: 248 × 3 Hours` (values from the run). Keep the PDF concise; detailed
    runtime, environment, config snapshots and development-agent metadata belong in NetCDF.
    Keep full timestamps and file paths in PDF metadata. Literal paths in this registered
    provenance panel are exempt from raw-key text checks, but retain layout/vector checks.
  - Regional time series show independently calculated AERONET and satellite daily means
    paired by site-day, then averaged equally across sites, with
    overlapping 25th–75th percentile bands across those sites (user choice, 2026-10-11).
    These replace the full-region satellite curve. A footer reports matched days, bias,
    RMSE, correlation and normalized mean absolute error (%), calculated from the plotted
    daily means. Bands describe site spread; they are not uncertainty intervals.
    Put `AOD 550 nm - <Region>` directly above the axes at left and the subtitle at right.
    Begin the single-line subtitle with latitude and longitude ranges separated by a space,
    then dates and active-site counts.
    Label the AOD axis with 550 nm, rotate dates 20°, and left-align the
    statistics and single-line explanatory footer. Put `Time` inside the axes above the
    bottom border, with dates outside. A region/site inset stays fully inside the plot box;
    choose among four corners, top center and bottom center for the most free space after
    accounting for curves, site-spread bands and the legend. Keep bottom-center maps above
    the Time label. Clip coastlines to the exact region box, without geographic padding.
    The map stays beneath the translucent bands and time-series curves.
  - Never put dates, years, statistics, `vs <source>`, raw keys, internal labels or `x`/`y` in
    titles. Dates go in the subtitle; maps add ` | Mean: <value>`.
- **Absolute AOD maps:**
  - `geosit_aod_levels()` (data-dependent, capped at 1.0), computed once per figure from all its
    AOD panels; comparison suites share the full fixed set;
  - `turbo`, `BoundaryNorm`, `extend="max"`, DAVINCI's sparse AOD colorbar ticks;
  - no colorbar label (dimensionless).
- **Other fields:** differences use `RdBu_r` with robust symmetric limits and `TwoSlopeNorm`;
  fractions use `gray_r` on [0, 1]; counts and σ use `viridis` with nice robust limits.
- **Rasterization:** rasterize dense filled layers; keep text, axes, colorbars and contour
  lines vector. The protocol checks this, since matplotlib 3.11 no longer warns.
- **Maps:** DAVINCI `_AOD_MAP_CONTEXT`: global PlateCarree, coastlines, solid gridlines, no
  political borders, no land/ocean fill.
- **Output:** PDF deliverables with PNG previews; dates and configuration in filenames. A suite
  is accepted only after its protocol report passes and its previews are visually inspected.

## Cross-model handoff

- At session start, check the repo root for `HANDOFF_*.md` / `REVIEW_*.md`.
- Write handoffs as `HANDOFF_<TOPIC>.md` with sections Context, Changes Made, Decisions &
  Rationale, Open Questions, Suggested Next Steps. They are gitignored; never commit them, and
  delete them once absorbed.
- Stage files explicitly (`git add <paths>`), never `git add -A`.

## Git workflow

- Develop strictly on `main`. Do not create branches or worktrees unless explicitly instructed.
- When a plan task or phase is complete, tested and reviewed, commit it on `main` and push to
  `origin/main` right away. Use one commit per task (`Task N.M: <summary>`) and update the
  plan's task table in the same commit. See "Workflow" in `docs/IMPLEMENTATION_PLAN.md`.
- The remote is SSH (`ssh://git@github.com/NCAR/CERES-SARB-MODIS-VIIRS`); keep it that way.
