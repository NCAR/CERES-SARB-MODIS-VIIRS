# CERES-SARB-MODIS-VIIRS

Preprocessing of MODIS and VIIRS Level-2 aerosol retrievals into CAM7-ready
550 nm AOD constraint files for CERES-SARB. The package is `sarb_aod`; the CLI is
`sarb-aod`.

Lineage: the gridding, four-stream (Terra/Aqua × Dark Target/Deep Blue) count-weighted
averaging and the CAM7 obs-file contract come from `../CERES-SARB-CAM7`
(`scripts/prepare_modis_era5_grid.py`, `sp1_aod_pipeline/`); the pydantic configuration
pattern, environment expansion and Earthdata staging come from `../DAVINCI`. This repo is
standalone: it ports what it needs rather than importing `davinci_monet`.

Plan and status: [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md). Agent notes: [`AGENTS.md`](AGENTS.md).

Spatial despeckling and the [July 2008 C6.1 laptop comparison](docs/DESPECKLE_PLAN.md)
are under development. Configuration, filtering and paired NetCDF output are implemented;
comparison tools and the scientific trial are pending.

## Environment

`environment.yml` is a minimal subset of the DAVINCI conda env (same pins; includes pandas,
matplotlib, cartopy and seaborn for validation and plotting), conda-forge only with `nodefaults`,
so it works under Miniconda as well as Miniforge:

```bash
conda env create -f environment.yml      # or mamba
conda activate sarb-aod
python -m pip install --no-deps -e .
pytest -m "not network"
```

Earthdata Login credentials are read from `~/.netrc` (`chmod 600`):

```text
machine urs.earthdata.nasa.gov
  login <earthdata-username>
  password <earthdata-password>
```

## Configuration

A run config (`config/runs/*.yaml`) states *what* to process; a machine profile
(`config/profiles/{mac,derecho,ami}.yaml`) states *where* things live. The run config is
deep-merged over the profile, so either may set any key.

- Profile selection: `--profile` > `$SARB_AOD_PROFILE` > the run config's `profile:` key.
- Profile search: `$SARB_AOD_PROFILE_DIR`, then `profiles/` beside or one level above the run config.
- String values expand `${VAR}`, bare `$VAR` path segments, and a leading `~`; an unset
  variable is an error.
- Unknown keys, duplicate YAML keys, and unknown template placeholders are errors.
- `sarb-aod show-config -c <run.yaml>` prints the fully resolved configuration.

Path templates are `str.format` strings. Granule directories may use `data_root`,
`platform`, `instrument`, `short_name`, `collection`, `cmr_version`, `file_token`, `year`,
`month`, `day`, `doy`, `yyyymmdd`; manifests may use the product fields plus `start`, `end`.

Products and collections are defined in `sarb_aod/data/catalog.yaml`:

| Products | Collection (= CMR version) | Filename token | Format | Status (2026-10-10) |
|---|---|---|---|---|
| MOD04_L2, MYD04_L2 | 6.1 | `061` | HDF4 | available |
| MOD04_L2, MYD04_L2 | 7 | `007` | NetCDF4 | not yet published on LAADS (only C7 L1B/geolocation) |
| AERDB_L2_VIIRS_SNPP, _NOAA20 | 2 | `002` | NetCDF4 | available |
| AERDB_L2_VIIRS_SNPP, _NOAA20 | 2.1 | `021` | NetCDF4 | NOAA-20 from 2018; SNPP only from 2026-06 |
| AERDT_L2_VIIRS_SNPP, _NOAA20 | 2.1 | `021` | NetCDF4 | available |

Run configs choose collections per product, e.g.
`stage.products: {MOD04_L2: ["6.1", "7"], AERDB_L2_VIIRS_SNPP: ["2"]}`.

## Staging L2 granules

```bash
sarb-aod stage -c config/runs/modis_l2_200807.yaml --dry-run   # CMR search, counts, sizes
sarb-aod stage -c config/runs/modis_l2_200807.yaml             # download (resumable)
sarb-aod audit -c config/runs/modis_l2_200807.yaml             # re-verify against manifests
sarb-aod stage -c config/runs/modis_viirs_l2_202009.yaml       # Sept 2020 MODIS + VIIRS
```

Default layout (mac profile), following NASA DOI naming `<SHORTNAME>.<token>`:

```text
~/Data/Terra/MODIS/MOD04_L2.061/2008/07/01/MOD04_L2.A2008183.0020.061.2017292033454.hdf
~/Data/Aqua/MODIS/MYD04_L2.061/2008/07/01/MYD04_L2.A2008183.0015.061.2018035100054.hdf
~/Data/Terra/MODIS/MOD04_L2.061/manifests/manifest_20080701_20080731.tsv
```

Granules are bucketed by the acquisition date in their filename (`.AYYYYDDD.`), downloaded
into `<day>/.partial/`, and moved into place only after the byte count matches CMR and the
file starts with the expected HDF4/HDF5 signature. Re-runs skip verified files and replace
truncated or corrupt ones. A collection with no granules in CMR is reported as unavailable,
not as an error.

## Gridding and CAM7 output

Gridding selects one collection per platform/algorithm stream in `grid.streams`.
Default QC follows the retrieval teams; `weighting.method` chooses correlated-error
GLS (default, `rho: 0.5`) or count weighting. The `cam7_v2` QC preset changes QA,
range, MODIS Ocean selection and scaling. The full
[`modis_l2_200807_cam7_v2.yaml`](config/runs/modis_l2_200807_cam7_v2.yaml) run also
selects legacy time and count weighting to reproduce CAM7's diagnostic four-stream
product; the QC preset alone does not change those settings.

Optional source-specific observation corrections apply `AOD_corrected = scale * AOD + offset`:

```yaml
aod_correction:
  terra_dt: {scale: 1.0, offset: 0.0}
  terra_db: {scale: 1.0, offset: 0.0}
```

Keys must match selected `grid.streams`: `terra_dt`, `terra_db`, `aqua_dt`,
`aqua_db`, `snpp_dt`, `snpp_db`, `noaa20_dt`, or `noaa20_db`. Each finite pair
applies to that satellite/algorithm for the entire analysis period. Omitted
streams or coefficients default to scale 1 and offset 0, preserving existing results.
Corrections apply after retrieval QC and uncertainty estimation, before gridding.
Per-pixel uncertainties and QA selection stay unchanged; corrected pixels are not
clipped or screened again. Existing final nonnegative CAM mean clipping still
applies. Negative-pixel counts describe corrected AOD. L2 satellite matchups use
the same source corrections; AERONET observations are unchanged. Gridded uncertainty
can still change through the existing spread/Birge calculations.

C7's separate producer-combined AOD diagnostic retains its native values: mixed
DT/DB pixels do not have a single source-specific correction. The standalone
September verification script's original-retrieval QC audit also remains native,
so it can check the retrieval AOD cap and rejection counts.

NetCDF metadata records the effective source-to-coefficient mapping as JSON in
`aod_correction`, its formula and uncertainty policy, plus the full config.
Changing a source's coefficients invalidates gridding resume; omitted or explicit
identity settings retain existing uncorrected processing hashes. Use a distinct
run-config name to retain corrected and uncorrected outputs. This is a manually
supplied correction, with no fitting or calibration step. Cached L2 matchups can
only be reused when correction settings agree for the retained sources.
Corrections that produce nonfinite AOD, exceed float32 output capacity, or
collide with CAM's missing-data range (AOD ≤ -900 after float32 rounding) fail
before writing grids for that day.

Experimental spatial filtering is disabled by default. Enable it in a distinct run:

```yaml
despeckle:
  enabled: true
  latitude_bands: [[65, 90]]  # Multiple bands are supported.
  radius_km: 150
  aod_min: 0.5
  min_valid_area_fraction: 0.20
  min_valid_neighbors: 5
  spike:
    enabled: true
    mad_multiplier: 3
    min_excess: 0.20
    action: neighbor_mean  # Or mask.
```

These are trial settings. Each source's three-hour field is filtered separately:
isolated high AOD becomes missing; supported high spikes are masked or replaced
by the area-weighted mean of unflagged original neighbors. Missing targets remain
missing. The radius crosses longitude seams and band edges; only targets inside
bands change. Both `aod_weighted_mean_unfiltered` and `aod_weighted_mean_filtered`
are stored in the same file, alongside paired sensor/source means and action/support
flags. The canonical CAM variable `aod_weighted_mean`, original counts and original
uncertainty remain unfiltered. The filtered comparison uses frozen original source
coefficients; filtered uncertainty is unavailable wherever a contributing source
changes. Thin real plumes can be flagged, so production adoption requires review
of the paired results. See the implementation plan for exact rules and flags.

The working value `rho: 0.5` remains provisional. The July 2008 and September 2020
[historical evaluation campaign](docs/validation/evaluation_campaign.md) found that no tested
rho calibrated uncertainty in the three-hour diagnostics. Acceptance now uses
[independent daily regional comparisons](docs/validation/daily_acceptance_campaign.md),
following the revised sampling definition. Daily satellite uncertainty is not inferred
without a temporal covariance model.

```bash
sarb-aod grid -c config/runs/modis_l2_200807.yaml --workers 2
sarb-aod summarize -c config/runs/modis_l2_200807.yaml
sarb-aod validate /path/to/output/directory
sarb-aod show-config -c config/runs/modis_l2_200807.yaml
```

Use `--day YYYY-MM-DD` for one day and `--overwrite` to recompute valid output.
Reruns reuse a day only when all eight files satisfy the contract, have the correct
window times, and match the processing definition, including catalog variable maps.
A failed day returns exit status 1 while successful days remain available.

For monthly orchestration and resumable multi-year runs, use `sarb-aod grid-months`;
see the [production guide](docs/AMI_PRODUCTION.md) for configuration, recovery and
measured development throughput. AMI deployment still requires confirmed site details.

`paths.output` controls the layout; `{run}` defaults to the run-config filename stem.
By default files live in `{data_root}/CAM7/{stem}/{run}/YYYY/MM/DD/` and carry scalar
window-center time, f09 bounds, per-stream counts/means, uncertainty and QC diagnostics.
`show-config` prints CAM's `aod_obs_path` (with the required trailing slash) and
`aod_obs_template`. Summaries use `paths.summary`; their period mean is area-weighted
across count-weighted cell means. Summarization rejects stale processing definitions.

New grid and matchup NetCDFs also carry code/repository revisions, original and
effective configuration, declared LLM development provenance, and the producing
host/Python environment. See [NetCDF provenance](docs/PROVENANCE.md) for fields,
configuration and reproducibility limits. Existing grids keep their original history.

## AERONET caches

```bash
sarb-aod aeronet stage -c config/runs/modis_l2_200807.yaml
sarb-aod aeronet audit -c config/runs/modis_l2_200807.yaml
```

Daily all-site responses are cached under `paths.aeronet`, checked structurally before
atomic replacement, and paired with checksum manifests. Requests use the following
midnight as their end bound and are paced at least six seconds apart. Selected sites
are validated against AERONET's catalog and filtered on reading, so site subsets share
the cache. L2 collocation needs an extra cached day before and after the study period.

## Evaluation and plots

```bash
sarb-aod evaluate -c config/runs/modis_l2_200807_gls_rho05.yaml --level l2
sarb-aod evaluate -c config/runs/modis_l2_200807_gls_rho05.yaml --level grid
sarb-aod evaluate -c config/runs/modis_l2_200807_gls_rho05.yaml --level grid --pairing cam7
sarb-aod plot -c config/runs/modis_l2_200807_gls_rho05.yaml \
  --compare config/runs/modis_l2_200807_count.yaml
```

Evaluation writes NetCDF matchups and CSV/JSON statistics under `paths.evaluation_dir`.
Grid evaluation defaults to `--pairing daily`: independently average all valid daily
AERONET observations and count-weight the day's available satellite cell fields, then
pair by site-day. Acceptance compares equal-site regional daily means (global for the
run gate), with |bias| ≤ 0.03, RMSE ≤ 0.10 and R ≥ 0.8. Regional time-series plots use
the same daily inputs and show middle-50% site envelopes, comparison statistics and
a region/site inset below the curves. Pooled site-day errors remain separate diagnostics.
Standalone plotting reads daily grid and individual-overpass L2 archives; daily
grid diagnostic figure names include `daily_` to distinguish historical figures.
`--pairing default` retains the historical three-hour diagnostic; `--pairing cam7`
reproduces hourly band conversion and nearest-cell pairing. L2 pairing uses a 25 km
radius and ±30 minutes. Configurable observation minima and published expected-error
envelopes live in `evaluation`. Scientific gates use unrounded metrics;
exit status 1 means a gate failed, and status 2 reports an input or processing error.
A failed science gate does not delete its evidence.

Maps default to `plots.suites: [aod_maps, aod_differences]`. AERONET suites are
`aeronet_site_bias`, `aeronet_site_timeseries`, `aeronet_regional_timeseries`,
`aeronet_scatter`, and `evaluation_summary`. Run both evaluation levels before rendering
all AERONET suites. `plots.sites` selects explicit site names or `{select: top_n, n: 20}`;
`plots.regions` selects region keys or `all`. Comparisons can repeat `--compare`.

Each figure has a PDF, PNG preview and protocol report under `paths.plot`. Rendering
fails when the protocol detects invalid scales, rasterization, labels or layout.
Use `sarb-aod plot -c <run.yaml> --prefetch-maps` once on a new machine to populate
`paths.cartopy_data_dir`; ordinary rendering requires the cached coastlines.
For `plots.regional_layout: summary`, that explicit prefetch also caches city
locations. The combined layout places daily time series across the top and
provenance, a context map and daily-mean scatter/statistics below. It uses the
same daily values and acceptance statistics as the existing regional series.
The July C6.1 laptop iteration has a dedicated tracked plot configuration:

```bash
sarb-aod plot -c config/plots/modis_c61_200807_regional_summary.yaml --prefetch-maps
sarb-aod plot -c config/plots/modis_c61_200807_regional_summary.yaml
```

It reuses verified daily matchups from `modis_l2_200807_gls_rho05` and writes
14 PDFs plus previews/protocols into that run's `aeronet_regional_summary/`
directory. `regional_map_padding` controls surrounding geography;
`regional_city_limit` controls the maximum number of labeled cities (zero
disables cities). Defaults retain the original time-series/inset layout.
See the [regional summary guide](docs/REGIONAL_SUMMARY_PLOTS.md).

Campaign measurements, figure inventories and outstanding scientific acceptance
checks are recorded in `docs/validation/`. Reproducing the CAM7 diagnostic four-stream
product is separate from comparison against the external SP1 archive.
The [map evidence](docs/validation/map_plot_verification.md) and
[AERONET plot evidence](docs/validation/aeronet_plot_verification.md) identify the
accepted PDF/PNG sets, inspection results and artifact hashes.

### Despeckle comparisons

Enabled paired outputs support `plots.suites: [aod_despeckle_comparison]` for global
and each configured latitude-band PDF/PNG maps, coverage/action fractions and time series.
The difference uses identical cell-windows and original observation-count weights;
available-sample means and removal fractions are shown separately. Per-window numeric
summaries accompany the figures. `sarb_aod.plots.despeckle.render_window` renders selected
source-level closeups with counts, support and action flags.

For `summarize`, grid `evaluate`, and ordinary plots, choose:

```yaml
evaluation:
  aod_variant: filtered         # default: unfiltered
  sample_support: common        # default: available
```

Common support intersects the paired fields before temporal averaging, preserving identical
site-days and window weights for AERONET comparisons. Daily AERONET still uses all valid
observations that UTC day. Each variant/support selection has separate grid matchup, report,
summary and plot paths. L2 matchups retain their original retrieval interpretation and caches.
Raw counts are fixed sampling weights; they are not counts of independent donor estimates.
