# AERONET plot publication evidence

The AERONET plot publication gate passes for **1,138 PDF/PNG pairs**.
All 1,138 individual figure protocols and
44 combined suite protocols pass. Every one of the
1,138 previews was visually inspected. These
results establish plot publication acceptance; the separate scientific gates remain
as reported in [the evaluation campaign](evaluation_campaign.md).

The [portable manifest](aeronet_plot_manifest.json) links the inventories below and
records their current SHA-256 hashes. Each inventory includes actual PDF, PNG,
individual protocol, combined protocol and matchup-input hashes, along with run
configuration and processing fingerprints. Artifact paths use `{data_root}` so the
evidence can be resolved against the configured data root without embedding a developer
home directory. The binary deliverables remain under the run's `paths.plot` template;
their hashes refer to the inspected files, not to an unexecuted recipe.

| Run / inventory | Suite reports | PDF/PNG pairs | Inspected previews |
|---|---:|---:|---:|
| [modis_l2_200807_gls_rho05](aeronet_plot_inventories/modis_l2_200807_gls_rho05.json) | 5 | 138 | 138 |
| [modis_l2_200807_gls_rho0](aeronet_plot_inventories/modis_l2_200807_gls_rho0.json) | 5 | 138 | 138 |
| [modis_l2_200807_count](aeronet_plot_inventories/modis_l2_200807_count.json) | 5 | 136 | 136 |
| [modis_l2_200807_cam7_v2](aeronet_plot_inventories/modis_l2_200807_cam7_v2.json) | 5 | 136 | 136 |
| [modis_l2_202009_gls_rho05](aeronet_plot_inventories/modis_l2_202009_gls_rho05.json) | 5 | 143 | 143 |
| [viirs_l2_202009_gls_rho05](aeronet_plot_inventories/viirs_l2_202009_gls_rho05.json) | 5 | 143 | 143 |
| [mixed_l2_202009_gls_rho05](aeronet_plot_inventories/mixed_l2_202009_gls_rho05.json) | 5 | 149 | 149 |
| [mixed_db21_l2_202009_gls_rho05](aeronet_plot_inventories/mixed_db21_l2_202009_gls_rho05.json) | 5 | 147 | 147 |
| [modis_l2_200807_ocean_qa3 (Southern Ocean)](aeronet_plot_inventories/modis_l2_200807_ocean_qa3_southern_ocean.json) | 1 | 2 | 2 |
| [modis_l2_200807_gls_rho05_nonnegative (Southern Ocean)](aeronet_plot_inventories/modis_l2_200807_gls_rho05_nonnegative_southern_ocean.json) | 1 | 2 | 2 |
| [mixed_l2_202009_ocean_qa3 (Southern Ocean)](aeronet_plot_inventories/mixed_l2_202009_ocean_qa3_southern_ocean.json) | 1 | 2 | 2 |
| [mixed_l2_202009_gls_rho05_nonnegative (Southern Ocean)](aeronet_plot_inventories/mixed_l2_202009_gls_rho05_nonnegative_southern_ocean.json) | 1 | 2 | 2 |

## Scope and reproduction

Each of the eight baseline configurations ran all five AERONET suites, with both
grid and L2 default pairings. The top 20 sites are selected by grid-matchup count,
breaking ties by exact site name. All active regions are included: July has 13
named regions plus global context; September adds the date-limited Labor Day smoke
region. The smoke region's actual samples are restricted to September 8–14 even
though the figure subtitle identifies the full campaign month.

The plotting configuration copies change only these settings:

```yaml
plots:
  suites: [aeronet_site_bias, aeronet_site_timeseries,
           aeronet_regional_timeseries, aeronet_scatter, evaluation_summary]
  regions: all
  sites: {select: top_n, n: 20}
```

They were rendered through `render_aeronet_suites`; the same configured suites are
available through `sarb-aod plot -c <config>`. July rho 0 and rho 0.5, and September's
mixed baseline, include comparisons at rho 0, 0.25, 0.5, 0.75 and 1. Comparison run
identities and matchup hashes are recorded in their inventories. L2 reuse preserves
the underlying matchup science when only the grid weighting changes. The pure
September MODIS and VIIRS L2 frames use verified exact stream subsets of the mixed
baseline. The Deep Blue v2.1 grid report was refreshed after its L2 evaluation to
include coastal-site evidence before plotting.

Additional QA3 and nonnegative-AOD configurations render the regional time-series
suite with `regions: [southern_ocean]` and `levels=("grid",)`, producing a global
context figure and Southern Ocean figure for each run. The baseline Southern Ocean
series are already included in the complete suites. Southern Ocean means the
configured latitude band south of 40 degrees S, including land and islands, rather
than an ocean-only surface mask. These series use independent cached AERONET and
grid context; absent nonnegative L2 archives are not required.

## Inspection and corrections

Every preview was inspected in contact sheets or at full size. Dense regional
scatter matrices, scorecards, boxplots and correlation sweeps were also examined at
full size. Independent review covered all July rho-0 and September VIIRS previews.
The inventories record per-figure inspection and, where retained, contact-sheet
hashes. The four Southern Ocean sensitivity runs were inspected entirely at full
size. Labels, source axes, date ranges, legends, colorbars, panel completeness,
sampling gaps and empty-state messages were checked. No unresolved rendering defect
was found in the accepted artifacts.

Real data exposed overlapping region names in wide boxplots and crowded September
month-boundary ticks in regional series. The boxplot layout now accommodates the
complete region list; the regional date locator uses evenly spaced dates across
the month boundary. Focused regressions pass, and affected figures were regenerated
and reinspected. Numeric and Boolean fields in legacy matchup archives are normalized
on read, preserving the original archive bytes and hashes. Plot saving rejects
duplicate figure names and failing suite protocols.

Scatter titles intentionally remain `AOD`: the two compared sources are identified
on their axes, following DAVINCI's scatter conventions. Source-first titles are used
for single-source global maps. Region and site figures retain `AOD - <Name>` titles.

## Scientific interpretation remains separate

The smoke-period scatter and time series retain the measured extreme AOD and the
satellite underestimation visible against AERONET. Summary figures preserve failed
bias, RMSE, correlation or residual-spread checks rather than changing thresholds
to obtain acceptance. Rho sweeps show how the reported uncertainty changes; movement
of the residual standard deviation toward one alone does not establish calibration.

This publication gate does not select production defaults or close the outstanding
July/September scientific gates. The CAM7-v2 diagnostic run is not an SP1 archive
comparison; unavailable historical SP1 inputs remain an explicit limitation in the
evaluation report. Numerical conclusions and provisional recommendations belong in
[the evaluation campaign](evaluation_campaign.md), alongside the separate
[map publication evidence](map_plot_verification.md).
