# July 2008 verification

The four baseline runs each contain all 248 three-hour files for July 1–31.
Contract validation, processing provenance and expected UTC window checks pass
for all 992 files. The CAM7 source regression and plot gates pass. **Task 1.10
science acceptance remains unresolved:** the 99.9th percentile of written
three-hour AOD exceeds 3 in every run, and window coverage often exceeds the
approximate 1–10% expectation. No extra filtering was applied to make these
checks pass.

The configurations are `config/runs/modis_l2_200807_{gls_rho0,gls_rho05,count,cam7_v2}.yaml`.
All use Terra and Aqua MODIS C6.1 Dark Target and Deep Blue. The first three
use retrieval-team QC; `cam7_v2` uses its separately specified legacy QC,
unrounded scaling, naive TAI93 time and count-weighted averaging.

## Numeric results and definitions

The monthly field is computed **within each cell** as
`sum(window_mean × window_count) / sum(window_count)`. Its global mean then
uses cosine-latitude weights over cells with data. Monthly p99.9 gives each
populated cell one value. In contrast, the `period` p99.9 column in
[`july2008_summary.tsv`](july2008_summary.tsv) pools all valid **three-hour
cell-window means**, without count or area weighting. This is the written-value
check required by Task 1.10; it must not be replaced by the monthly percentile.

| Run | Monthly global mean | Window coverage range | Written p99.9 | Monthly p99.9 | Monthly cells >3 |
|---|---:|---:|---:|---:|---:|
| GLS, ρ=0 | 0.161083 | 8.091–13.755% | 3.178413 | 2.043363 | 6 |
| GLS, ρ=0.5 | 0.160751 | 8.091–13.755% | 3.176493 | 2.055972 | 6 |
| Count, retrieval-team | 0.171921 | 8.091–13.755% | 3.320614 | 2.394796 | 10 |
| Count, CAM7 reproduction | 0.186574 | 8.675–15.050% | 3.489359 | 2.358703 | 8 |

All monthly global means meet 0.1–0.25. Written minima are zero and maxima are
5.0 for the retrieval-team runs and 4.997000 for the CAM7 reproduction, meeting
the [0, 5] bounds. Coverage exceeds 10% in 162/248 retrieval-team windows and
224/248 reproduction windows. Monthly p99.9 is below 3, but monthly maxima
still exceed 3; this separates the monthly-map language in Task 4.6 from the
explicit written-value gate in Task 1.10.

## Investigation of the upper tail

[`july2008_high_aod.json`](july2008_high_aod.json) records the complete summary,
geographic recurrence, stream contributions and both percentile definitions.
[`july2008_high_aod_examples.tsv`](july2008_high_aod_examples.tsv) contains 96
representative cell-windows: the 12 largest AOD values and 12 largest counts
per run. These selections can overlap.

| Run | Cell-windows >3 | Fraction of valid cells | Count median / p90 / max |
|---|---:|---:|---:|
| GLS, ρ=0 | 1,627 | 0.11223% | 4 / 49 / 151 |
| GLS, ρ=0.5 | 1,626 | 0.11216% | 4 / 49 / 151 |
| Count, retrieval-team | 1,874 | 0.12927% | 5 / 52.7 / 151 |
| Count, CAM7 reproduction | 3,301 | 0.20448% | 5 / 51 / 215 |

For GLS ρ=0.5, 423 high cell-windows have one accepted retrieval, 772 have at
most three, and 34 have at least 100. Repeated high values occur at
39.110° N, 116.25° E and 24.031° N, 70° E (seven windows each), and
56.073° N, 135° E (six windows). These are locations of recurrence, not
attributions to a physical cause or retrieval failure.

On July 4 at 00:00 UTC, the cell at 53.246° N, 157.5° E has GLS ρ=0.5 AOD
3.391101 from 151 accepted retrievals: Terra DT 18, Terra DB 60, Aqua DT 0,
and Aqua DB 73. Their estimable stream means are respectively 4.421480,
2.923493 and 3.078288. Thus the tail includes well-populated cells as well
as isolated observations. Conversely, the largest retrieval-team monthly
mean, 4.336 at 73.979° N, 101.25° W, contains only one retrieval in the entire
month.

Across GLS ρ=0.5 high cell-windows, raw accepted counts are 23.0% Terra DT,
29.7% Terra DB, 21.5% Aqua DT and 25.8% Aqua DB. The reproduction is more DB
dominated: 15.6%, 40.5%, 15.0% and 28.9%, respectively. These are retrieval
count fractions, **not GLS weights**, independent footprints or evidence that
one algorithm is correct. Region rectangles in the JSON overlap. The current
evidence does not justify removing high values; physical interpretation and
the provisional gate remain open.

## Reproduction and plotting evidence

[`test_cam7_reproduction.py`](../../tests/test_cam7_reproduction.py) passes on
three real July 1 granules: Terra 17:00, Aqua 18:20 and Terra 23:55. Its oracle
is the vendored CAM7 `scripts/prepare_modis_era5_grid.py` from commit
`cd1b5d45dbf3ddfb737c3a7153b67cdf2adef254`, executed through its NumPy fallback.
Total and four per-stream counts match exactly; float32 means and standard
errors meet `rtol=1e-6, atol=1e-7`. This is a three-granule source regression,
not a claim of byte-identical archived monthly products. It reproduces CAM7's
diagnostic four-stream product, **not the SP1 archive CAM read in 2008**.

[`july2008_plots.json`](july2008_plots.json) records configured paths, SHA256
hashes, processing metadata and seven passing suite reports for 152 PDF/PNG
pairs. Visual inspection covered all 24 period maps, July 1/15/31 daily maps
for each run, and all four run differences. No clipping, overlapping labels
or artificial longitude seams were found. The count-mode disagreement maps
are correctly empty because GLS χ² is undefined for those products.

## Reproduce

```bash
E=~/miniforge3/envs/sarb-aod/bin
MPLCONFIGDIR=$PWD/.cache/matplotlib $E/python -m pytest tests/test_cam7_reproduction.py
MPLCONFIGDIR=$PWD/.cache/matplotlib $E/python scripts/investigate_high_aod.py \
  --config config/runs/modis_l2_200807_gls_rho0.yaml \
  --config config/runs/modis_l2_200807_gls_rho05.yaml \
  --config config/runs/modis_l2_200807_count.yaml \
  --config config/runs/modis_l2_200807_cam7_v2.yaml \
  --threshold 3 --output docs/validation/july2008_high_aod.json
```

Run `sarb-aod summarize -c <config>` for each configuration to regenerate its
configured summary, and `sarb-aod plot -c <config> --compare <other-config>`
for maps and comparisons. All data paths come from the configurations.
