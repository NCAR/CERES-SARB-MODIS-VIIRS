# AOD Output Naming and Metadata Specifications

Revised 2026-11-06 from the Toni/Tom email exchange and David Fillmore's subsequent
instructions. The new publication format uses the rules below. Historical files
retain their recorded names and processing history. CAM7 source changes are deferred.

## Variable Names

Use uppercase `AOD`, uppercase sensor/algorithm acronyms, title case for `Terra`
and `Aqua`, and lowercase statistics and processing words. Use `NOAA20` and
`SNPP` as platform tokens. Names are case-sensitive.

| Quantity | Publication name |
|---|---|
| Final merged 550 nm AOD | `AOD_mean` |
| Merged one-sigma uncertainty | `AOD_uncertainty` |
| Merged contributing pixel count | `AOD_count` |
| Terra Dark Target mean/count | `AOD_mean_Terra_DT`, `AOD_count_Terra_DT` |
| Aqua Deep Blue mean/count | `AOD_mean_Aqua_DB`, `AOD_count_Aqua_DB` |
| NOAA-20 Dark Target mean/count | `AOD_mean_NOAA20_DT`, `AOD_count_NOAA20_DT` |
| NOAA-20 Deep Blue mean/count | `AOD_mean_NOAA20_DB`, `AOD_count_NOAA20_DB` |
| MODIS / VIIRS sensor aggregate, when configured | `AOD_mean_MODIS` / `AOD_mean_VIIRS` |
| Supplied MODIS-team combined diagnostic | `AOD_mean_MODIS_COMBINED` |
| Supplied combined algorithm-category diagnostic | `AOD_algorithm_flag_MODIS_COMBINED` |
| Supplied combined pixel counts | `AOD_count_MODIS_COMBINED`, with `_DT`, `_DB`, `_MIXED` category companions |
| Other diagnostics | `AOD_chi2_per_dof`, `AOD_n_effective`, `AOD_count_negative`, etc. |
| Experimental comparison fields, when enabled | `_unfiltered` / `_filtered` suffixes on the corresponding publication name |

The same rule covers every selected platform/algorithm pair. CF coordinates and
bounds retain `lat`, `lon`, `time`, `lat_bnds`, `lon_bnds`, and `time_bnds`.
`long_name`, units and other attributes provide the fuller descriptions.

`AOD_mean` retains the configured DT/DB weighting and final clipping policy.
`AOD_mean_MODIS_COMBINED` remains the independently gridded arithmetic mean of
the retrieval team's supplied C7 combined product. It preserves native negatives
and is not an extra input to the primary merge. See [MODIS C7](docs/MODIS_C7.md).
Naming changes do not alter retrieval QC or numerical aggregation.

## Configured Sources Determine the Output Schema

Only configured `grid.streams` produce stream means, counts and optional filter
fields. Only configured instruments produce sensor aggregates. In particular:

- Terra+Aqua MODIS: selected Terra/Aqua DT/DB fields and the MODIS aggregate;
  no VIIRS, SNPP or NOAA-20 placeholders.
- NOAA-20 VIIRS: selected NOAA-20 DT/DB fields and the VIIRS aggregate;
  no MODIS, Terra, Aqua or SNPP placeholders.
- A DT-only run emits no DB stream fields.

A configured stream that has no valid retrievals in a particular window retains
its fields with missing AOD and zero counts. With `grid.missing_products: fill`,
the same applies to a configured source whose granules are absent for a day.
All eight time windows are still written under that gap policy. This keeps the
configured schema and filenames stable through clouds and archive gaps.
Mixed-sensor capability remains available for explicitly configured comparisons.

## Filenames

The proposed automatic stems reflect the configured instruments and platforms:

```text
CER_AOD-3hr-f09_MODIS-Terra-Aqua.20080701.00000.nc
CER_AOD-3hr-f09_MODIS-Terra-Aqua.20080701.10800.nc
CER_AOD-3hr-f09_VIIRS-NOAA20.20260901.00000.nc
CER_AOD-3hr-f09_VIIRS-NOAA20.20260901.10800.nc
```

The generic layout is `<stem>.YYYYMMDD.SSSSS.nc`. `SSSSS` is the five-digit UTC
window-start second of day: `00000`, `10800`, ..., `75600`. The scalar NetCDF
`time` remains the window center in `hours since 2000-01-01 00:00:00 UTC`;
`time_bnds` records the three-hour interval. The grid is **f09**, 192 x 288.
An automatic filename includes both sensor names only if both are configured.

Select the new output explicitly with:

```yaml
output:
  naming: ceres
  stem: auto
  clip_negative_mean: true
```

This is also the default for new configurations. Existing campaign YAML that
explicitly specifies `stem: MODIS_VIIRS_AOD_3hr_f09` and omits `naming` keeps
`naming: legacy`, preserving its numerical processing hash and archive lookup.
To switch such a run to publication names, select `naming: ceres` and `stem: auto`.
Explicit custom stems remain supported. No existing archive is renamed or rerun
by changing this specification.

Examples:

- [Terra+Aqua MODIS C7, July 2008](config/runs/modis_c7_l2_200807_f09_3hr_gls_rho05_ceres.yaml).
- [NOAA-20 DB 002 / DT 021, September 2026](config/runs/viirs_noaa20_db002_dt021_l2_202609_f09_3hr_gls_rho05_ceres.yaml).

The `CER_` form follows Tom's proposal. His original examples included
`Edition5-Test_999999` and the inconsistent spelling `f90`; no distribution-ID
meaning or final edition token was supplied. Those tokens are omitted from the
automatic stem until their meanings are specified; a final release stem can be
set in configuration.

## Metadata

New grid files record `input_file_paths` as newline-separated absolute L2 granule
pathnames, `input_file_count`, and `input_file_paths_scope`. The list identifies
all distinct L2 granules read for the UTC processing day, including configured
previous-day edge inputs. It does not assert that every listed file contributes
a valid pixel to every individual three-hour window. A day without inputs has
an empty pathname string and count zero.

Remove the full `python_environment` inventory from newly written metadata:
no Conda package lists, build strings, package hashes, environment prefixes or
interpreter paths. Retain a compact `software_versions` record with sarb_aod,
Python, NumPy, netCDF4-Python, NetCDF-C and HDF5 versions. Keep repository/revision,
creation date, processing settings, source collections, configuration snapshots,
and declared development provenance. See [PROVENANCE.md](docs/PROVENANCE.md).

## CAM7 Compatibility

CAM7 modification is deferred. The current reader expects the primary variable
`aod_weighted_mean`. A separate compatibility copy can rename the published mean:

```bash
ncrename -O -v AOD_mean,aod_weighted_mean publication.nc cam7.nc
```

This follows the [NCO variable-renaming interface](https://nco.sourceforge.net/nco.html#ncrename-netCDF-Renamer).
CAM7's configured input path/template must point to the chosen filename. The
other publication fields can retain their names because CAM7 ignores them.
Internal validation, plotting, summaries and AERONET evaluation accept both
naming conventions without rewriting the input file.

## Correspondence Basis

The November 6 thread **“Variable names for preprocessed MODIS-VIIRS files to
be used in CAM7”** contains Tom Caldwell's product-first naming guidance and
Toni Viudez-Mora's response. Tom's example was `Aod_mean_terra_dt`; David's
subsequent direction selects `AOD_mean_Terra_DT`. Tom initially suggested hour
filenames, then accepted retaining seconds after Toni raised compatibility.
David subsequently requested configured-source-only fields, input-aware filenames,
removal of Conda metadata and addition of full input pathnames.

## Archive and Retrieval Specifications in Toni's Thread

Seiji's November 3 clarification states that VIIRS has no MODIS-team supplied
DT/DB combined AOD product; use this package's DT/DB merge for VIIRS.
His November 2 message requests processing NOAA-20 from May 2018 with the new
Dark Target delivery. The thread specifies:

| Input | Requested archive or filename information |
|---|---|
| MODIS C7 Terra | Toni's sample-directory question names `/ASDC_archive/MODIS/Terra/C7/yyyy/jd`. |
| MODIS C7 Aqua | Toni's sample-directory question names `/ASDC_archive/MODIS/Aqua/C7/yyyy/jd`. |
| NOAA-20 Deep Blue | `/ASDC_archive/VIIRS/J1/002/`; look for `AERDB_L2_VIIRS_NOAA20.*.nc`. |
| NOAA-20 Dark Target | `/ASDC_archive/VIIRS/J1/021/`; Seiji also reports the delivery under `/CERES_prd/VIIRS/J1/021`. |

These are correspondence specifications, not a fresh completeness audit of the
remote archive. The earlier October note mentioned `021/2026`; the November
update reports Dark Target back to May 2018. Existing historical campaign
configs that deliberately use DT `002` retain their recorded provenance and
must not be silently treated as DT `021` products.

Seiji listed the following source variable names on November 3:

| Retrieval | Exact names in the email |
|---|---|
| Deep Blue AOD | `Aerosol_Optical_Thickness_550_Land_Ocean_Best_Estimate` |
| Deep Blue land uncertainty | `Aerosol_Optical_Thickness_550_Expected_Uncertainty_Land` |
| Deep Blue ocean uncertainty | `Aerosol_Optical_Thickness_550_Expected_Uncertainty_Ocean` |
| Dark Target AOD | `Optical_Depth_Land_And_Ocean` |
| Dark Target QA | `Land_Ocean_Quality_Flag` |

This list does not override the per-product maps in
[`catalog.yaml`](sarb_aod/data/catalog.yaml). In particular, MODIS C7 DB readers
use the separate land/ocean AOD fields and apply QA to recover the best-estimate
selection; VIIRS DB likewise uses separate land/ocean AOD and
`Aerosol_Optical_Thickness_550_Expected_Uncertainty_Land` / `_Ocean`, as defined
in the catalog. Group paths and variable availability must be checked
for each product and collection rather than inferred from the email's list.
