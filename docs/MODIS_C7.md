# MODIS C7 on AMI

## Requirements and evidence

Seiji Kato's October 7–8, 2026 email requests Terra and Aqua C7 for Edition 5:
use separate Dark Target and Deep Blue retrievals in the processing algorithm,
and also grid/save the supplied combined AOD and its algorithm flag. It does
not prescribe a new filename convention or a new weighting method. VIIRS
production waits for DMT's new Dark Target delivery; its version is unspecified.

On 2026-10-11, bounded listings of July 2008 found 4,919 Terra and 4,910 Aqua
C7 aerosol files, with both products present on every day. These local holdings
are independent of public CMR availability. Input directories mix aerosol,
geolocation, and radiance products: filter by product, acquisition day, and
collection, not just `.nc` suffix. Archive sources remain read-only.

The reader maps were checked against these AMI files (untracked development
copies in `.cache/c7_samples`, not distributed in the repository):

- `Terra/C7/2008/183/MOD04_L2.A2008183.2335.007.2025356181028.nc`
- `Aqua/C7/2008/183/MYD04_L2.A2008183.0555.007.2025356073150.nc`

Both report `VersionID=7`, `PGEVersion=7.0.3`, and a December 22, 2025
production date. The archive root is `/ASDC_archive/MODIS` (an alias of
`/CERES_prd/MODIS`). This does not establish completeness against an external
inventory or availability of other months.

## Reader and uncertainty

Geolocation is in `geolocation_data/Latitude` and `Longitude`. TAI93 scan times
are in `geophysical_data_Deep_Blue/Scan_Start_Time`; apply leap seconds as for
C6.1. The default DT fields are land corrected AOD and ocean average AOD, with
550 nm selected by the trailing wavelength dimension's name. Retrieval-team
QA reproduces `geophysical_data_Dark_Target/Optical_Depth_Land_And_Ocean` on
the two samples. C7's DB includes land and SOAR ocean retrievals. The reader
uses the unfiltered per-surface `Aerosol_Optical_Thickness_550_Land` and `_Ocean`
fields, then applies configurable QA (default >= 2). This permits QA-1
comparisons without prefiltering them away. Sample comparisons check default equivalence to
`Aerosol_Optical_Thickness_550_Land_Ocean_Best_Estimate`. The email's `Aerorol`
spelling is a typo.

Neither inspected DT group contains per-pixel AOD uncertainty. Fitting
residuals, reflectance standard deviations, and error codes are not AOD sigma.
DT therefore retains the configured MODIS expected-error approximation,
provisionally; C7 uncertainty calibration is not established. DB uses its
`Aerosol_Optical_Thickness_550_Expected_Uncertainty_Land` and `_Ocean` fields,
labeled one-sigma in the source. `STDV` fields describe retrieval-cell spread
and are not substituted for the uncertainty estimates.

## Two independent gridded estimates

`aod_weighted_mean` remains the configured DT/DB merge for CAM (default GLS,
rho 0.5, Birge inflation). Its existing companion fields remain available.

Enable `combined_product.enabled` to add these exact root-level names:

```text
AOD_550_Dark_Target_Deep_Blue_Combined_Land_Ocean
AOD_550_Dark_Target_Deep_Blue_Combined_Algorithm_Flag_Land_Ocean
```

Their source group is `geophysical_data_Dark_Target_Deep_Blue_Combined`.
Each native combined retrieval contributes once, regardless of the number of
DT/DB streams selected. The first field is an arithmetic mean over the same
f09 cells and half-open three-hour UTC windows, pooling Terra and Aqua. It is
never included as another independent observation in the primary merge.

The diagnostic preserves the source AOD range [-0.1, 5], including negative
values, without CAM clipping. Default `combined_product.qa_min: 0` adds no QA
threshold to the supplied finite product: source QA must still be an integer
0–3 and algorithm flag must be 0–2. The source describes QA 0 as no retrieval;
finite AOD with QA 0 is retained for this producer-product diagnostic, not
interpreted as high quality. Set `qa_min` explicitly to study a stricter policy.
These choices are implementation defaults, not requirements settled by email.

The gridded integer algorithm flag is:

| Value | Meaning |
|---|---|
| 0 | All contributing native pixels have DT flag 0 |
| 1 | All have DB flag 1 |
| 2 | All have native mixed flag 2 |
| 3 | More than one native category contributes to this grid cell/window |
| -999 | No contributing retrievals |

Value 3 is an explicitly documented **gridded extension**, not a new native
retrieval category. The flags are never averaged. `aod_count_modis_combined`
and `aod_count_modis_combined_dt`, `_db`, `_mixed` retain the total and full
category distribution. This preserves provenance without an arbitrary modal
tie rule. Empty counts are valid zeros, not masked fills. No uncertainty for
this additional arithmetic mean is claimed.

The CAM contract validator also checks the additional fields' layouts,
count sums, category flags, and empty-cell means. Processing hashes include
combined-product settings and reader maps, so changing them invalidates resume.

## Scope of acceptance

Synthetic tests cover category mixing, missing flags, time boundaries, native
negative AOD, no double counting, archive filtering, missing platform inputs,
read-only staging, and corrupted-output recovery. Optional real-data tests
compare both cached samples with the producer's DT and DB best estimates.
A completed July grid run establishes operational processing, not C7 science
acceptance or calibrated uncertainty. AERONET comparison and September C7 remain
separate work.
