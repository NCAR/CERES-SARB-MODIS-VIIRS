# September 2020 smoke verification

Task 2.4 examines 2020-09-08 through 2020-09-14, inclusive, inside
125–116° W and 36–49° N. September staging was audited complete at 29,856
granules before this verification. The two mixed configurations select MODIS
C6.1, VIIRS DT v2.1, and VIIRS DB v2; the version comparison substitutes
NOAA-20 DB v2.1. Both use retrieval-team QC, AOD limits [−0.05, 5], and GLS
with ρ = 0.5 and Birge inflation.

Reproduce from the repository root:

```bash
MPLCONFIGDIR=$PWD/.cache/matplotlib ~/miniforge3/envs/sarb-aod/bin/python scripts/verify_september2020.py
```

The script accepts repeated `--config` paths and reads all input/output locations
from those configurations. `--grids-only` refreshes the grid checks without
rereading the Level-2 granules.
The accompanying `sept2020_summary.retrieval.json` stores resolved catalog
recipes, QC, time, uncertainty, edge-granule settings, input paths, and the
summary's content digest. Cache reuse rejects changes to that definition or
to the TSV; a changed reader configuration requires a new Level-2 scan.

`sept2020_summary.tsv` reports exact pooled-pixel quantiles after full QC, with
one row per stream and collection. Spatial boundaries are inclusive; UTC days
are half-open and use the production reader's time conversion and configured
adjacent-day-granule setting. MODIS and VIIRS DB land/ocean overlap follows the production reader's
QC-first, land-priority rule. `n_qc_passed` counts the accepted pixels used for
p50, p90, and maximum; `n_at_or_above_max` counts accepted pixels exactly at the
inclusive limit plus any separately rejected, QA-passing pixels above it.
`n_rejected_high` records only the latter. Fill and native `valid_range` exclusions
remain excluded from every statistic.

`sept2020_grid_windows.tsv` records each available three-hour mixed-grid window.
The convexity check compares every valid mixed cell with its MODIS-only and
VIIRS-only companion means, using a 1e-6 absolute tolerance. Each companion uses
exactly that mixed configuration's sensor streams and collections, including the
NOAA-20 v2.1 substitution. Disagreement diagnostics use grid centers within the
smoke box; rejection counts are reported both globally and within the box.
`sept2020_verification.json` records gates and completeness independently.

## Native-range constraint on the rejection gate

The first 2020-09-08 granule of every selected stream/collection was inspected.
MODIS DT/DB and VIIRS DT AOD fields use a packed upper `valid_range` of 5000
with scale factor 0.001; VIIRS DB land and ocean fields have an upper physical
`valid_range` of 5. MODIS packed values are rounded to six decimal places by the
retrieval-team preset, restoring the inclusive endpoint to exactly 5.

Consequently, the requested `aod_count_rejected_high > 0` acceptance gate is
structurally incompatible with these native ranges and the default maximum 5.
Pixels at 5 are valid endpoint retrievals, not above-limit rejections. This
verification preserves that distinction and does not tune QC or ignore native
validity metadata to manufacture a passing gate. Task 2.4 must not be recorded
as scientifically accepted while this gate remains unmet.

## Measured results

All platform comparisons show a higher DB p90 than DT p90:

| Platform | DT p90 | DB v2/C6.1 p90 | DB v2.1 p90 |
|---|---:|---:|---:|
| Terra | 2.212 | 3.390 | — |
| Aqua | 2.099 | 3.478 | — |
| SNPP | 1.740 | 3.167 | — |
| NOAA-20 | 1.859 | 3.338 | 3.278 |

The baseline mixed run has all 56 three-hour windows for the smoke week.
χ²/ν exceeds 1 in 871 smoke-box cell-windows, reaching 46.873. All 202,245
cell-windows with both sensor companions globally, including 1,160 in the
smoke box, satisfy the mixed-mean bound. Missing mixed values where both
companions exist count as failures; none occurred.

There are 850 accepted pixels exactly at AOD 5 across the eight baseline
streams in the smoke box. Both the pixel diagnostics and all baseline mixed
grid windows report zero above-limit rejections, so the rejection gate fails.

NOAA-20 DB v2.1 retains 168,072 pixels versus 180,146 for v2 in the smoke box
(−6.70%). Its median decreases from 0.382137 to 0.298281 and p90 decreases
from 3.337785 to 3.277747 (−1.80%). The v2 maximum is 5, with 275 pixels at
that endpoint; the v2.1 maximum is 4.949651, with no endpoint pixels.
These are distributions over each version's own accepted pixels, not paired
pixel differences.

The NOAA-20 DB v2.1 mixed run also has all 56 smoke-week windows. χ²/ν exceeds
1 in 870 smoke-box cell-windows, reaching 48.158. All 202,154 cell-window
comparisons globally, including 1,159 in the box, satisfy the sensor bound;
there are no missing mixed values when both companions exist. Its above-limit
rejection count is also zero.

Both mixed runs pass the DB/DT distribution, disagreement, and sensor-bound
gates. All 112 requested windows are present and checked. The requested overall
Task 2.4 acceptance remains unmet because the above-limit rejection gate fails
under the native-range constraint described above.
