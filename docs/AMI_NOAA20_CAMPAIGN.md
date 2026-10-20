# AMI NOAA-20 VIIRS: launch through October 18, 2026

The user requested the complete NOAA-20 VIIRS record, no MODIS, in six-month
jobs on 2026-10-18. The interval starts at the satellite's
[November 18, 2017 launch](https://www.nesdis.noaa.gov/news/noaas-newest-polar-orbiting-satellite-celebrates-milestone)
and ends on October 18, 2026, the request's date in America/New_York.
This is a fixed submission interval; future dates require a later submission.

## Three-month recovery (October 19, 2026)

The initial array has finished, with incomplete output. The user requested
three-month recovery jobs and explicitly chose to keep completed output.
The output inventory found 16,352 of 26,056 expected NetCDF files, occupying
52.63 GiB. All expected filenames are present in 66 of 108 months. The
remaining 42 months are incomplete or absent. This is output-file coverage;
the configured missing-product days still contain explicit missing AOD.

The [preflight record](validation/ami_noaa20_20261019_recovery_preflight.json)
contains the month-by-month counts and archive diagnoses. All 520 existing
files in the four partially populated months pass contract, time and
processing-fingerprint resume checks (65 complete days); retain them too.

Scheduler accounting for array 5063353 reports seven tasks (7–13) killed by
the six-hour walltime limit and two tasks (15 and 17) with exit status 1.
Successful six-month tasks took roughly 48–124 minutes. Several timed-out
tasks produced no daily output, so elapsed time alone does not establish a
need for more computation. A bounded read of a January 2021 Deep Blue sample
stalled in filesystem I/O on the login node. Shorter batches isolate work;
they do not resolve unavailable archive storage.
After that blocked process exited, a bounded retry of the same file read
all 12,282,422 variable elements successfully in 0.206 seconds. This supports
retrying the stalled periods; it does not establish readability of all inputs.

The [three-month manifest](../config/campaigns/viirs_noaa20_c2_c21_20171118_20261018_3month.txt)
splits each original six-month block in half, giving 36 disjoint blocks:
November–January, February–April, May–July and August–October. The initial
November and final October retain their original partial-month boundaries.
The common output tree, processing fingerprints, numerical settings and
May 1, 2026 DT collection transition are unchanged. Both final three-month
configs use DT 021 and DB 002; all earlier configs use 002 for both.

The [October 19 recovery manifest](../config/campaigns/viirs_noaa20_recovery_20261019_3month.txt)
contains only the 16 blocks intersecting incomplete months: November 2020
through April 2024, February–April 2025 and November 2025–January 2026.
This is a dated recovery selection, not a replacement for the full manifest.
It includes completed months adjacent to gaps. Resume validates existing
daily files and preserves their original processing provenance; it also
rebuilds earlier gap outputs if a previously missing product has arrived.
Do not pass `--overwrite`. Retain valid completed days within incomplete
months, and keep earlier job logs as evidence. Cleanup is limited to invalid
or unfinished output artifacts and superseded failed monthly metrics.

The two HDF errors remain reproducible when opening these DT 002 inputs:

- March 31, 2025: `AERDT_L2_VIIRS_NOAA20.A2025090.1336.002.2025091022114.nc`.
- December 14, 2025: `AERDT_L2_VIIRS_NOAA20.A2025348.0630.002.2025348192110.nc`.

HDF5 confirms both files are truncated: the March file contains 8,355,840
bytes but its header expects 13,347,352; the December file contains 4,177,920
bytes but its header expects 7,020,473. The archive remains read-only, and
these granules are not silently excluded or converted to missing-product
days. Recovery tasks 15 and 16 cover these dates and remain held until the
inputs are repaired. Their other days already have completed output.

Submit the recovery with one serial slot per task, at most four concurrent
tasks in `scf.q`, and a twelve-hour limit to allow additional archive-I/O
time. The limit is a scheduling allowance, not an expected runtime. Use a
separate log directory for this attempt. After deploying the reviewed
configuration revision, the submission is:

```bash
source /SPG_ops/utils/altair/current/default/common/settings.sh
cd "$HOME/CERES-SARB-MODIS-VIIRS"
SARB_VIIRS_RECOVERY_LOG_DIR=/CERES/sarb/dfillmor/MODIS_VIIRS/logs/viirs_noaa20_c2_c21_l2_201711_202610_f09_3hr_gls_rho05/recovery_20261019_3month
mkdir -p "$SARB_VIIRS_RECOVERY_LOG_DIR"
qsub -h -q scf.q -l h_rt=12:00:00 -t 1-16 -tc 4 \
  -N noaa20_viirs_3month -o "$SARB_VIIRS_RECOVERY_LOG_DIR" \
  scripts/ami_grid_years.sh config/campaigns/viirs_noaa20_recovery_20261019_3month.txt
# Record the returned job ID, then release only its tasks 1–14 with qrls.
# Keep tasks 15 and 16 held until their truncated archive inputs are repaired.
qstat -u dfillmor
```

Task 5.12 records the configuration and validation; Task 5.13 records actual
cleanup, submission and startup. Queue acceptance is not recovery completion.

The full offline gate passed 1,071 tests (one network test deselected), and
mypy, Black, isort and shell syntax checks passed. Coverage checks confirmed
26,056 unique output paths, 108 distinct monthly summaries and unchanged
processing fingerprints; all 52 full/recovery wrapper dispatches passed.
`codex review --uncommitted` found no actionable issues, independently
confirming coverage and dispatch and passing 77 targeted tests.

### Recovery submission and preservation checks

Configuration revision `364504b84f2b9c911b37931d06846a8dc5c29a12` was deployed
to a clean AMI checkout after the old array had finished. All 16 recovery
configs loaded there with the original DT/DB 002 processing fingerprint.
Cleanup moved the two failed 2025 monthly metrics into
`logs/<run>/recovery_20261019_3month/previous_failed_metrics/`, preserving
their bytes and SHA-256 checksums. No NetCDF file was deleted or modified.
All 520 existing files in partially populated months were checked again by
SHA-256 after cleanup and submission and remained unchanged.

Recovery array **5067937.1–16**, `noaa20_viirs_3month`, was submitted on
**2026-10-20 at 00:55 UTC** (October 19 in America/New_York), using `scf.q`,
one serial slot per task, concurrency four and `h_rt=12:00:00`. Tasks 1–14
were released at **00:55:37 UTC**. Tasks 15 and 16 retain user holds because
their March 31 and December 14, 2025 DT inputs are truncated.

The startup check found tasks 1–4 running, 5–14 queued, and 15–16 held.
Logs record the deployed revision and expected three-month configs.
Task 1 resumed all 61 completed November/December 2020 days with zero days
reprocessed, preserving their NetCDF provenance. The remaining recovery
dates still require completion checks; the two held dates require archive
repair before release. Do not release those tasks merely because the other
tasks finish.

The [recovery submission record](validation/ami_noaa20_20261019_recovery_submission.json)
retains the exact scheduler arguments, task intervals, processing hash,
cleanup paths/checksums, release receipt, startup states and preservation
verification. Runtime records and the per-file preservation manifest remain
in `logs/<run>/recovery_20261019_3month/`.

## Initial six-month configuration and coverage

The [campaign manifest](../config/campaigns/viirs_noaa20_c2_c21_20171118_20261018_6month.txt)
lists 18 tracked configurations in chronological task order. Blocks span
November–April and May–October, anchored to the launch month. The first block
is November 18, 2017–April 30, 2018; the last is May 1–October 18, 2026.
Together they cover 3,257 days without overlaps or gaps, with 26,056 expected
three-hourly output files.

Every config selects exactly `noaa20_dt` and `noaa20_db`, with
`AERDT_L2_VIIRS_NOAA20` and `AERDB_L2_VIIRS_NOAA20`. Tasks 1–17 use Collection
002 for both algorithms. Task 18, May–October 2026, uses DT Collection 021
and DB Collection 002. No MODIS, SNPP or NOAA-21 streams are selected.
Collection 002 matches the
[verified September 2020 AMI run](AMI_PRODUCTION.md#september-2020-noaa-20-only-gridding).
The `ami-viirs-noaa20` profile filters the exact aerosol product names and
selected collection tokens in `/ASDC_archive/VIIRS/J1/{002|021}/YYYY/DDD/`;
archive inputs remain read-only.

The [full Collection 002 filename inventory](validation/ami_noaa20_20171118_20261018_inventory.json)
found 409,200 DT and 445,762 DB granules. Both begin February 17, 2018; DT ends
August 29, 2026 and DB ends October 17, 2026. DT 021 covers all 170 days from
May 1 through October 17, so the last six-month block selects that version
consistently instead of dropping recent DT observations. The transition on
May 1, 2026 is a retrieval-version change, not a homogenized climate record;
each output retains the actual collections and processing fingerprint. This
campaign performs no fitted inter-version correction.

The [DT 021 inventory](validation/ami_noaa20_dt021_20260501_20261018_inventory.json)
and [reader probes](validation/ami_noaa20_dt021_reader_probe.json) retain the
recent-block evidence. The May 1 and October 17 samples yielded 296 and 23,441
QC-accepted pixels respectively, with finite AOD and uncertainty. With the
selected collections, the campaign contains 415,668 DT and 445,762 DB granules.
Both algorithms are absent on 95 days: November 18, 2017–February 16, 2018;
February 22–23 and March 13, 2018; and October 18, 2026. The last day is included
as an explicit archive-latency gap; the newest observed granules are October 17.

Settings retain retrieval-team QA, leap-second-aware UTC, f09, eight windows
per day, GLS rho 0.5, Birge inflation, identity AOD corrections, no edge
granules, nonnegative final cell means and disabled despeckling. Native MODIS
combined-product diagnostics are disabled. The common output tree is:

```text
/CERES/sarb/dfillmor/MODIS_VIIRS/grids/
  viirs_noaa20_c2_c21_l2_201711_202610_f09_3hr_gls_rho05/YYYY/MM/DD/
    MODIS_VIIRS_AOD_3hr_f09.YYYYMMDD.SSSSS.nc
```

The shared filename stem is the established CAM7 contract; it does not imply
MODIS inputs. September's earlier test output has its own run tree.

All configs explicitly set `grid.missing_products: fill`. Days with one
available algorithm use it; days without either write eight missing-AOD files
with zero counts. Missing products are recorded in each file and the monthly
metrics. Early commissioning dates and archive latency are not fabricated
observations. Input corruption and I/O errors still fail their day. A rerun
rebuilds a previously missing-product day if that product has since arrived.
The filename inventory establishes local archive coverage, not external
completeness or per-granule readability.

## Initial six-month resources and submission

Use `scf.q`, one serial slot per task, at most four simultaneous tasks, and
`h_rt=06:00:00`. September 2020's daily tasks took approximately 45–50 seconds,
projecting to roughly 2.3–2.6 hours for 184 days before monthly summary overhead
or cold archive I/O. The six-hour request allows margin. No parallel
environment is requested; numerical libraries use one thread.

The verified September output used 828,918,960 bytes for 240 files. Scaling
that measurement to 26,056 files projects approximately 84 GiB; allow 90 GiB
for metadata variation and logs. The live project filesystem had about 1.2 TiB
free on October 18. Shared free space is not a reservation.

After local validation/review, commit and push, then fast-forward AMI before
either new campaign starts. Queue March 2000 MODIS first, then this VIIRS array.
The existing `ami_grid_years.sh` wrapper accepts a manifest and dispatches one
arbitrary-period config per task; its historical filename does not constrain
the period to a year. Each task runs the resumable monthly driver, producing
monthly summaries and metrics as well as validating its daily grid files.

```bash
source /SPG_ops/utils/altair/current/default/common/settings.sh
cd "$HOME/CERES-SARB-MODIS-VIIRS"
SARB_VIIRS_LOG_DIR=/CERES/sarb/dfillmor/MODIS_VIIRS/logs/viirs_noaa20_c2_c21_l2_201711_202610_f09_3hr_gls_rho05
mkdir -p "$SARB_VIIRS_LOG_DIR"
qsub -q scf.q -l h_rt=06:00:00 -t 1-18 -tc 4 \
  -N noaa20_viirs_6month -o "$SARB_VIIRS_LOG_DIR" \
  scripts/ami_grid_years.sh config/campaigns/viirs_noaa20_c2_c21_20171118_20261018_6month.txt
qstat -u dfillmor
```

Keep executable source/configs fixed while queued or running. Recover failed
tasks by resubmitting only their indices with the same manifest. Do not use
`--overwrite` for routine recovery. Check `qacct` and monthly metrics before
claiming processing completion. Plotting and AERONET evaluation are not part
of this submission.

## Initial six-month validation and submission record

Configuration checks verified all 18 task dispatches with stub executables,
the exact date interval, 26,056 unique output paths, 108 unique monthly summary
paths, the two NOAA-20 streams and their declared collections. Each block's
other settings match the verified September baseline, except for the explicit
missing-product policy and common campaign output name.

Processing SHA-256 is
`aa4ea5f8558c46ac28cd68e8c5757f77c24add9c75ff36d368a0adede6bdc715`
for tasks 1–17, and
`61f5b3a7f955a2160920c43b07cb8fb0505201952c2a4ff2d781be1287f4a89a`
for task 18. The repository gate passed 1,071 offline tests, with one network
test deselected; mypy, Black, isort and shell syntax checks passed. No numerical
implementation or scheduler wrapper was changed for these submissions.

Final review found no actionable issues and passed 72 targeted tests. Its
independent checks confirmed continuous date coverage, unique paths, selected
collections, processing fingerprints, inventory consistency and all 18 wrapper
dispatches.

Array **5063353.1–18**, `noaa20_viirs_6month`, was accepted at
**2026-10-19 01:01:39 UTC** (October 18 at 21:01:39 EDT), with one serial slot
per task, `scf.q`, `h_rt=21600`, and a concurrency limit of four. It was
initially held, then released at **01:02:03.020 UTC**, immediately after March
MODIS job 5063352. At 01:02:31 UTC, tasks 1–4 were running on
`la2520-cn-grid-ami03-pi.cluster.net` and tasks 5–18 were queued. No holds or
scheduler error states remained.

All 18 configs loaded successfully on AMI and matched the local processing
hashes before submission. The filesystem had 1,227,831,115,776 free bytes
(approximately 1,144 GiB). Startup logs recorded the expected manifest entries
and clean revision `c9d722255394887b72261b466bc4fe709b6b4906`. The processing
checkout stays at that revision while jobs run; later documentation-only
submission records are not pulled into it during processing.

[The submission record](validation/ami_noaa20_20171118_20261018_submission.json)
retains all task intervals/configs/collections/hashes, exact `qsub` arguments,
the release receipt, free-space observation and scheduler states. Runtime
`submission.json`, `release.json`, `scheduler_submission.txt` and resolved
config snapshots remain in the campaign log directory. This is queue/startup
evidence, not completed processing or scientific validation.
