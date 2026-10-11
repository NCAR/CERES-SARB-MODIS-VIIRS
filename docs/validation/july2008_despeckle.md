# July 2008 MODIS C6.1 despeckle trial

Completed on the development laptop, 2026-10-11. The implementation and technical
trial pass; scientific promotion remains open. Filtering stays **disabled by
default**, and `aod_weighted_mean` remains the unfiltered CAM input even in enabled
runs. Each enabled file also contains explicit unfiltered/filtered product,
sensor and source fields, action/support diagnostics and filtered uncertainty.

## Experiment and reproducibility

Terra/Aqua × Dark Target/Deep Blue; C6.1; f09; eight UTC three-hour windows per day;
retrieval-team QA; GLS rho=0.5; identity affine corrections. The source-level
filter acts only at cell centers in 65–90°N, independently in each window. It uses
150 km neighborhoods, AOD ≥0.5 candidates, at least five valid neighbors and 20%
valid area. Supported spikes exceed the area-weighted median by more than
max(0.2, 3 × 1.4826 × weighted MAD). The replacement run uses the area-weighted
mean of original, unflagged donors and rechecks their support. The mask run masks
those spikes instead. Isolated candidates are masked in both runs.

All three full configs are tracked under `config/runs/`:

| Config suffix after `modis_c61_l2_200807_f09_3hr_gls_rho05_despeckle_` | Files | Output bytes | Wall time, two workers |
|---|---:|---:|---:|
| `off.yaml` | 248 | 1,067,919,496 | 74.7 s |
| `n65_mask.yaml` | 248 | 1,166,804,889 | 89.0 s |
| `n65_mean.yaml` | 248 | 1,166,862,031 | 90.1 s |

The paired fields add approximately 9.3% to the control's file volume. The first
single-day mean-filter run took 6.46 s, with 868,794,368 bytes maximum resident set
size reported by `/usr/bin/time -l`. Full-run timing/RSS records are in
[runtime.json](despeckle_200807/runtime.json); these are command measurements, not
a controlled performance comparison. All 31 AERONET days and map assets were
already cached; no input downloads were needed.

The full grids were produced by implementation revision `11c60de`. NetCDF globals
include the complete effective config, original YAML snapshots, algorithm version,
repository/revision, development-agent information and runtime/environment
provenance. Unknown model snapshot/effort values remain explicitly unknown.
The final PDFs include the Task 6.5 layout refinements and were rendered before
that commit, as required for review. Their package source checksums are recorded
in [the rendering code inventory](despeckle_200807/plot_render_code_sha256.json).

Outputs are under `~/Data/CAM7/MODIS_VIIRS_AOD_3hr_f09/<config-stem>/2008/07/DD/`.
PDF/PNG products are under
`~/Data/plots/<config-stem>/aod_despeckle_comparison/`. Large scientific artifacts
are local; small numeric evidence and their checksums are tracked here.
The primary review deliverable is one two-page monthly-mean PDF,
`~/Desktop/MODIS_C61_July2008_Monthly_Means.pdf`: global and 65–90°N
unfiltered/neighbor-mean-filtered maps. The complete diagnostic set remains under
`~/Data/plots/`; it is not part of the Desktop review. The
[review PDF manifest](despeckle_200807/monthly_review_pdf.json) records its checksum
and the two source pages. These are available-sample monthly means; changed
coverage contributes to their differences.

## Technical checks

The final gate passes 1,054 offline tests (one network test deselected), mypy,
Black, isort and whitespace checks. The code review found no actionable
regressions; its 67 focused tests and independent selected-day audit also pass.

- All **744** files pass contract and paired-extension validation.
- All 25 original variables in the 248 new control files exactly match the
  archived `modis_l2_200807_gls_rho05` outputs. Every original variable also exactly
  matches the control in both enabled runs.
- Originally missing targets remain missing; every value outside the latitude
  band is unchanged. Raw counts and original uncertainty remain baseline fields.
  Altered contributing sources make filtered uncertainty unavailable rather
  than supplying an invented donor uncertainty.
- Common-sample AERONET comparisons have identical site-days, original temporal
  counts and contributing-window counts. Grid matchup/report/summary/plot paths
  distinguish variant and sample support; L2 cache identity ignores these
  grid-only choices.

See [the monthly audit](despeckle_200807/monthly_audit.json),
[archive equivalence](despeckle_200807/archive_equivalence.json) and
[the 744-file checksum inventory](despeckle_200807/monthly_files.tsv).
All 40 final plot protocols pass, and every PNG preview was visually inspected.
[The plot inventory](despeckle_200807/plot_artifacts.json) records PDF, PNG and
protocol checksums and source paths.

## Spatial effects

There are 172,436 originally valid northern cell-windows (1,449,709 globally).
Counts below refer to primary-product cell-windows, not individual L2 pixels.
Actions can affect several source streams in the same cell-window.

| Action | Mask run | Neighbor-mean run |
|---|---:|---:|
| Primary product removed | 2,056 (1.19%) | 1,257 (0.73%) |
| Primary product has a replacement action | 0 | 1,234 (0.72%) |
| Any contributing source changed | 2,936 (1.70%) | 2,936 (1.70%) |

Across sources, both runs mask 1,792 isolated estimates. The mean run replaces
1,391 source estimates and masks 123 additional spikes because unflagged donor
support is insufficient. The mask run masks all 1,514 supported spikes. Masking
one source can also change a retained merged value when other sources survive.

The northern area-weighted monthly mean is 0.124005 before filtering and 0.105635
(mask) or 0.105676 (mean) on each variant's available samples. On **identical
cell-windows and original count weights**, the filtered-minus-unfiltered monthly
map averages are only −0.000209 (mask) and −0.001668 (mean). Most of the difference
between available-sample maps therefore comes from changed coverage/sampling;
it should not be interpreted as an equivalent correction to surviving values.
The map averages use each field's observed spatial domain.

The global available-sample map means are 0.160751 before filtering and 0.160051 /
0.160052 after filtering. Full precision, area weighting, and window-level action
inventories are retained in [monthly map metrics](despeckle_200807/monthly_map_metrics.tsv),
[product actions](despeckle_200807/monthly_product_actions.tsv) and
[source actions](despeckle_200807/monthly_source_actions.tsv).

## Preselected cases and sensitivity

[Case selection](despeckle_200807/preselected_cases.json) used only the archived
unfiltered outputs, before examining filter outcomes. July 2, 4, 12, 21 and 23
were processed and inspected before expanding the experiment to the whole month.
The selected outputs were subsequently refreshed in the full run.

- The isolated 4.336 AOD at 73.979°N, 101.25°W on July 23, 03 UTC is masked in both
  runs. Its small detached patch disappears in the source-level closeup.
- The populated northern high-AOD control at 72.094°N, 120°E on July 12, 03 UTC
  stays at 1.033947. The broader coherent feature remains, although some edge
  and detached source estimates are altered. It is a structural control, not
  independently established aerosol truth.
- The high-AOD control outside the band (3.391101), the outside band-edge control
  (0.624013), and the low northern control (0.028021) are unchanged at their
  recorded coordinates. Surrounding high candidates can still change.

Source means, counts, original support and actions for each recorded coordinate
are in [monthly cases](despeckle_200807/monthly_cases.tsv); 20 selected-window
PDF/PNG diagnostics expose the larger neighborhoods.

The one-factor test re-filters **stored float32 source means** for the five
selected days. It tests classification/donor sensitivity and does not reconstruct
GLS coefficients or claim six additional full gridding runs.

| Setting (others fixed) | Isolated masks | Replacements | Insufficient-donor masks |
|---|---:|---:|---:|
| Reference: 150 km, support 0.20, MAD multiplier 3 | 351 | 261 | 29 |
| Radius 100 km | 1,520 | 5 | 1 |
| Radius 200 km | 288 | 300 | 16 |
| Support 0.10 | 336 | 272 | 27 |
| Support 0.30 | 395 | 233 | 27 |
| MAD multiplier 4 | 351 | 228 | 19 |

The 100 km case is unsuitable with the five-neighbor requirement on this grid:
there are only two potential neighboring centers at 65.50°N and four at
72.09°N. Even complete local coverage cannot meet the threshold there. The
150 km reference avoids this structural failure. The tested thresholds have
not been optimized or independently validated; the reference was retained for
both complete-month comparisons.

See [sensitivity settings](despeckle_200807/sensitivity_settings.json),
[per-day/source outcomes](despeckle_200807/sensitivity_source_actions.tsv) and
[geometric neighbor counts](despeckle_200807/geometric_neighbor_counts.json).

## AERONET results and scientific limits

All eight evaluations (two actions × two variants × available/common sampling)
were completed. Independent UTC daily means were paired by site-day, then sites
were weighted equally within each regional day. Metrics use those region-days.

| Scope | Sites | Site-days | Region-days | MB | RMSE | R |
|---|---:|---:|---:|---:|---:|---:|
| Global | 196 | 3,249 | 31 | 0.015121 | 0.020690 | 0.880179 |
| 65–90°N | 7 | 108 | 30 | 0.060221 | 0.067185 | 0.156145 |

**All paired satellite values are exactly unchanged**, for both actions and both
sampling choices. No paired site-day is removed. The northern sites are Andenes,
Barrow, Hornsund, Kangerlussuaq, OPAL, PEARL and Thule. This experiment therefore
provides no AERONET evidence of improvement or harm in the altered locations.
The existing global gate passes, while northern bias and correlation fail; those
facts apply equally before and after filtering.

[Pair-change counts](despeckle_200807/aeronet_pair_changes.tsv),
[unrounded metrics](despeckle_200807/aeronet_metrics.tsv), and
[all configured regions](despeckle_200807/regional_aeronet_metrics.tsv) are tracked.
The complete variant-specific matchup NetCDFs and reports remain in each run's
configured local evaluation directory.

Keep this as an **optional diagnostic filter**. Spatial isolation alone does not
prove a retrieval is false; synthetic narrow-plume tests and the real edge
changes show the risk of removing real aerosol. Additional independent evidence
near affected cells and a defensible uncertainty model would be needed before
promoting the filtered field to CAM input. No AMI deployment or CAM simulation
was performed, and ordinary configs retain their previous behavior.

## Reproduction

Use the direct `sarb-aod` environment binaries and the tracked `mac` profile.
For each of the three configs, run `sarb-aod grid-months -c <config> --workers 2`;
the initial complete runs used `--overwrite` to refresh selected-day provenance.
Then run:

```bash
E=~/miniforge3/envs/sarb-aod/bin
OFF=config/runs/modis_c61_l2_200807_f09_3hr_gls_rho05_despeckle_off.yaml
MASK=config/runs/modis_c61_l2_200807_f09_3hr_gls_rho05_despeckle_n65_mask.yaml
MEAN=config/runs/modis_c61_l2_200807_f09_3hr_gls_rho05_despeckle_n65_mean.yaml
export MPLCONFIGDIR="$PWD/.cache/matplotlib"
export MPLBACKEND=Agg
$E/python scripts/despeckle_trial.py --baseline "$OFF" --mask "$MASK" --mean "$MEAN" \
  --inventory docs/validation/despeckle_200807/preselected_cases.json \
  --destination .cache/despeckle-trial --sensitivity
$E/python scripts/despeckle_evaluate.py --mask "$MASK" --mean "$MEAN" \
  --destination .cache/despeckle-trial
```

Add `--selected-only` to the audit command for the five-day preflight. Individual
closeups use `sarb_aod.plots.despeckle.render_window(config, window_start, Region)`
with the saved case coordinates. The plot artifact inventory records exact
paths and protocol results; previews were inspected before acceptance.
