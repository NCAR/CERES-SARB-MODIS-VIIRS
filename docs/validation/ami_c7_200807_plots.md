# July 2008 MODIS C7 plots from AMI

Generated on 2026-10-11 on AMI's interactive node
`la2520-hn-grid-ami01-pi.cluster.net`, without a scheduler job. The rendering
code was initially commit `557a1c0`; the monthly spatial figure was refreshed
with commit `d2c94b5` as described below. The plot selection is recorded in the
[July C7 run config](../../config/runs/modis_c7_l2_200807_f09_3hr_gls_rho05.yaml).
The underlying 248 grids are the [completed AMI operational run](ami_c7_200807.md).

## Deliverables and checks

The AMI plot root is
`/CERES/sarb/dfillmor/MODIS_VIIRS/plots/modis_c7_l2_200807_f09_3hr_gls_rho05/`.
The files were copied with `scp` to the same run subdirectory beneath
`~/Data/plots/` on the laptop.

- `monthly_mean/mean_aod.20080701-20080731.pdf`: five panels, with combined
  MODIS at left and Terra/Aqua Dark Target/Deep Blue at right. Monthly means
  weight the three-hour GLS results by their contributing observation counts.
  This plots the primary GLS product, not the separate producer-supplied
  combined-product diagnostic.
- `aeronet_regional_timeseries_grid_daily/daily_region_<region>.20080701-20080731.pdf`:
  global plus the 12 published aerosol regions and the Southern Ocean band.
  The South Atlantic has no finite matched site-days and is explicitly shown
  as an empty comparison. The date-specific 2020 smoke region is omitted.
- Every PDF has a PNG preview and a passing individual protocol JSON. Both
  suite protocol reports pass. All 15 previews were visually inspected for
  labels, layout, curves, bands, coastlines and inset placement.

The 15 PDFs now total 2,221,123 bytes. All 47 delivered files (PDFs, PNGs and
protocol reports) matched AMI by SHA-256 and byte count after transfer;
their total size is 4,540,992 bytes. The
[PDF inventory](ami_c7_200807_plots.tsv) records individual sizes and hashes.
The daily matchup NetCDF, CSV tables and evaluation JSON were also copied
to `~/Data/evaluation/modis_c7_l2_200807_f09_3hr_gls_rho05/`.

### Collection titles, provenance panel and Desktop refresh

Task 4.11 regenerated the C6.1 and C7 monthly maps on 2026-10-11 using
plotting commit `d2c94b5`. The C6.1 plot was rendered on the laptop; AMI
fast-forward pulled the same code and regenerated C7 interactively. Both
aggregate maps now align with the top row and read `MODIS C6.1 AOD` or
`MODIS C7 AOD`. The lower-left cell records grid creation dates/revisions,
plot creation date/revision, weighting and QA preset, configured L2 directory
templates, the grid output template and canonical PDF destination. Full
timestamps, actual grid filenames and QC configuration are embedded in PDF
Keywords. The gridded data and scientific settings were unchanged.

The C7 PDF, PNG and protocol files were copied again from AMI; all 47 C7
artifacts were checked by byte count and SHA-256 against AMI. Both revised
previews were visually inspected and passed the plotting protocol. Canonical
copies remain in each run's plot directory; identical PDFs were placed on
the laptop Desktop:

| Desktop filename | Bytes | SHA-256 |
|---|---:|---|
| `MODIS_C6.1_AOD_Monthly_Mean_2008-07.pdf` | 494581 | `14de6db275def9513a973f8b14338b686b7e0d9d0e6a4960678d5ae0747e2c79` |
| `MODIS_C7_AOD_Monthly_Mean_2008-07.pdf` | 513780 | `106d6ff53cb8f94608c99bfb03b3e23f064d35571e6199c14d21c714e686b59f` |

The C6.1 canonical file is
`~/Data/plots/modis_l2_200807_gls_rho05/aod_maps/mean_aod.20080701-20080731.pdf`;
C7 retains the `monthly_mean/` location documented above.

The final code passes 936 offline tests, mypy, Black and isort. Review's QA
override finding was fixed: the panel marks overrides and full QC settings
are retained in metadata. Regional time-series artifacts retain their
original hashes.

## Daily comparison result

Time series use independent UTC daily satellite and AERONET means, pair
site-days, and average active sites equally. Shading is the middle 50% of
sites, not an uncertainty interval. The global comparison includes 3,392
site-days across 31 days, with 93–130 active sites per day.

| Global daily metric | Value | Gate |
|---|---:|---|
| Mean bias | +0.0142769476 | Pass: absolute bias ≤ 0.03 |
| RMSE | 0.0224769973 | Pass: ≤ 0.10 |
| Correlation | 0.7920111461 | Fail: ≥ 0.80 |
| Normalized mean absolute error | 8.5441% | Diagnostic |

Plot acceptance passes, but this C7 configuration does **not** pass the global
scientific acceptance gate. Regional shortcomings also remain, including
East Asia and India RMSE above 0.10 and sparse sampling in India and the
North Indian Ocean. Task 2.6 still covers C6.1/C7 comparisons, September,
L2 evaluation and uncertainty assessment; these figures do not close it.

## Reproduction on AMI

The laptop's existing July `AERONET/AOD20/2008/07` cache, including its
manifests, was copied under the AMI profile's data root. Both Natural Earth
coastline resolutions were copied from `~/Data/cartopy` to its configured
`cartopy` directory. Daily evaluation verifies the AERONET cache manifests
and all required grid files. No new external downloads are required.

Run from `$HOME/CERES-SARB-MODIS-VIIRS` in Bash with the `sarb-aod` environment:

```bash
export MPLCONFIGDIR="$PWD/.cache/matplotlib" MPLBACKEND=Agg
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 NUMBA_NUM_THREADS=1
E="$HOME/miniforge3/envs/sarb-aod/bin"
"$E/sarb-aod" evaluate \
  -c config/runs/modis_c7_l2_200807_f09_3hr_gls_rho05.yaml \
  --level grid --pairing daily
```

This writes the regional PDFs as part of evaluation. Its CLI exit status is
1 when scientific gates fail, even when all reports and plots were generated
successfully. Inspect the report and protocol status separately. To regenerate
only the single monthly mean map using the existing rendering API:

```bash
"$E/python" - <<'PY'
from pathlib import Path
import matplotlib.pyplot as plt
from sarb_aod.config.loader import load_config
from sarb_aod.plots.style import ncar_style
from sarb_aod.plots.suites import (
    MapFigureSpec, aggregate_maps, render_map_figure,
    save_figure, write_protocol_report,
)

config = load_config(Path('config/runs/modis_c7_l2_200807_f09_3hr_gls_rho05.yaml'))
spec = MapFigureSpec('monthly_mean', 'mean_aod', config.period.start,
                     config.period.end, 'mean_aod')
aggregate = aggregate_maps(config)
with ncar_style(config.plots.context):
    figure = render_map_figure(config, aggregate, spec)
    try:
        artifact = save_figure(config, figure, suite=spec.suite,
                               name=spec.name, start=spec.start, end=spec.end)
    finally:
        plt.close(figure)
write_protocol_report(config, spec.suite, [artifact], expected_figures=[spec.key])
print(artifact.pdf)
PY
```

Aggregation validated all 248 windows in 48.2 s, monthly rendering took 8.7 s,
and daily evaluation plus regional rendering took 83.3 s. Plot settings have
config hash `23777f17aee6c2dbb2559e47d2eedc1e7811e121a1434453ae704ebc52969945`.
The processing hash remains
`f0656a6c81d3dceb02c5353bdd69bf119d817600417fe66167ff52e7a67c2991`;
no gridding or scientific settings changed. The 933 offline tests, mypy,
Black and isort checks pass.
