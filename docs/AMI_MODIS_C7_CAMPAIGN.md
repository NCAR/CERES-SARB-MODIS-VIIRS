# AMI MODIS C7: March 2000–March 2022

The user authorized 22 April–March analysis years on 2026-10-11, using
Terra and Aqua Dark Target and Deep Blue, Collection 7 per Seiji's request.
Despeckling is explicitly disabled in every yearly configuration. This task
grids observations only; it does not run plots or AERONET evaluation.

The March 2000 supplement extends the record to 265 months. On October 19,
2026, all expected production files and passing monthly metrics were present.
The complete processed record and separate July 2008 trial have now been
downloaded to the laptop as verified monthly archives; see the delivery
record below. The historical submission sections retain their original dates.

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

July's 1,355,006,280 bytes over 31 days initially projected to 327.1 GiB for the
campaign. The deployed annual config's two smoke days each produced 44,529,472
bytes, updating the projection to **333.2 GiB**, approximately 15.1 GiB per full
year. Allow about **340 GiB**, plus logs and metadata variation.
The checked project filesystem had 1,027.4 GiB available;
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

## Deployment and submission

Task 5.8 was committed and pushed as
`1ebdaf499e0c1593244e3224f39fd8965450d1ad`, then fast-forward pulled on AMI.
All 22 configs loaded there with four C7 MODIS streams, disabled despeckling,
identity AOD corrections and the expected processing hash. Resolved config
snapshots and the indexed annual manifest are saved in the campaign log directory.

Two serial `scf.q` smoke jobs used the first annual config and wrote directly
into the campaign tree, so the annual task can resume those days:

| Job | Day | Inputs | Output check | Wallclock |
|---|---|---|---|---:|
| 5034006 | 2000-04-01 | Terra present, Aqua absent | Eight valid files, nonzero AOD counts; Aqua and VIIRS fields missing | 38.823 s |
| 5034007 | 2000-08-06 | Both absent | Eight valid files; AOD and native combined fields missing, all counts zero | 11.027 s |

Both have scheduler `failed=0` and `exit_status=0`. All 16 files passed the CAM7
contract and processing-provenance checks; all explicitly record disabled
despeckling and the correct missing-product lists.

**Array 5034008, tasks 1–22**, was accepted with a concurrency limit of four,
one slot/task, `scf.q`, and `h_rt=12:00:00`. It was submitted held so this
submission record can be committed/pushed and AMI synchronized before processing
begins. After that fast-forward pull, release with `qrls 5034008` and verify
`qstat -u dfillmor`. Keep the executable source/configs fixed while it runs.
This is a queue-submission record, not evidence of completed annual processing.

[The submission evidence](validation/ami_modis_c7_200004_202203_submission.json)
retains the exact `qsub` arguments, array ID, source revision, hash, expected
file count and smoke-job accounting/validation. Runtime submission and release
records belong under:

```text
/CERES/sarb/dfillmor/MODIS_VIIRS/logs/
  modis_c7_l2_200004_202203_f09_3hr_gls_rho05/
    submission.json
    release.json
```

Task 5.9 uses the same tested implementation. The separate documentation commit
records the deployment; no production code was edited on AMI.

Task 5.9 validation: the final offline suite passes 1,071 tests (one network
test deselected); mypy, Black, isort and whitespace checks pass. Required review
found no actionable defects in the deployment record and independently passed
all seven campaign/config/dispatch tests.

## March 2000 supplement

The user requested March 1–31, 2000 on 2026-10-18. The tracked configuration
`config/runs/modis_c7_l2_200003_f09_3hr_gls_rho05.yaml` retains the annual
campaign's processing settings and shared run name. Its 248 expected files go
under the existing campaign tree's `2000/03/DD/` directories. The run name
retains the original April 2000–March 2022 interval so existing consumer paths
remain stable. The 22-year manifest and its date intervals are unchanged.

This supplement uses available Terra/Aqua C7 DT/DB granules with explicit
missing-product outputs, retrieval-team QA, GLS rho 0.5, Birge inflation,
identity AOD corrections, and disabled despeckling. Archive inputs remain
read-only. The existing monthly driver processes one day at a time, validates
outputs, and writes March's summary and metrics. It resumes valid existing
days without `--overwrite`.

Submit one serial job using the existing wrapper and its two-hour runtime
request. `scf.q` matches the campaign's established queue:

```bash
source /SPG_ops/utils/altair/current/default/common/settings.sh
cd "$HOME/CERES-SARB-MODIS-VIIRS"
SARB_MARCH_LOG_DIR=/CERES/sarb/dfillmor/MODIS_VIIRS/logs/modis_c7_l2_200004_202203_f09_3hr_gls_rho05/200003
mkdir -p "$SARB_MARCH_LOG_DIR"
qsub -q scf.q -N modis_c7_200003 -o "$SARB_MARCH_LOG_DIR" \
  scripts/ami_grid.sh config/runs/modis_c7_l2_200003_f09_3hr_gls_rho05.yaml
qstat -u dfillmor
```

The 2026-10-18 filename inventory found 4,560 Terra C7 aerosol granules across
all 31 days, no Aqua granules, and no existing March campaign outputs. The
resolved configuration matches the annual campaign except for its date period,
including processing SHA-256
`1524d4597c06aec55ea61104ad14057e5713b1b331192b28c09bf887f1972e10`.
All 248 output paths are unique and precede the first annual config. Review
verified the monthly wrapper dispatch with a stub and passed 56 focused tests.

The full gate passed 1,071 offline tests (one network test deselected), mypy,
Black, isort, shell syntax and whitespace checks.

Job **5063352**, `modis_c7_200003`, was submitted on October 18, 2026 at
20:59:18 EDT, with one serial slot in `scf.q` and `h_rt=7200`. It was initially
held while the NOAA-20 configurations were deployed. March was released first,
at **2026-10-19 01:02:02.928 UTC**, followed by the NOAA-20 array. At 01:02:31
UTC it was running on `la2520-cn-grid-ami03-pi.cluster.net`; its log recorded
clean revision `c9d722255394887b72261b466bc4fe709b6b4906` and Python 3.13.14.

[The submission record](validation/ami_modis_c7_200003_submission.json)
retains the exact command, source revisions, processing hash, release receipt
and scheduler observation. Runtime records and the combined output log are in
the `200003` log directory shown above. This establishes queue acceptance and
startup; monthly processing completion remains to be checked separately.

## Complete laptop delivery (October 19, 2026)

Task 5.14 inventories and downloads every MODIS C7 product run in the AMI
output tree. The delivery completed at **2026-10-20 01:30:48 UTC** (October 19
at 21:30:48 EDT) under **`~/Data/MODIS_VIIRS`**:

| Dataset | Monthly archives | CAM7 NetCDF files |
|---|---:|---:|
| Production, March 2000–March 2022 | 265 | 64,528 |
| Separate July 2008 trial | 1 | 248 |
| Total | 266 | 64,776 |

The NetCDF files total **360,529,727,432 bytes (335.77 GiB)** before
compression. The `.tar.zst` archives total **15,709,287,856 bytes
(14.63 GiB)**, including 532 monthly TSV summary and JSON metrics files.
An additional **160 evaluation, plot and log files (19,351,795 bytes)** were
copied as normal files under `evaluation/`, `plots/` and `logs/` beneath the
local delivery root. Original Level-2 granules are outside this transfer.

Archives are named `archives/<run>/<run>.YYYYMM.tar.zst`. They preserve
`grids/<run>/YYYY/MM/DD/` and `summaries/<run>/` paths relative to the AMI
product root. The March supplement remains in the production tree despite
its historical `200004_202203` run name. The July trial stays in its separate
run directory. Existing NetCDF files, missing-data windows and provenance
were copied byte for byte; no numerical processing or NetCDF rewriting was
performed. Archives remain compressed locally.

Every archive has an embedded transfer manifest and a JSON sidecar with
the original member paths, sizes and SHA-256 hashes. Local verification
checked the archive SHA-256, the complete tar member inventory, the embedded
manifest, and every member SHA-256 after streaming decompression: **65,308
source members verified**, including the monthly summaries. All associated
evaluation/plot/log files also match their source sizes and SHA-256 hashes.
A downloaded NetCDF sample opens with the expected 192 × 288 f09 dimensions,
AOD field, CF time coordinate and original processing fingerprint.

The exporter uses one streaming read to hash each source member while
packing it, publishes only finished archives, and resumes unchanged exports.
The receiver uses partial-preserving rsync and resumes verified archives.
Local smoke checks covered export/resume, two simultaneous disjoint export
workers, complete-manifest publication, decoded content and deliberate
checksum-mismatch rejection. Initial serial export job 5067939 was replaced
by four-task array **5067940** to accelerate the record; its finished archives
were reused. All four array tasks have `failed=0` and `exit_status=0`.
Source originals and completed export archives remain on AMI.

`archives/DOWNLOAD_COMPLETE.json` records successful local verification;
`archives/verification.json` retains the per-archive verification times and
hashes. `archives/COMPLETE.json` describes remote export completion only.
The delivery's `README.txt` gives optional extraction instructions, and
`transfer_tools/` preserves the operational scripts, configuration and
export accounting. Extraction of all CAM7 files would need about 336 GiB
in addition to the archives.

The [tracked completion record](validation/ami_modis_20261019_download.json)
retains all 266 archive hashes, exact counts/bytes, scheduler accounting,
source/destination paths and the sample-header check. These are transfer
and output-coverage checks; C7 science acceptance remains tracked separately.
