# CERES-SARB-MODIS-VIIRS — Agent Notes

## Project overview

Preprocess MODIS (and later VIIRS) Level-2 aerosol retrievals into **CAM7-ready 550 nm AOD
constraint files** for CERES-SARB (CESM3/CAM7 AOD nudging). Package `sarb_aod`, CLI
`sarb-aod`. Scope is deliberately narrow: only what MODIS/VIIRS → CAM7 preprocessing needs,
while keeping DAVINCI's pydantic-schema, config-driven design so that file paths and
filenames differ between machines (macOS dev laptop, NCAR derecho, AMI production) only in
configuration, never in code.

Pipeline (target):

1. **Stage** L2 granules from NASA Earthdata into `<product>/YYYY/MM/DD` (done: `sarb-aod stage`).
2. **Read** per-source streams: Terra/Aqua × Dark Target/Deep Blue (MODIS); SNPP/NOAA-20 ×
   DT/DB (VIIRS). MODIS and VIIRS are normally not mixed, but mixing must stay possible.
3. **Grid** to the CAM f09 grid (192×288, pole-centered) in 3-hour windows, with
   count-weighted (and, where per-pixel uncertainty exists, uncertainty-aware) averaging
   across sources.
4. **Write** CAM7 contract files (below).

Deferred by user direction (2026-10-10): spatial/temporal denoising ("despeckle") and the
AERONET-anchored VIIRS→MODIS bias correction.

Test periods:

- **July 2008**: MODIS Terra + Aqua, Collections 6.1 and 7 (MODIS-only; predates VIIRS).
- **September 2020**: MODIS C6.1/C7 + VIIRS (Labor Day 2020 western US fires: extreme AOD,
  smoke/cloud confusion, MODIS/VIIRS mixing). VIIRS Deep Blue v2 (SNPP + NOAA-20) is the
  baseline, NOAA-20 Deep Blue v2.1 a version comparison; VIIRS Dark Target v2.1.

## Design decisions (user-confirmed 2026-10-10)

- **Numba binning** ported from DAVINCI `pairing/grid_binning.py` (not imported), extended to
  accumulate per-stream N, Σw, Σw·x, Σw·x², Σσ².
- **Weighting is configurable.** Default: generalized least squares with a simple
  error-correlation model ("superobbing"): pixel inverse-variance within a stream, stream-mean
  variance σ̄²(ρ + (1−ρ)/N) so weight saturates at 1/(ρσ̄²), streams combined by inverse
  variance, uncertainty inflated by the Birge ratio when streams disagree. ρ is a config
  parameter (ρ = 0 → pure inverse-variance). Also provide **simple count weighting with no
  uncertainty** (CAM7 `prepare_modis_era5_grid.py` behavior).
- **No monetio dependency.** AERONET is fetched straight from the AERONET Web Service v3
  (URL and CSV rules re-implemented from what DAVINCI's `AERONETReader` delegates to
  monetio). The validation suite ports DAVINCI's metrics, grouped-statistics calculator,
  output formatter, `PointStrategy` matching and `AEROSOL_REGIONS` (plan Phase 3).
- **QA defaults follow the retrieval teams' recommendations** (verified 2026-10-10), with a
  `cam7_v2` preset (QA≥1 everywhere, `Effective_Optical_Depth_Best_Ocean`) for regression
  against CAM7 products:
  - Dark Target (MODIS and VIIRS): land QA = 3, ocean QA ∈ {2, 3}; ocean AOD from
    `Effective_Optical_Depth_Average_Ocean` (darktarget.gsfc.nasa.gov L2 product contents).
  - Deep Blue (VIIRS user guide v2 §1.2; same convention for MODIS DB): QA ∈ {2, 3} over land
    and water; `*_Best_Estimate` SDS are the same data pre-filtered to QA ≥ 2.

## Implementation plan

`docs/IMPLEMENTATION_PLAN.md` is the tracked plan (phases, tasks, tests, open items). Update
its Status table and open-items list in the same commit that completes or changes a task.

## Layout

```text
sarb_aod/config/      pydantic schema (schema.py) and YAML/profile/env loader (loader.py)
sarb_aod/data/        catalog.yaml: products, collections (CMR version, filename token, format)
sarb_aod/paths.py     path-template rendering
sarb_aod/stage/       Earthdata search/download hooks, manifests, resumable staging runner
sarb_aod/cli.py       sarb-aod show-config | stage | audit
config/profiles/      machine profiles: mac, derecho, ami (where things live)
config/runs/          run configs (what to process)
tests/                offline pytest suite (network calls injected via Hooks)
```

## Environment and commands

`environment.yml` is a minimal subset of the DAVINCI env (same pins), conda-forge only with
`nodefaults`, so it works under Miniconda as well as Miniforge.

```bash
conda env create -f environment.yml      # env name: sarb-aod (mamba also works)
conda activate sarb-aod
python -m pip install --no-deps -e .
pytest                                    # offline; network tests are marked `network`
mypy sarb_aod
sarb-aod show-config -c config/runs/modis_l2_200807.yaml
sarb-aod stage -c config/runs/modis_l2_200807.yaml [--dry-run]
sarb-aod audit -c config/runs/modis_l2_200807.yaml
```

## Earthdata credentials (earthaccess)

- Staging authenticates through `earthaccess.login(strategy=...)`; the strategy is
  `stage.auth_strategy` in the run config (default `netrc`).
- `netrc`: `~/.netrc` with a `machine urs.earthdata.nasa.gov` entry (login/password), mode
  `600`. Present and verified on the macOS dev laptop 2026-10-10.
- `environment`: `EARTHDATA_USERNAME` / `EARTHDATA_PASSWORD` (or `EARTHDATA_TOKEN`) — useful
  on batch/production systems.
- Never print, log, copy, or commit credential contents. Check for presence by file
  existence or key names only.
- CMR **search** needs no login (dry runs work without credentials); **downloads** do.
  LAADS data links resolve to `data.laadsdaac.earthdatacloud.nasa.gov`.

## Data layout and configuration rules

- Dev data root: `~/Data`. Granules: `~/Data/<Platform>/<Instrument>/<SHORTNAME>.<token>/YYYY/MM/DD/`,
  e.g. `~/Data/Terra/MODIS/MOD04_L2.061/2008/07/01/` (`<SHORTNAME>.<token>` = NASA DOI naming,
  keeps C6.1 and C7 apart). Manifests: `.../<SHORTNAME>.<token>/manifests/`.
- Production (AMI) paths differ and are not yet known: `config/profiles/ami.yaml` reads
  `${SARB_AOD_DATA_ROOT}`. Never hard-code machine paths in code; add template fields or
  profile keys instead.
- Run configs select collections per product: `stage.products: {MOD04_L2: ["6.1", "7"], ...}`.
- Collections (`sarb_aod/data/catalog.yaml`; keys = CMR VersionId): MODIS C6.1 = token `061`,
  HDF4. MODIS C7 = token `007`, **NetCDF4**; as of 2026-10-10 LAADS publishes C7 only for
  L1B/geolocation (MOD02*/MOD03), so MOD04_L2/MYD04_L2 C7 return zero granules and are
  reported "unavailable" until released. VIIRS AERDB v2 = `002`, v2.1 = `021` (SNPP v2.1 only
  from 2026-06); AERDT v2.1 = `021`; all NetCDF4. Readers must handle HDF4 and NetCDF4.
- Per-pixel uncertainty: C6.1 has `Deep_Blue_Aerosol_Optical_Depth_550_Land_Estimated_Uncertainty`;
  Dark Target has none (only `Fitting_Error_Land`, `Least_Squares_Error_Ocean` fit residuals),
  so DT falls back to its expected-error envelope unless C7 adds one.

## CAM7 contract (consumer: `../CERES-SARB-CAM7/src/cam/aod_nudging_obs.F90`)

- One NetCDF4 file per 3-hour window; default name `MODIS_VIIRS_AOD_3hr_f09.%y%m%d.%s.nc`,
  `%s` = window-start seconds of day (0, 10800, …, 75600).
- CAM requires: dims/coords `lat(192)`, `lon(288)` exactly equal to f09 centers
  (`lat = linspace(-90, 90, 192)`, `lon = arange(288) * 1.25`), `aod_weighted_mean(lat, lon)`
  with `_FillValue = -999` meaning "no obs, leave model untouched", and a scalar CF `time`
  at the window **center**. Extra variables are ignored by CAM.
- Keep the companion fields from the CAM7 SP1 contract: `aod_count`, `aod_uncertainty`,
  `aod_weighted_mean_modis`/`_viirs`, `lat_bnds`, `lon_bnds`, `time_bnds`, provenance globals.
- CAM builds the path as `aod_obs_path // interpret_filename_spec(aod_obs_template)`, so
  output YYYY/MM/DD subdirectories need only a template like
  `%y/%m/%d/MODIS_VIIRS_AOD_3hr_f09.%y%m%d.%s.nc` (to be confirmed in a CAM run).

## Brownfield reference repositories (read-only)

Borrow from these; do not modify them from this repo's tasks.

- **`../CERES-SARB-CAM7`** (branch `develop`; GitHub `NCAR/CERES-SARB-CAM7`)
  - `scripts/prepare_modis_era5_grid.py`: the **four-stream count-weighted** MODIS averaging
    (Terra/Aqua × DT/DB, per-stream counts, standard error of the mean). Primary algorithm reference.
  - `sp1_aod_pipeline/`: contract writer/validator (`contract.py`), f09 grid (`f09_grid.py`),
    3-hour windows (`aggregate.py`), inverse-variance merge (`merge.py`), AERONET-anchored
    VIIRS bias correction (`bias.py`, deferred here), AERONET validation (`validation/`).
  - `src/cam/aod_nudging_obs.F90`, `aod_nudging_intr.F90`: what CAM actually reads.
  - `docs/science-verification.md`: plot-based verification gates (VP1–VP4 for obs products).
  - No despeckle/smoothing code exists there (only a visual "no speckle" gate).
- **`../DAVINCI`** (local `main` is ~50 commits behind `origin/develop`; GitHub `NCAR/DAVINCI`)
  - `davinci_monet/config/parser.py`: YAML unique-key loading, `${VAR}`/`~` expansion (ported).
  - `davinci_monet/config/schema.py`: pydantic schema style.
  - `davinci_monet/io/download/earthdata.py`: hook-injected staging pattern (ported).
  - `davinci_monet/pairing/grid_binning.py`: numba swath→grid binning kernels (to port).
  - `origin/develop:davinci_monet/analysis/projection_core.py::masked_boxcar_smooth`:
    mask-aware 3×3 smoother (candidate for the deferred denoising step).
  - `MODIS_VIIRS_L2.md`: LAADS product/collection notes.
- The legacy CAM6 prototype (Toni Viudez-Mora, `/glade/work/tvmora` on derecho) had an
  unarchived data-prep pipeline (daily averaging, a `VIIRS_corrected` intermediate); it is
  seed material only.

## Plotting rules (DAVINCI practice; details in plan Phase 4)

- All text goes through `sarb_aod/plots/labeling.py` (ported from DAVINCI). Titles are terse:
  the quantity plus an operation word only when it changes meaning; single-source titles put
  the source first (`MODIS AOD`). Never put dates, statistics, `vs <source>`, raw variable
  names, internal labels (phase/task, `gls`, `rho`, `cam7_v2`) or `x`/`y` in titles. Dates go in
  the subtitle (`YYYY-MM-DD – YYYY-MM-DD`), maps add ` | Mean: <area-weighted mean>`.
- Absolute AOD maps: `geosit_aod_levels()` (0.05 bins to a 1.0 cap plus 0.005/0.01/0.02/0.03/0.04),
  `turbo`, `BoundaryNorm`, `extend="max"`. Differences/biases: `RdBu_r`, robust symmetric
  limits, `TwoSlopeNorm` at 0, colorbar `bias_label` (`<Y> − <X>`, U+2212).
- Rasterize dense filled layers (pcolormesh, contourf, fill_between, large scatters); keep
  text, axes, colorbars and contour lines vector.
- Global PlateCarree maps by default; coastlines and solid gridlines; no political borders.
- PDF deliverables with PNG previews; dates/configuration in filenames, not titles. A suite is
  accepted only after its protocol report passes and its previews are visually inspected.

## Git workflow

- Develop strictly on `main`. Do not create branches or worktrees unless explicitly instructed.
- When a plan task or phase is complete, tested and reviewed, commit it on `main` and push to
  `origin/main` right away (one commit per task, `Task N.M: <summary>`), updating the plan's
  Status table in the same commit. See "Workflow" in `docs/IMPLEMENTATION_PLAN.md`.
