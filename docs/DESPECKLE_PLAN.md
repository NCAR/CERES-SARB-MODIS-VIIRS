# Spatial despeckling: July 2008 MODIS C6.1 laptop trial

Status: **implemented; July laptop trial complete** (2026-10-11). Tasks 6.1–6.5
provide configuration/geography, source filtering, paired NetCDF output, comparison
tools and the three-run July experiment. Filtering remains disabled by default;
scientific promotion is open. See [the measured results and limits](validation/july2008_despeckle.md)
and [the task/commit record](IMPLEMENTATION_PLAN.md), Phase 6.

## Objective and scope

Identify suspicious high-AOD cell-windows inside configurable latitude bands:

- **Spatial isolation:** high AOD with insufficient nearby valid data → missing.
- **Local spike:** high AOD substantially above a well-supported neighborhood →
  replace with an area-weighted mean of acceptable neighbors, or mask if configured.

When enabled, write **unfiltered and filtered AOD into the same three-hour
NetCDF file**. Preserve the original values and flags so every change is auditable.
“Unfiltered” means after ordinary retrieval QC, any configured affine correction,
and gridding, but before this spatial filter; it does not mean raw Level-2 AOD.

The first experiment is local July 1–31, 2008: Terra/Aqua × Dark Target/Deep Blue,
Collection 6.1, f09, three-hour windows, retrieval-team QC and GLS rho=0.5. Start
with 65–90° N. Support multiple bands in code and synthetic tests; a southern
band can be a separate sensitivity run. Retain identity affine corrections to
isolate the filter's effect. Inputs and existing outputs remain read-only.

For this comparison trial, **`aod_weighted_mean` continues to contain the
unfiltered CAM product**. Explicit `_unfiltered` and `_filtered` variables remove
ambiguity for comparison tools. Promoting filtered AOD to the CAM-read variable
is a subsequent decision after inspection and uncertainty assessment; this plan
does not change AMI jobs, deploy to AMI, or launch a CAM simulation.

Temporal smoothing, filling cells that were originally missing, automatic
threshold fitting, and AERONET-fitted VIIRS/MODIS bias correction remain deferred.
The supplied C7 combined-product diagnostic keeps its native semantics.

## Evidence and scientific limits

The [July verification](validation/july2008_verification.md) found both sparse
and well-populated high-AOD cells. Its largest retrieval-team monthly mean,
4.336 at 73.979° N, 101.25° W, had one retrieval in the entire month. This is a
specific case to inspect, not a label of known bad data. High AOD and sparse
coverage can also represent real aerosol events through cloud gaps.

NASA describes both residual cloud contamination and dense smoke mistakenly
screened as cloud in its [Dark Target algorithm documentation](https://darktarget.gsfc.nasa.gov/atbd-appendices).
The proposed local-spike statistic uses median absolute deviation (MAD), a robust
spread measure; see [SciPy's definition](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.median_abs_deviation.html).
The thresholds below are **trial values**, not retrieval-team recommendations or
validated scientific defaults. A lower mean, smoother picture, or improved global
metric alone does not establish that the removed observations were erroneous.

## Configuration

One strict pydantic `DespeckleConfig` in `RunConfig` is disabled by default.
Settings apply to all selected sources and dates, separately for each source.
The following configuration and paired gridding are implemented; numerical values
remain experimental after the laptop assessment; independent accuracy evidence is still needed.

```yaml
despeckle:
  enabled: true
  latitude_bands:
    - [65.0, 90.0]
    # - [-90.0, -65.0]  # Optional second band, tested separately.
  radius_km: 150.0
  aod_min: 0.5
  min_valid_area_fraction: 0.20
  min_valid_neighbors: 5
  spike:
    enabled: true
    mad_multiplier: 3.0
    min_excess: 0.20
    action: neighbor_mean  # neighbor_mean | mask
```

Validation: finite ordered band endpoints within [-90, 90], positive finite
radius (at most half the Earth's circumference), nonnegative finite AOD threshold,
coverage fraction in (0, 1], integer neighbor minimum >=3, positive finite spike
multiplier and minimum excess, and known action names. Enabled filtering requires
at least one band. Canonicalize overlapping/touching bands into a sorted union;
include endpoints and select target cells by their latitude centers. No filter
can act outside that union. Omitted configuration and `enabled: false` preserve
legacy numerical results, required variables, and processing hashes.

## Algorithm and pipeline order

### 1. Preserve the baseline and prepare source fields

Run existing QC → uncertainty estimation → affine correction → accumulation and
GLS/count aggregation without modification to its numerical order. Preserve the
original combined product, sensor products, source means, counts and uncertainties.
For each source, use its estimable three-hour gridded AOD before final CAM clipping.
A missing estimate or failed existing minimum-count requirement is never a candidate
for filling. Treat missing values as missing, never as zero AOD.

Expose the existing per-source combination coefficients through `CombinedGrid.stream_weight`
in `grid/combine.py`, without changing baseline arithmetic:

- Count mode: source coefficient is its accepted pixel count N.
- GLS mode: source coefficient is 1/V, using the existing within-source variance
  model, sigma floor and rho. A source without an estimable mean has coefficient 0.

Protect this refactor with existing count/GLS and CAM7 reproduction regressions.

### 2. Build geographic neighborhoods once

Use great-circle distance between f09 cell centers and a radius in kilometres.
Precompute sparse neighbor indices for target cells and reuse across sources and
windows; do not allocate a dense global cell-by-cell distance matrix. An optional
spatial index must use a declared dependency; a latitude-bounded NumPy search is
also sufficient at this grid size. Do not introduce a DAVINCI/monetio dependency.

Exclude the target cell. Neighbors may lie outside the target latitude bands or
cross 0/360° longitude. Include both land and ocean because the source field is
land/ocean AOD. Compute finite cell areas from latitude/longitude bounds:
`R² × delta_longitude_radians × (sin(north) - sin(south))`.

Use full areas of cells whose centers lie within the radius: this is an explicit
center-in-radius approximation, not exact intersection with a circular footprint.
At ±90°, treat the pole-row cells as distinct finite-area longitude wedges;
deduplicate indices, include all qualifying wedges and never use zero-area
cosine-of-center weights. Test polar and longitude-rotation behavior explicitly.

### 3. Classify from the original source field

For each valid target cell i inside a selected band:

1. If AOD_i < `aod_min`, leave it unchanged.
2. Compute valid neighbor count and support fraction
   `sum(area_valid_neighbors) / sum(area_all_neighbors)`.
   If the neighborhood is empty, support is zero. If support or neighbor count
   is below its threshold, flag the candidate **isolated** and mask it.
3. Otherwise, when spike detection is enabled, compute the area-weighted median m
   of valid neighbor AOD, then area-weighted median absolute deviation d about m.
   Define each weighted median as the first sorted value whose cumulative weight
   reaches half the total; use the same definition in reference tests.
4. Flag a high spike only when
   `AOD_i - m > max(min_excess, mad_multiplier × 1.4826 × d)`.
   This is a one-sided high-spike rule. Zero MAD is handled by `min_excess`.
   These are robust thresholds, not calibrated Gaussian significance tests.

Perform classification once from the original fields. Do not recalculate flags
following a removal or replacement. Retain coherent high-AOD neighborhoods;
synthetic gradients, narrow plumes and plume edges must be part of testing.

### 4. Replace or mask flagged spikes

For `action: mask`, mark the spike missing. For `neighbor_mean`, use valid
original neighbor values excluding the target and any neighbors independently
flagged by this pass. Candidates outside configured bands are not filtered and
remain eligible donors. No replacement value may become a donor in the same pass.

Recheck both neighbor count and valid-area support after excluding flagged donors,
using the original geometric denominator. If either fails, mask the target and
record **insufficient replacement support**. Otherwise replace with
`sum(area_j × original_AOD_j) / sum(area_j)` over the remaining donors. Record
donor count and support. Initially missing cells remain missing in every case.

### 5. Form filtered products for the comparison trial

Combine filtered source means with the original source coefficients from step 1,
setting a coefficient to zero where that source was masked. Replacements keep the
original target coefficient. Re-normalize over surviving sources; no survivors
means missing. Reapply the existing `min_pixels` requirement using original
accepted counts of unmasked sources. Preserve every originally missing aggregate
cell; do not recover cells that originally failed aggregation requirements.
Repeat for MODIS-only and VIIRS-only companions using their own source subsets.
Where no contributing source changes, copy the baseline aggregate directly;
recompute only affected cells so no-op and outside-band outputs remain exact.

**Frozen coefficients are an explicit experimental choice:** replacing a value
with a neighborhood estimate does not establish a new retrieval variance or a
new independent observation count. These coefficients isolate the value/masking
effect for this trial. They are not a calibrated uncertainty model for filtered AOD.

Apply existing final nonnegative clipping consistently to paired combined/sensor
fields; retain the existing unclipped convention for paired source means. Ensure
an enabled no-op filter produces exactly equal stored pairs. Raw source counts,
negative/rejection diagnostics, primary counts and primary uncertainty remain
attached to the unfiltered baseline.

For `aod_uncertainty_filtered`, copy baseline uncertainty only where no contributing
source was altered; use fill for masked products or any altered contributing source.
Do not invent a replacement sigma, recompute a purported GLS precision, or count
neighbor estimates as additional L2 retrievals. Daily uncertainty remains unavailable
under the existing temporal-covariance policy. Production filtered uncertainty and
any revised source weighting are open scientific items, not blockers for the trial.

## NetCDF output and provenance

With filtering enabled, every window file contains these fields on `(lat, lon)`:

| Variable | Meaning |
|---|---|
| `aod_weighted_mean` | Existing unfiltered CAM-read product, unchanged |
| `aod_weighted_mean_unfiltered` | Explicit copy of that baseline after ordinary final clipping |
| `aod_weighted_mean_filtered` | Experimental source-filtered combination |
| `aod_weighted_mean_modis_unfiltered` / `_filtered` | Paired MODIS-only products |
| `aod_weighted_mean_viirs_unfiltered` / `_filtered` | Paired VIIRS-only products; fill when no VIIRS is selected |
| `aod_mean_<stream>_unfiltered` / `_filtered` | Paired source means; existing source variables remain baseline |
| `aod_despeckle_flag_<stream>` | Integer action flag; see below |
| `aod_despeckle_support_fraction_<stream>` | Original valid-neighbor area fraction for examined candidates |
| `aod_despeckle_neighbor_count_<stream>` | Original valid-neighbor cell count for examined candidates |
| `aod_despeckle_donor_count_<stream>` / `aod_despeckle_donor_fraction_<stream>` | Actual donor support for replacement attempts |
| `aod_despeckle_flags` | Aggregate bit mask identifying source actions affecting the primary combination |
| `aod_uncertainty_filtered` | Baseline sigma for unchanged combinations, otherwise unavailable/fill |

Source action values: 0 unchanged, 1 isolated/masked, 2 spike/replaced,
3 spike/masked by configuration, 4 spike/masked for inadequate donors. Use a
separate negative fill for originally missing source estimates. Valid cells
outside bands have flag 0. Support diagnostics are fill where not examined.
Aggregate bits 1/2/4/8 correspond to those four nonzero actions on contributing
sources; preserve a separate fill for originally missing primary estimates.

Add a typed optional writer block analogous to the existing native C7 block;
`extra_fields` currently infers float fields except `aod_count_*`, so it cannot
correctly express these flags without an extension. Set `flag_values` or
`flag_masks`, `flag_meanings`, wavelength, units, long names and ancillary links.
Use float32 AOD and the existing -999 fill convention; avoid using flag 0 as fill.

Globals record the full effective configuration, filter method/version, geographic
weighting, thresholds, source-wise operation order, frozen-coefficient policy,
uncertainty limits, `cam_aod_variant=unfiltered`, and per-source action totals.
Continue repository/revision, config snapshots, LLM-development and runtime provenance.
Include normalized enabled settings and algorithm version in processing hashes;
require paired fields and valid flag/value relationships before resume succeeds.
Disabling the feature omits the additional block and preserves legacy grid hashes.

## Comparisons and evaluation

Add an `aod_despeckle_comparison` plot suite and variant-aware summary/evaluation
reading. The proposed `evaluation.aod_variant: unfiltered | filtered` defaults to
unfiltered. Variant identity must appear in grid matchup metadata, signatures,
paths and report names to prevent cache collisions. L2 matchups remain original
QC/affine-corrected retrievals and do not acquire a spatially filtered variant.
L2 cache compatibility should not change just because grid filtering changes.

Temporal comparison weighting for the trial uses the **same original per-cell
observation counts** for both fields, excluding windows where the selected field
is missing. This is fixed baseline sampling weight, not a claim that replaced
AOD has that many independent donor observations. Preserve raw counts in NetCDF;
use field validity to exclude masked windows. Carry action status into diagnostics.

Provide both of the following monthly products:

- **Available-sample means:** each variant uses its own valid windows. These show
  the actual retained product and its changed coverage.
- **Common-sample differences:** both means use exactly the same cell-windows,
  weights, and (for AERONET comparisons) matched site-days. Their difference
  isolates changed values; separately map removals, which have no paired value.

Make global and northern-band PDF/PNG maps of unfiltered AOD, filtered AOD,
filtered-minus-unfiltered on common samples, and removed/replaced fractions.
Use the same AOD color levels for both fields, symmetric difference limits and
explicit sampling labels. Add selected three-hour closeups with source means,
counts, support fractions and action flags; a monthly mean can hide the cause.
Use regional/band time series for paired means and coverage/action fractions.
All text, provenance and DAVINCI formatting use existing labeling/protocol tools.

Evaluate both variants using independent UTC daily AERONET means, paired site-days
and equal-site regional averages, consistent with current practice. Report both
own-coverage results and a common-site-day comparison; the latter must also use
common satellite windows within each site-day. Give station/day counts and action
fractions. Sparse Arctic reference coverage limits claims even if global metrics
improve. Do not tune to the historical AOD upper-tail gate or smoother-looking maps.

## Implementation tasks

| Task | Work | Completion evidence |
|---|---|---|
| 6.0 | Record this design, master-plan dependencies and revised deferrals | Documentation checks, review, commit/push; no data run |
| 6.1 | Config schema, normalized bands, geographic area/neighborhood helper | Invalid-config, dateline, poles, radius, multiple-band and cache tests; disabled hash regression |
| 6.2 | Pure source filter plus baseline coefficient extraction and filtered combination | Analytic support/MAD/mean tests, source isolation, no-op identity, fixed-weight count/GLS regressions |
| 6.3 | Day pipeline, typed dual-output block, validator, provenance and resume | End-to-end synthetic windows, same-file pairs, flags, uncertainty limits, failure/atomic-write and compatibility tests |
| 6.4 | Paired plots, summaries and daily evaluation with distinct variants | Planted common/own-sample comparisons, cache separation, masks/counts, PDF protocol checks and inspected previews |
| 6.5 | Track laptop trial configs; process selected days then July; assess sensitivity | 248 complete paired files per selected monthly run, baseline equivalence, numerical inventories, inspected plots and written scientific assessment |

Suggested files: `sarb_aod/despeckle.py` (pure geometry/filter functions),
`config/schema.py`, `grid/combine.py`, `pipeline.py`, `contract.py`, `summarize.py`,
`evaluation/{collocate,report}.py`, and `plots/{suites,maps,labeling,protocol}.py`.
Add focused tests to new `tests/test_despeckle.py` and existing integration suites.
Use one reviewed commit per task on main and update the master task table.

Essential planted cases: isolated high and low cells; a high spike in uniform
low AOD; zero MAD; supported uniform high AOD; gradual/narrow plumes; invalid/fill
neighbors; insufficient donors after simultaneous classification; an empty or
all-missing field; mixed source actions; overlapping/disjoint bands; band edges;
longitude seam; both poles; zero/negative valid AOD; and single/multiple sources.
Verify unchanged inputs and unchanged outside-band outputs, no creation of data
at missing targets, deterministic one-pass results, donor bounds on replacements,
and no-op stored pairs equal to a disabled control for count and GLS.

## Laptop experiment and acceptance

Use the `mac` profile and existing `~/Data` C6.1 trees. Create full tracked run
configs during Task 6.5, based on `modis_l2_200807_gls_rho05.yaml`; the loader has
no run-config inheritance. Suggested names:

- `modis_c61_l2_200807_f09_3hr_gls_rho05_despeckle_off.yaml`
- `modis_c61_l2_200807_f09_3hr_gls_rho05_despeckle_n65_mask.yaml`
- `modis_c61_l2_200807_f09_3hr_gls_rho05_despeckle_n65_mean.yaml`

The mask run enables isolation and spike detection with spike `action: mask`;
the mean run uses identical thresholds and `action: neighbor_mean`. Each enabled
run automatically includes its own unfiltered fields. Use distinct run names and
configured output paths so archived baseline grids and plots are never overwritten.

First inventory existing baseline outputs and candidate cell-windows using small
output reads. Include the known 73.979° N monthly extreme, a supported high-AOD
control, band-edge and ordinary low-AOD examples. Record selection rules and UTC
windows before examining filter outcomes. Start with their parent days; expand
to all 31 days only after paired-output invariants and plot inspection pass.

Once implemented, use env binaries directly (commands below are future run steps):

```bash
E=~/miniforge3/envs/sarb-aod/bin
export MPLCONFIGDIR="$PWD/.cache/matplotlib"
RUN=config/runs/modis_c61_l2_200807_f09_3hr_gls_rho05_despeckle_n65_mean.yaml
$E/sarb-aod show-config -c "$RUN"
$E/sarb-aod grid -c "$RUN" --day 2008-07-01
$E/sarb-aod grid -c "$RUN" --workers 2
$E/sarb-aod summarize -c "$RUN"
$E/sarb-aod plot -c "$RUN"
```

The July 1 smoke command is illustrative; the recorded candidate days also run
before the full month. Reuse cached AERONET data; fetch only missing required days
through the existing acquisition path. Benchmark one day and record wall time,
peak memory and extra output size before launching the monthly run. Do not run
long input traversals or duplicate downloads just to plan this experiment.

Test a small one-factor sensitivity set on the selected days: radius 100/150/200 km,
support fraction 0.10/0.20/0.30, and spike multiplier 3/4 with the other settings
held fixed. Retain a manifest of every tested setting/result. Only configurations
chosen from this inspection proceed to full-month runs; keep the stated baseline
thresholds as an explicit reference even if a different setting is preferred.

Technical acceptance requires all offline pytest, mypy, Black/isort and review
gates; all 248 time windows validated; unfiltered fields matching a current-code
disabled control at stored precision; no changes outside the selected bands; no
unexplained masks/flags; deterministic reruns/resume; and passing plot protocols
with visually inspected PNGs. Compare older archived baseline fields when their
processing definition matches, rather than demanding identical file metadata.

Scientific assessment must report coverage by source/band/day, action fractions,
AOD distributions and common-sample differences, sparse-cell recurrence, plume
controls, and AERONET daily skill with sample counts. Keep any lack of improvement
or removal of plausible plumes explicit. Complete the experiment even if filtering
is not scientifically accepted; leave filtering disabled in ordinary configs.

Track results and small tables/manifests in `docs/validation/july2008_despeckle.md`
and companion TSV/JSON files, including config/input/output paths, revisions,
thresholds, runtime/storage and figure checksums. NetCDF/PDF/PNG artifacts stay in
configured laptop data/output directories. Final recommendation should identify
which settings, if any, merit subsequent production and uncertainty work.
