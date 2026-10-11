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
than an ocean-only surface mask. These regional series now use the verified
independent daily grid-matchup archives directly; absent nonnegative L2 archives are not required.
Independent AERONET and full grid samples remain inputs to the site time-series suite.

Task 4.8 updates the 124 regional time-series figures at the user's request.
The full-region satellite curve is replaced by overlapping AERONET and satellite
bands spanning the 25th–75th percentiles of the matched sites' daily means.
Both sources use the same finite site-days, after independently averaging each
day's AERONET observations and count-weighting the available satellite cell fields.
Each active site receives equal weight in the regional mean. AERONET observations
are not restricted to satellite-containing three-hour windows. Missing days
interrupt lines and bands; single-site days show a point without a spread band.
Isolated multi-site days show their interval in a short band extending six hours
on either side of the daily point. This is spatial variation across sites, not a
confidence interval or retrieval uncertainty.

Each figure reports matched days, mean bias, RMSE, correlation and normalized
mean absolute error (100 × mean absolute difference / mean AERONET). These
statistics use the displayed daily regional averages and the configured minimum
sample count. User direction on 2026-10-11 makes these the acceptance basis,
replacing the historical three-hour gates. The July baseline global series gives
N=31, bias=+0.01512, RMSE=0.02069, R=0.88018 and normalized absolute error=7.9%;
its unrounded plot statistics equal the acceptance report and pass all three gates.
The percentage formula and meaning of the shaded bands appear in the footer.
Optional sensor/stream requests produce separate paired comparison figures with
each component's count-weighted daily satellite mean and paired AERONET daily sample.

The title sits directly above the axes at left, with the subtitle at right:
latitude and longitude ranges separated by a space, before the dates and active-site
counts, all on one line.
The title and AOD axis read `AOD 550 nm`; date ticks rotate 20° to save
vertical space. `Time` is inside the axes above the bottom border, while dates
remain outside. Both footer lines align with the plot's left edge, with the band
and percentage definitions combined on one line. Labels and annotations use Title Case,
including `Active Sites`, `Daily Means` and `Absolute Error`. An automatically positioned inset
shows the region outline, coastlines and contributing AERONET sites. It is drawn
below the translucent site-spread bands and time-series curves, preserving those
curves through overlaps. It stays fully inside the plot box, selecting among four corners,
top center and bottom center for the most free space among the curves, bands and legend.
Bottom-center placement clears the Time label. Coastlines are clipped to the
exact region box, with no surrounding geographic padding. Site markers include only finite
pairs within the displayed period. The 124 daily regional figures have explicit daily input provenance; the
other 1,014 AERONET figures retain their historical diagnostic sampling and bytes.
Current science results are in [daily_acceptance_campaign.md](daily_acceptance_campaign.md).

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
