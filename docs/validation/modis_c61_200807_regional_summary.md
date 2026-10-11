# July 2008 C6.1 regional summaries on the laptop

Rendered on 2026-10-11 with clean plotting revision
`56a764292b6482030425b55567ac33a4cd9af247`, using the tracked
[plot configuration](../../config/plots/modis_c61_200807_regional_summary.yaml).
The [layout guide](../REGIONAL_SUMMARY_PLOTS.md) gives the rendering command,
city source, configurable map padding and sampling interpretation.

Each figure has a full-width daily time series above provenance, a context
map and scatter/statistics. The two data panels share the same regional
daily means. Context maps retain the analysis rectangle and active AERONET
sites, and label major cities with slightly larger light grey markers. Black
city labels render above the orange analysis boundary. Line, band and error
definitions now sit in Provenance. A bold, wrapped DAVINCI expansion ends
that same text block. The page remains 8.7 inches high, with no separate footer. Statistics
use the least occupied scatter corner. The South Atlantic has no finite
pairs and is explicitly shown as an empty comparison.

## Deliverables

Canonical root:

```text
~/Data/plots/modis_l2_200807_gls_rho05/aeronet_regional_summary/
```

There are 14 PDFs, 14 PNG previews, 14 individual protocol JSON files and
one passing suite protocol report: 43 files totaling 6,092,279 bytes.
The PDFs total 2,738,787 bytes. The [inventory](modis_c61_200807_regional_summary.tsv)
records every PDF's size, SHA-256 and inspection/protocol status.

The same 14 PDFs were copied into:

```text
~/Desktop/MODIS_C6.1_Regional_2008-07/
```

Every Desktop copy matches its canonical file byte for byte. All region
previews were visually inspected during layout verification; the final
Western US render was rechecked after committing.
Every PDF contains the full plotting revision and the input matchup checksum.
The monthly C6.1/C7 Desktop PDFs were refreshed with the same plotting revision;
their records remain in [the monthly delivery report](ami_c7_200807_plots.md).
This revised regional layout was rendered on the laptop for C6.1 iteration.

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

All 952 offline tests, mypy, Black and isort pass. Review exposed clipping
in a separate South Atlantic spatial provenance block with three processing
revisions; reserved page padding fixes it, and a new regression reproduces
the failure before the fix. No findings remain open. Existing checks compare
daily values/statistics with the established renderer and verify scatter
pairs, geographic coverage, marker sizes, empty/date-limited regions,
cached map assets and finite text positions. This is a presentation update;
scientific acceptance criteria and regional shortcomings are unchanged.
