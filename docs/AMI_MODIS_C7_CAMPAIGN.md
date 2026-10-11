# AMI MODIS C7: April 2000–March 2022

The user authorized 22 April–March analysis years on 2026-10-11, using
Terra and Aqua Dark Target and Deep Blue, Collection 7 per Seiji's request.
Despeckling is explicitly disabled in every yearly configuration. This task
grids observations only; it does not run plots or AERONET evaluation.

## Configuration and output

[The campaign manifest](../config/campaigns/modis_c7_200004_202203.txt) lists the
22 tracked annual configs in task order. Task 1 is April 2000–March 2001, and
task 22 is April 2021–March 2022. Each uses `ami-modis-c7` and the July AMI test's
retrieval-team QC, GLS rho 0.5, Birge inflation, and identity AOD corrections.
All four MODIS streams are selected; no VIIRS streams are selected.

Seiji's native combined AOD and algorithm-category diagnostics remain enabled,
independently of the primary DT/DB merge. All configs set:

```yaml
grid:
  missing_products: fill
despeckle:
  enabled: false
```

The annual YAML filenames identify each analysis interval. Their shared run name
keeps all years in one continuous output tree, while their nonoverlapping periods
produce distinct monthly summaries and metrics:

```text
/CERES/sarb/dfillmor/MODIS_VIIRS/grids/
  modis_c7_l2_200004_202203_f09_3hr_gls_rho05/YYYY/MM/DD/
    MODIS_VIIRS_AOD_3hr_f09.YYYYMMDD.SSSSS.nc
```

There are **8,035 days and 64,280 expected files**, including gap days.
No existing July test or September VIIRS output is overwritten. The resolved
configuration, original annual YAML and profile, code revision, runtime
environment and declared development-agent information are retained in each
NetCDF's provenance.

## Data gaps

The default `grid.missing_products: error` preserves existing strict behavior
and processing fingerprints. This campaign opts into `fill`: a product with no
matching granules contributes zero observations. Available products still
contribute normally. If neither satellite has data, all eight three-hour files
are written with missing AOD/uncertainty flags and zero counts, including the
native combined-product diagnostics. There is no temporal or spatial filling.

Missing products are recorded in `primary_products_missing`; the number of
primary granules is `primary_granule_count`. Monthly metrics retain missing
products for every day and `days_with_missing_products`, including resumed days.
Corrupt/unreadable files and directory permission/I/O errors still fail their day. Missing primary data cannot be
replaced by an edge granule. If a previously missing product appears later, a
resume rebuilds that day's outputs rather than preserving stale fill values.

The [dated directory inventory](validation/ami_modis_c7_200004_202203_inventory.json)
found 7,979 Terra and 7,201 Aqua day directories in the requested interval,
leaving 56 and 834 absent directories respectively. **38 dates lack both.**
Aqua's sampled archive begins on July 4, 2002; its earlier absence is expected.
The inventory checks directory presence and one product-day filename sample per
available analysis year, not independent granule completeness. Empty directories
or subsequent archive changes may add further gaps when jobs read the data.

## Runtime, queue and space

Measured July C7 AMI daily tasks took 60.291–69.413 seconds, with a median of
64.2425 seconds on one core. Multiplying by 365 gives 6.11–7.04 hours, median
6.51 hours. Monthly validation/summary adds about 15 minutes/year at the observed
75.3 seconds/month. **Budget roughly 6.5–7.5 hours per full two-satellite year**;
Terra-only years should be faster. This is an extrapolation from July, not a
measured annual run, and cold archive I/O or competing workloads can extend it.

Use **`scf.q`, one serial slot, `h_rt=12:00:00`**, with at most four concurrent
yearly array tasks. A full year does not fit the user's two-hour short-queue
limit. The live `short.q` setting still says three hours, which is also too short.
The available `make` parallel environment distributes slots across hosts, so
requesting four slots would not safely accelerate the local Python worker pool.

With four concurrent years, allow roughly 1.5–2 days for all 22 years, excluding
queue wait and archive stalls. Each task runs the existing resumable monthly
driver with `--workers 1`; a retry validates completed days and processes only
missing/stale output. Monthly summaries and metrics are the progress record.

July's 1,355,006,280 bytes over 31 days project to **327.1 GiB** for the whole
campaign, approximately 14.9 GiB per full year. Allow about **330 GiB**, plus logs
and metadata variation. The checked project filesystem had 1,027.4 GiB available;
this is shared free space, not a reservation. Timing evidence is in
[the July operational record](validation/ami_c7_200807.md).

## Submit and monitor

Develop, test, review, commit and push on the laptop; fast-forward pull on AMI
before submission. In an explicit remote Bash shell:

```bash
source /SPG_ops/utils/altair/current/default/common/settings.sh
cd "$HOME/CERES-SARB-MODIS-VIIRS"
SARB_LOG_DIR=/CERES/sarb/dfillmor/MODIS_VIIRS/logs/modis_c7_l2_200004_202203_f09_3hr_gls_rho05
mkdir -p "$SARB_LOG_DIR"
qsub -q scf.q -l h_rt=12:00:00 -t 1-22 -tc 4 \
  -N modis_c7_years -o "$SARB_LOG_DIR" \
  scripts/ami_grid_years.sh config/campaigns/modis_c7_200004_202203.txt
qstat -u dfillmor
```

Every array task selects exactly one line of the manifest and executes one
annual config. There is no parallel environment request; numerical libraries
are limited to one thread. Record the returned array ID and inspect
`qacct -j <ID>` for completed task exit statuses. A held initial submission may
be used to record the job ID and synchronize documentation before `qrls <ID>`.
Queue acceptance is separate from successful annual completion.

For recovery, resubmit only failed task indices with the same manifest and
configuration. Use `--overwrite` only when intentionally regenerating existing
valid days. A partially completed month resumes on retry; other months continue
after individual day failures and the job exits nonzero if any failed.

## Implementation validation

Task 5.8 passes 1,071 offline tests and the type/format/shell checks. Review
findings were fixed with interrupted-rebuild and optional-HDF4 coverage. The
nine focused gap tests pass; without HDF4, the C7 test passes and the eight
HDF4-dependent cases skip. Baseline processing hashes are unchanged. The annual
configs share processing SHA-256
`1524d4597c06aec55ea61104ad14057e5713b1b331192b28c09bf887f1972e10`.
