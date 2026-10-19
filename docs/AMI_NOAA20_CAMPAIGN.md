# AMI NOAA-20 VIIRS: launch through October 18, 2026

The user requested the complete NOAA-20 VIIRS record, no MODIS, in six-month
jobs on 2026-10-18. The interval starts at the satellite's
[November 18, 2017 launch](https://www.nesdis.noaa.gov/news/noaas-newest-polar-orbiting-satellite-celebrates-milestone)
and ends on October 18, 2026, the request's date in America/New_York.
This is a fixed submission interval; future dates require a later submission.

## Configuration and coverage

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

## Resources and submission

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

## Validation and submission record

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
