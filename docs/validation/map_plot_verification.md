# Map publication evidence

The map publication gate passes for **414 PDF/PNG pairs** across July 2008,
September 2020, and Southern Ocean sensitivity runs. All 414 individual protocol
reports and 31 combined suite reports pass. **All 414 previews were visually
inspected**, including every daily map and every smoke and sensitivity figure. This is
map publication acceptance; it does not close the separate scientific gates.

| Inventory | PDF/PNG pairs | Suite reports | Inspected previews |
|---|---:|---:|---:|
| [July baselines](july2008_plots.json) | 152 | 7 | 152 |
| [September baselines and smoke week](sept2020_plots.json) | 204 | 14 | 204 |
| [Southern Ocean sensitivities](southern_ocean_plots.json) | 58 | 10 | 58 |

Each inventory records configured artifact paths, SHA-256 hashes for PDFs,
PNGs and protocol reports, the actual PDF configuration/git provenance,
inspection flags, and passing report counts. Deliverables are under the run
configuration's `paths.plot` template. Southern Ocean files use dedicated
`aod_maps_southern_ocean` and `aod_differences_southern_ocean` suite directories,
so their reports do not replace the global reports for the same dates.

All period fields and comparison maps were inspected, together with every daily
map for July 1–31 and September 1–30. Smoke-box inspection covers every day from
September 8–14 in each of the four configurations. After the initial 182-preview
review, the remaining 112 July and 120 September daily maps were inspected on
40 contact sheets preserving each preview's original pixel dimensions. Full-size
followups checked the MODIS September 10 global map and mixed DB v2.1 September 12
smoke map; no additional defects were found. All 1,068 July/September PDF, PNG and
individual protocol-report hashes, plus 21 suite-report hashes, were verified
unchanged. Absolute AOD maps share
the full GEOSIT scale, capped at 1 with an upper extension. Missing values stay
blank. No artificial longitude seam, clipped text, overlapping labels or missing
colorbars was visible. Two issues discovered during inspection were fixed and
regression tested: title/subtitle spacing for the shallow Southern Ocean band,
and source prefixes on single-sensor global maps. All affected outputs were
refreshed and inspected again before final acceptance.

The September smoke maps show the west-coast high-AOD event and increasing
saturation from September 8 through September 10/14. The cap is a display
choice; AOD above 1 remains in the numeric means. VIIRS-minus-MODIS and NOAA-20
Deep Blue v2.1-minus-v2 maps label their subtraction explicitly. They compare
independently count-weighted period cell means, not matched retrieval samples.

The Southern Ocean region is the configured latitude band south of 40° S,
including land and islands. The following are cosine-latitude means of period
cell statistics. Observation fraction includes unsampled cells as zero; AOD
includes only finite cells, so changing coverage changes its spatial support.

| Period / setting | AOD | Accepted product count per cell | Observed-window fraction |
|---|---:|---:|---:|
| July MODIS, ocean QA ≥ 1 | 0.05547 | 388.60 | 0.06431 |
| July MODIS, ocean QA = 3 | 0.08280 | 65.93 | 0.02033 |
| July MODIS, AOD ≥ 0 | 0.05548 | 388.49 | 0.06430 |
| September mixed, ocean QA ≥ 1 | 0.10765 | 3921.22 | 0.11917 |
| September mixed, ocean QA = 3 | 0.10573 | 2418.32 | 0.08787 |
| September mixed, AOD ≥ 0 | 0.10767 | 3916.07 | 0.11917 |

On cells finite in both settings, the area-mean QA3-minus-baseline differences
are +0.02464 in July and +0.000370 in September. The latter differs from the
difference of the two independently supported regional means in the table.
Nonnegative-minus-baseline differences are +0.0000128 and +0.0000192, respectively.
The maps preserve these measured changes without adding smoothing or QC cuts.

July's written three-hour p99.9 and approximate coverage gates remain open;
[the science verification](july2008_verification.md) distinguishes them from
monthly-cell percentiles. September's monthly-cell p99.9 is 1.033–1.071 across
the four baselines, but monthly maxima reach 4.968 for MODIS and 3.593 for the
mixed products. Plot acceptance does not establish a universal AOD upper bound.
The [September rejection gate](sept2020_verification.md) remains incompatible
with the products' native maximum 5 and the configured inclusive maximum 5.
