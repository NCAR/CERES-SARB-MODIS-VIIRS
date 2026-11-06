# CAM7 AOD File and Variable Naming Specifications

Recorded 2026-11-06 from NASA Outlook correspondence. This document separates
the new naming guidance from examples and choices that still need agreement.
It records specifications; it does not rename existing files or NetCDF variables.

## Email Sources

- **“Variable names for preprocessed MODIS-VIIRS files to be used in CAM7”**,
  2026-11-06: Thomas Edward Caldwell (Tom) and Antonio Viudez-Mora (Toni),
  with David Fillmore copied. Tom proposed product-first variable names and
  CERES-style filenames. Toni agreed with the variable naming and raised the
  seconds-timestamp requirement. Tom subsequently accepted keeping the existing
  timestamp if necessary or preferable; Toni agreed.
- **“Update CAM7 and AODs”**, updated 2026-11-02–03: Toni and Seiji Kato,
  with David copied. This thread identifies archive collections and requested
  retrieval variables, and corrects an earlier statement about a supplied
  DT/DB combined product for VIIRS.

The implementation explanations below were checked against the producer and
the adjacent CAM7 reader on 2026-11-06. The producer's committed base was
`2a51d6adbaa1e4f126667b2324b3cfe9b89cb360`.

## New Naming Guidance

| Topic | Guidance from the November 6 exchange | Status |
|---|---|---|
| Variable prefix | Start data-variable names with the product name, followed by what the variable represents. | Tom's guidance; Toni agreed. |
| Variable length | Keep names intuitive and concise; put fuller descriptions in `long_name` and other attributes. | Tom's guidance; Toni agreed with the variable naming. |
| Product identity | Select the product name before finalizing the variable names. Tom used `AOD` as an example. | Product name remains undecided. |
| Explicit variable example | `Aod_mean_terra_dt` | Tom's example, including its capitalization; a complete rename map was not supplied. |
| Filename structure | CERES prefix, product, source, edition, six-digit token, timestamp, `.nc`. | Proposed through the examples below; token meanings and final spelling remain open. |
| Filename timestamp | Retain the current seconds-based convention for CAM7 compatibility. | Tom's initial hours suggestion was relaxed after Toni's objection; Toni agreed. |
| Global attributes | Review unnecessary information and backslash characters in the delivered attributes. | Tom suggested cleanup; the attribute list and serialization policy remain unspecified. |

### Filename Examples and Outstanding Choices

Tom described an `AOD-3hr-f09` product and gave these exact examples:

```text
CER_AOD-3hr-f90_MODIS-VIIRS_Edition5-Test_999999.2022010100.nc
CER_AOD-3hr-f90_MODIS-VIIRS_Edition5-Test_999999.2022010103.nc
```

The examples contain `f90`, while his product description and the implemented
192 × 288 CAM grid use `f09`. Resolve this inconsistency before adopting the
filename. The `999999` token's meaning, the final edition string, and whether
`MODIS-VIIRS` is the fixed product-family label or changes with the actual sensor
content were not settled.

The hour suffixes in those initial examples are **not a final requirement**.
Toni explained that CAM7 normally uses seconds in filenames. Tom then said the
timestamp could remain as it is; Toni's final reply agreed. Preserve the existing
`YYYYMMDD.SSSSS` date/seconds suffix until a complete replacement is agreed.

For illustration only, applying the proposed prefix with `f09` and the existing
date/seconds suffix would give:

```text
CER_AOD-3hr-f09_MODIS-VIIRS_Edition5-Test_999999.20220101.00000.nc
CER_AOD-3hr-f09_MODIS-VIIRS_Edition5-Test_999999.20220101.10800.nc
```

These two seconds-based examples are a compatibility illustration derived here,
not filenames approved in the email. Any eventual prefix change must be made in
configuration and matched by the consuming CAM7 filename template.

## Current CAM7 Contract and Variable Meanings

The current default stem is `MODIS_VIIRS_AOD_3hr_f09`. Existing files use:

```text
MODIS_VIIRS_AOD_3hr_f09.YYYYMMDD.SSSSS.nc
```

`SSSSS` is the zero-padded **UTC window-start second of day**: `00000`,
`10800`, `21600`, `32400`, `43200`, `54000`, `64800`, or `75600`.
Each window spans three hours. The scalar NetCDF `time` is instead the window
**center**, in `hours since 2000-01-01 00:00:00 UTC`, with a Gregorian calendar.
Keeping seconds in filenames does not change the CF time units.

| Current exact name | Meaning |
|---|---|
| `aod_weighted_mean(lat, lon)` | Primary 550 nm DT/DB merge that CAM7 reads. The configured weighting is used; the working default is GLS with provisional rho 0.5 and Birge inflation. The final valid mean is clipped at zero for CAM. |
| `aod_weighted_mean_modis(lat, lon)` | MODIS-only aggregate from the selected streams. |
| `aod_weighted_mean_viirs(lat, lon)` | VIIRS-only aggregate from the selected streams. |
| `aod_mean_<stream>(lat, lon)` | Per-stream mean, for example `aod_mean_terra_dt`. Tom's example would capitalize the prefix as `Aod`; that exact case change is not implemented. |
| `aod_count(lat, lon)` | Number of valid contributing L2 pixels in the cell/window. |
| `aod_count_<stream>(lat, lon)` | Contributing pixel count for one stream. |
| `aod_uncertainty(lat, lon)` | Reported one-sigma uncertainty of the primary merge. Global uncertainty calibration remains open. |
| `lat(192)`, `lon(288)` | Pole-centered f09 grid-cell coordinates. |
| `lat_bnds(lat, nbnds)`, `lon_bnds(lon, nbnds)` | Cell edges, with two bounds per coordinate. These describe cell extent rather than a second set of centers. |
| `time` | Scalar window-center coordinate used by CAM7 for temporal interpolation. |
| `time_bnds(nbnds)` | Start and end of the three-hour averaging interval, in the same units as `time`. |

The current reader explicitly requests the case-sensitive name
`aod_weighted_mean`, the `lat` and `lon` coordinates, and scalar `time`.
Renaming a CAM-read variable therefore requires a coordinated consumer change
or a compatibility variable. No final case-sensitive mapping for the primary
field, companion fields, or bounds was supplied by Tom and Toni.

### Supplied MODIS Combined AOD Is a Separate Diagnostic

When `combined_product.enabled` is true for MODIS C7, these additional exact
names are written:

```text
AOD_550_Dark_Target_Deep_Blue_Combined_Land_Ocean
AOD_550_Dark_Target_Deep_Blue_Combined_Algorithm_Flag_Land_Ocean
```

The first is an arithmetic mean of the retrieval team's supplied combined
product, pooling Terra and Aqua pixels into the same f09 cells and UTC windows.
It is calculated independently of `aod_weighted_mean`; it is not another input
to that primary merge. The diagnostic preserves valid native negatives rather
than applying the primary field's final CAM clipping. The algorithm flag and
associated `aod_count_modis_combined*` fields describe its contributing retrievals.
See [MODIS C7](docs/MODIS_C7.md) for the flag values and aggregation rules.

This resolves the distinction Tom asked about: Toni's tentative interpretation
that the long-named field was used to form the primary regridded AOD is not how
the current implementation works.

MODIS-only, VIIRS-only, and mixed-sensor runs are all supported through
`grid.streams`. Writing both sensor companion variables does not imply that
both sensors contributed. A sensor without contributing retrievals has missing
aggregate AOD. The sensor transition requested in the October 15 history is
Terra+Aqua through March 2022, then NOAA-20 from April 2022, without SNPP in
that production record; the separate NOAA-20 overlap comparison starts May 2018.

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

## Follow-Up Before Implementing New Names

1. Confirm the product name, `f09` spelling, six-digit token, edition text,
   and sensor-label policy for the filename.
2. Agree the complete case-sensitive variable mapping, including the CAM-read
   primary field and treatment of the supplied MODIS combined diagnostic.
3. Specify the global-attribute cleanup. Preserve required processing history
   and reproducibility metadata described in [PROVENANCE.md](docs/PROVENANCE.md);
   blindly deleting backslashes can corrupt serialized configuration JSON.
4. Coordinate producer configuration and CAM7 reader/template changes, then
   validate sample files before producing a newly named archive.

Current behavior is defined by [`contract.py`](sarb_aod/contract.py),
[`pipeline.py`](sarb_aod/pipeline.py), [`windows.py`](sarb_aod/windows.py), and
the consumer `../CERES-SARB-CAM7/src/cam/aod_nudging_obs.F90`.
