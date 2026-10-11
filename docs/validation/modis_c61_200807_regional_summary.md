# July 2008 C6.1 regional summaries on the laptop

Rendered on 2026-10-11 with clean plotting revision
`455816cdd08a9df2afc72498f0fdb27fcc76ab39`, using the tracked
[plot configuration](../../config/plots/modis_c61_200807_regional_summary.yaml).
The [layout guide](../REGIONAL_SUMMARY_PLOTS.md) gives the rendering command,
city source, configurable map padding and sampling interpretation.

Each figure has a full-width daily time series above provenance, a context
map and scatter/statistics. The two data panels share the same regional
daily means. Context maps retain the analysis rectangle and active AERONET
sites, and label major cities with slightly larger black markers. Statistics
use the least occupied scatter corner. The South Atlantic has no finite
pairs and is explicitly shown as an empty comparison.

## Deliverables

Canonical root:

```text
~/Data/plots/modis_l2_200807_gls_rho05/aeronet_regional_summary/
```

There are 14 PDFs, 14 PNG previews, 14 individual protocol JSON files and
one passing suite protocol report: 43 files totaling 5,992,802 bytes.
The PDFs total 2,697,431 bytes. The [inventory](modis_c61_200807_regional_summary.tsv)
records every PDF's size, SHA-256 and inspection/protocol status.

The same 14 PDFs were copied into:

```text
~/Desktop/MODIS_C6.1_Regional_2008-07/
```

Every Desktop copy matches its canonical file byte for byte. All region
previews were visually inspected during layout verification; the final
Western US, global and India renders were rechecked after committing.
Every PDF contains the full plotting revision and the input matchup checksum.
The monthly C6.1/C7 Desktop PDFs were refreshed separately with `131cb29`;
their records remain in [the monthly delivery report](ami_c7_200807_plots.md).
This new regional layout was rendered on the laptop for C6.1 iteration.

## Inputs and checks

The input is the existing verified
`~/Data/evaluation/modis_l2_200807_gls_rho05/matchups_grid_daily_20080701_20080731.nc`.
Its SHA-256 is
`90e8c1d6cba2e46d8d80ac272fe400a2e0e70b59e409e29c896d0f0a47f9bce2`.
It contains 3,249 paired site-days and 196 distinct sites. The gridded data
and matchup file were not rewritten. Their processing hash remains
`9845977da679b3129f4accbc37b925c4bde11c415e71dbd235de78688407f112`.
The plot configuration hash is
`f44202ed37ab1c7abf15e511f45f079eb54d6dea278abf22db4a7d1c63f6553e`.

All 951 offline tests, mypy, Black and isort pass. The review's date-limited
city-selection issue was fixed using a spatial-only mask and a regression
test. New tests compare daily values/statistics with the established renderer,
check actual scatter pairs, geographic coverage and marker sizes, exercise
empty/date-limited regions, and enforce cached map assets and finite text
positions. This is a presentation update; scientific acceptance criteria and
the existing regional shortcomings are unchanged.
