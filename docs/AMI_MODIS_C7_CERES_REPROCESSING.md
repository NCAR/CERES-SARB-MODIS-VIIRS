# Sequential MODIS C7 CERES reprocessing

David authorized reprocessing the March 2000–March 2022 record on November 6,
2026, with sequential execution. This campaign publishes the names and compact
provenance in [PLAN_VARNAME.md](../PLAN_VARNAME.md).

## Periods and settings

The tracked manifest is
`config/campaigns/modis_c7_200003_202203_ceres.txt`. Its 45 configurations cover
March 1, 2000 through March 31, 2022 without gaps or overlap: 44 six-month blocks
(March–August and September–February), followed by March 2022. David confirmed
the six-month job size before submission. The expected inventory is **265 months, 8,066 days
and 64,528 three-hour files**.

The retrieval, QC, timing and weighting settings match the corresponding
historical configurations: Terra/Aqua C7 DT/DB, retrieval-team QA,
leap-second-aware UTC, GLS with rho 0.5 and Birge inflation, minimum count 1,
identity AOD correction, no edge granules and disabled despeckling. The
producer-supplied combined diagnostic remains separate from the GLS merge.
`grid.missing_products: fill` retains all eight windows on days with missing
archive products, including the period before Aqua observations began.

All configurations use `output.naming: ceres`, `output.stem: auto` and the
shared run name `modis_c7_l2_200003_202203_f09_3hr_gls_rho05_ceres`. The first
and last output paths are:

```text
/CERES/sarb/dfillmor/MODIS_VIIRS/grids/modis_c7_l2_200003_202203_f09_3hr_gls_rho05_ceres/2000/03/01/CER_AOD-3hr-f09_MODIS-Terra-Aqua.20000301.00000.nc
/CERES/sarb/dfillmor/MODIS_VIIRS/grids/modis_c7_l2_200003_202203_f09_3hr_gls_rho05_ceres/2022/03/31/CER_AOD-3hr-f09_MODIS-Terra-Aqua.20220331.75600.nc
```

The filename describes the configured platforms and remains stable across
archive gaps. Fields include `AOD_mean`, `AOD_mean_Terra_DT` and the other three
configured MODIS streams; VIIRS fields are absent. Actual daily L2 pathnames
are recorded in `input_file_paths`, together with compact software versions.
The existing historical output tree and its provenance remain unchanged.
CAM7 reader changes are deferred; a compatibility copy can rename `AOD_mean`
to `aod_weighted_mean` with `ncrename` as documented in the naming plan.

## Deployment and sequential submission

Develop, verify, commit and push locally, then fast-forward the clean AMI
checkout at `$HOME/CERES-SARB-MODIS-VIIRS`. The existing environment is
`$HOME/miniforge3/envs/sarb-aod`; call its binaries directly.

Use `scripts/ami_grid.sh` with one configuration argument for each manifest
entry. Each job runs `grid-months --workers 1`, processing its months and days
chronologically. Request one serial slot on `scf.q`, numerical-library thread
counts of one (set by the wrapper), and `h_rt=12:00:00` per job. Override the
wrapper's two-hour default at submission. Do not request a parallel environment.

Submit the manifest entries in order and give every job after the first
`-hold_jid <previous-job-id>`. These dependencies enforce chronological period
execution with at most one job in this campaign running at a time. An array
concurrency limit alone does not establish this dependency order. Store every
returned job ID and its configuration before submitting the next job.

Grid Engine releases a dependency when the preceding job finishes, including
an unsuccessful finish. The monthly driver records individual day failures,
continues through the configured period, and exits nonzero if any month fails.
Check scheduler accounting and monthly metrics when assessing completeness;
recover failed periods with the same configurations without `--overwrite`.
Valid existing days resume, and gap outputs are reconsidered if inputs appear.

The local real-input C7 smoke test wrote eight files from two cached Terra/Aqua
granules. All files passed contract and provenance checks, contained no VIIRS
variables or Conda inventory, and recorded both actual input pathnames. All
240 variable arrays and their masks exactly matched a legacy-format run using
the same inputs (3,338 contributing retrievals). This tests the publication
format and numeric parity, not full-day source coverage.

Before submission, verify live SSH/GitHub access, a clean remote checkout,
the deployed revision, queue availability, free space and representative input
directories. The November 6 preflight found no jobs for `dfillmor`, an unlimited
queue-level runtime on `scf.q`, about 5.5 TiB free on `/CERES`, and source
directories for March 2000, July 2008 and March 2022. These observations do
not establish complete or readable L2 holdings for every day; processing and
the missing-product metrics provide that evidence.

Logs belong under
`/CERES/sarb/dfillmor/MODIS_VIIRS/logs/modis_c7_l2_200003_202203_f09_3hr_gls_rho05_ceres/`.
Monthly summaries and metrics use the same run name under `summaries/`.
Submission evidence and the producing code revision are recorded separately
from eventual completion. The scientific acceptance and uncertainty limits
of the historical C7 campaign still apply.

## November 6 submission

AMI deployed code/config commit `c39ad801c8b03a3ca3333ac05733a57e6c583170`.
Grid Engine accepted **45 jobs, 5159343–5159387**, with job 5159343 covering
March–August 2000 and each subsequent job depending on its predecessor.
Verification found job 5159343 running on `scf.q`, with the remaining 44 jobs
in dependency wait. All 45 predecessor links, single-slot requests and
12-hour runtime limits were checked against the live scheduler.

The first UTC day, March 1, 2000, produced all eight files. Each passed the
contract, naming and input-provenance checks; no VIIRS variables or retired
environment inventory were present. The files contain 855,741 contributing
retrievals. This is initial operational verification; the full record is
still processing.

The [submission record](validation/ami_modis_ceres_20261106_submission.json)
contains every job ID, configuration, dependency and receipt, the deployed
revision, scheduler verification, config coverage checks and local smoke-test
evidence. The original receipt remains in the campaign log directory as
`submission_20261106.json`.

These batch jobs continue after closing the local Codex session or disconnecting
SSH. Check progress later with `qstat -u dfillmor` after initializing the
scheduler, then use monthly metrics and scheduler accounting to verify
completion. AMI stays at the deployed processing commit while the local
repository adds this submission record.
