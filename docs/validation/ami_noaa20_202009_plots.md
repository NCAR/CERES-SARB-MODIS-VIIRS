# September 2020 NOAA-20 AMI completion and plots

Verified and delivered 2026-10-11 (Task 5.7). The gridding completed successfully,
and the monthly spatial map and regional summaries were rendered interactively
on AMI. Plot publication checks pass. The independent daily AERONET comparison
fails the global bias criterion; these plots do not establish scientific acceptance.

## Run and completion

- Run: `viirs_noaa20_c2_l2_202009_f09_3hr_gls_rho05`.
- Grid config: `config/runs/viirs_noaa20_c2_l2_202009_f09_3hr_gls_rho05.yaml`.
- Plot config: `config/plots/viirs_noaa20_c2_202009_regional_summary.yaml`.
- Profile: `ami-viirs-noaa20`; only NOAA-20 Dark Target and Deep Blue, both
  Collection 002. DT is product v2.0.1 and DB is v2.0.0. This differs from the
  laptop mixed-sensor baseline, which uses DT Collection 021.
- Inputs: 8,240 archive granules (3,981 DT and 4,259 DB) under
  `/ASDC_archive/VIIRS/J1/002/{year}/{doy}`.
- Processing: f09, eight three-hour windows/day, retrieval-team QC, GLS with
  rho 0.5 and Birge inflation, identity observation corrections, no despeckling.
- Grid revision: `11cd1410977114cac1b5ddef362f3fc77577bf2f`.
- Plot revision: `65f556d0cf19758876d4943fb2c242645b27c55a` (Task 4.22).
- Processing SHA-256:
  `1dd872ae13a6698d73f14da8036f04f05b2c654428751af8e430d3388c97c3cf`.

Grid Engine job **5034005**, array tasks 1–30, used four concurrent serial slots
in `scf.q`. Every task has `failed=0` and `exit_status=0`; no user jobs remained
in `qstat` at verification. The first task started at 20:26:34.443 UTC and the
last ended at 20:32:49.825 UTC: **375.382 seconds** elapsed.

All **240 expected NetCDF files** passed CAM7 contract, window-time and
processing-provenance checks. No window was empty. Total output is
**828,918,960 bytes (0.77199 GiB)**. All 30 task logs report eight files written
without error lines. The read-only checks did not rerun gridding.

Files remain under:

```text
/CERES/sarb/dfillmor/MODIS_VIIRS/grids/
  viirs_noaa20_c2_l2_202009_f09_3hr_gls_rho05/2020/09/DD/
```

[Completion evidence](ami_noaa20_202009_completion.json) retains scheduler
accounting, counts, bytes and the NetCDF verification summary. Accounting times
are America/New_York; the timestamps above are UTC.

## Plot delivery

The monthly figure has a 2×2 composition: merged VIIRS upper left, Dark Target
upper right, provenance lower left, and Deep Blue lower right. It uses a shared
550 nm AOD scale and a colorbar below the right column. Each regional page has
the daily time series across the top; provenance, geographic context with cities
and analysis box, and scatter/statistics occupy the bottom row.

Only two review PDFs were placed on the laptop Desktop:

1. `~/Desktop/VIIRS_NOAA20_C002_AOD_Monthly_Mean_2020-09.pdf` (one page).
2. `~/Desktop/VIIRS_NOAA20_C002_AOD_Regional_Summaries_2020-09.pdf`
   (15 pages, with region bookmarks). Global, Western US and Labor Day Smoke
   come first; the remaining regions follow.

The monthly PDF is an unchanged SCP copy. The regional PDF joins unchanged
source pages with pypdf 6.20.0, installed temporarily in the laptop's gitignored
`.cache/pdf_tools` and archived after use; it adds no project dependency. Page dimensions, extracted
text and bookmark destinations were checked against every source page.
The complete ordered sources and Desktop checksums are in
[the Desktop manifest](ami_noaa20_202009_desktop_pdfs.json).

AMI originals and PNG/protocol companions are under:

```text
/CERES/sarb/dfillmor/MODIS_VIIRS/plots/
  viirs_noaa20_c2_l2_202009_f09_3hr_gls_rho05/
    monthly_mean/
    aeronet_regional_summary/
```

These two directories and the daily evaluation artifacts were copied to matching
`~/Data/plots/<run>/` and `~/Data/evaluation/<run>/` directories on the laptop.
All **76 transferred artifacts** match remote byte counts and SHA-256 hashes;
see [the transfer manifest](ami_noaa20_202009_plot_artifacts.json). All **16 final
plot protocols pass** and all PNG previews were visually inspected. The booklet's
Global, Western US and smoke-event pages were also inspected after assembly.

The initial `evaluate` command also produced `aeronet_regional_summary_grid_daily`
figures. The delivered pages are the subsequent `plot` outputs in
`aeronet_regional_summary`: these read the saved matchup NetCDF, so provenance
identifies its pathname rather than an in-memory input. Both use the same daily
data and statistics. Initial evaluation plots remain diagnostic artifacts and
are not additional Desktop deliverables.

## Daily comparison and limits

Satellite and AERONET UTC daily means are calculated independently, paired by
site-day, and averaged with equal active-site weight per regional day. Shading
is the middle 50% of sites, not an uncertainty interval. Bias is VIIRS minus
AERONET. The monthly spatial map averages grid windows by observation count;
its spatial mean is not the site-sampled regional statistic.

| Region | Days | Site-days | Bias | RMSE | R |
|---|---:|---:|---:|---:|---:|
| Global | 30 | 6,640 | −0.04035 | 0.05735 | 0.92737 |
| Western US | 30 | 1,250 | −0.1793 | 0.2924 | 0.9071 |
| Labor Day Smoke (September 8–14) | 7 | 81 | −1.1709 | 1.2100 | 0.9827 |

Global RMSE and correlation pass the current thresholds; absolute bias exceeds
0.03, so the global science gate fails. Western US and the smoke-event box have
substantial negative bias. The event page intentionally shows only its seven-day
analysis interval within the full-month axis. No correction was fitted or applied
as part of this plotting task. The Southern Ocean remains a latitude band with
mostly land/coastal AERONET sites, without an ocean mask.

[Full-precision statistics and gates](ami_noaa20_202009_daily_statistics.json)
retain the global and all regional results. This Collection 002 NOAA-20-only run
is separate from the earlier mixed-sensor acceptance campaign.

## Reproduction on AMI

Use Bash with the repository at the plot revision above (or a later compatible
revision). The production environment's binaries are called directly:

```bash
cd "$HOME/CERES-SARB-MODIS-VIIRS"
export MPLCONFIGDIR="$PWD/.cache/matplotlib" MPLBACKEND=Agg
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 NUMBA_NUM_THREADS=1 MKL_NUM_THREADS=1
E="$HOME/miniforge3/envs/sarb-aod/bin"
CONFIG=config/plots/viirs_noaa20_c2_202009_regional_summary.yaml

# Evaluation writes daily matchups and reports; this run's science gate fails.
# Continue to plot the saved matchups even when evaluate exits for that gate.
"$E/sarb-aod" evaluate -c "$CONFIG" --level grid --pairing daily
"$E/sarb-aod" plot -c "$CONFIG"
```

The existing laptop September AOD20 cache (60 files, all 30 days audited on AMI)
and Natural Earth populated-place layer were copied into the AMI profile's data
root before rendering. No L2 downloads or directory-tree scans were performed.
The monthly map was selected explicitly to avoid generating daily map suites:

```python
import matplotlib.pyplot as plt
from sarb_aod.config.loader import load_config
from sarb_aod.plots.style import ncar_style
from sarb_aod.plots.suites import (
    MapFigureSpec, aggregate_maps, render_map_figure, save_figure, write_protocol_report,
)

cfg = load_config('config/plots/viirs_noaa20_c2_202009_regional_summary.yaml')
aggregate = aggregate_maps(cfg)
spec = MapFigureSpec('monthly_mean', 'mean_aod', cfg.period.start, cfg.period.end, 'mean_aod')
with ncar_style(cfg.plots.context):
    figure = render_map_figure(cfg, aggregate, spec)
    try:
        artifact = save_figure(cfg, figure, suite=spec.suite, name=spec.name,
                               start=spec.start, end=spec.end)
    finally:
        plt.close(figure)
write_protocol_report(cfg, spec.suite, [artifact], expected_figures=[spec.key])
```

Task 4.22 code/config changes were developed, tested, reviewed, committed and
pushed on the laptop, then pulled on AMI before rendering. Task 5.7 records the
delivery separately, preserving the actual rendering and original gridding
revisions in provenance. Its final offline gate passes 1,055 tests (one network
test deselected); the known NumPy binary-size warning remains unchanged.

Mypy, Black, isort and whitespace checks pass. The required
`codex review --uncommitted` found no actionable defects after verifying artifact
hashes, statistics, acceptance gates, processing hashes and reproduction imports.
