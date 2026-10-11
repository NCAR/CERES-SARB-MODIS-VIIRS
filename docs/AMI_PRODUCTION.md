# AMI production and portable monthly driver

## July 2008 C7 deployment

The production root is `/CERES/sarb/dfillmor/MODIS_VIIRS`. The tracked
`config/runs/modis_c7_l2_200807_f09_3hr_gls_rho05.yaml` selects Terra/Aqua C7,
GLS rho 0.5, and supplied combined-product diagnostics. Its default
`ami-modis-c7` profile reads `/ASDC_archive/MODIS/{platform}/C7/{year}/{doy}`
with an aerosol-product filename filter. See [configuration naming](../config/README.md)
and [C7 interpretation](MODIS_C7.md). Archive staging is disabled; no Earthdata
credentials or network downloads are required for this test.

Develop/test/review on the Mac, commit and push `main`, then pull on AMI:

```bash
cd "$HOME/CERES-SARB-MODIS-VIIRS"
git pull --ff-only origin main
"$HOME/miniforge3/envs/sarb-aod/bin/python" -m pip install --no-deps --no-build-isolation -e .
```

Remote commands and jobs must use Bash; the account's default shell is tcsh.
The separate `sarb-aod` conda environment was cloned offline from the existing
`davinci` environment (Python 3.13.14, numpy 2.4.6, numba 0.65.1, netCDF4
1.7.4). Its scientific package pins match `environment.yml`; DAVINCI's
original environment is not changed. A fresh installation elsewhere can use
`conda env create -f environment.yml`. Invoke environment binaries directly.

Run `show-config` before submission. The job script uses one worker and limits
threaded numerical libraries to one thread, matching a serial Grid Engine slot:

```bash
# The vendor setup reads optional variables such as MANPATH; source without nounset.
set +u
source /SPG_ops/utils/altair/current/default/common/settings.sh
SARB_RUN=config/runs/modis_c7_l2_200807_f09_3hr_gls_rho05.yaml
SARB_LOG=/CERES/sarb/dfillmor/MODIS_VIIRS/logs/modis_c7_l2_200807_f09_3hr_gls_rho05
mkdir -p "$SARB_LOG"
"$HOME/miniforge3/envs/sarb-aod/bin/sarb-aod" show-config -c "$SARB_RUN"
qsub -N sarb_c7_smoke -o "$SARB_LOG" scripts/ami_grid.sh "$SARB_RUN" 2008-07-01
```

After checking the smoke job's exit status and eight validated files, choose
one monthly submission. A serial monthly job writes the numeric summary and
metrics directly:

```bash
qsub -N sarb_c7_july -o "$SARB_LOG" scripts/ami_grid.sh "$SARB_RUN"
```

Alternatively, a day array runs at most four serial tasks concurrently; each
task occupies one scheduler slot on its assigned host. This avoids assuming
that the site's round-robin `make` parallel environment provides colocated
cores. Array indices map to the inclusive configured period; July has 31 days:

```bash
qsub -N sarb_c7_july -t 1-31 -tc 4 -o "$SARB_LOG" scripts/ami_grid.sh "$SARB_RUN"
```

When every array task has succeeded, submit the serial monthly command to
validate/resume the existing days and generate the monthly summary and metrics.
Its resume-only timing is not fresh processing throughput. Record the IDs
returned by `qsub`, inspect `qstat -u dfillmor`, and check `qacct -j <job_id>`
plus logs. A submitted or disappeared job is not proof of success. The script
uses `scf.q` and an explicit two-hour walltime per job/task; interrupted work is
resumable. Multi-year production, external acquisition, AERONET validation, and
plot deployment remain separate work.

## Completed July test

The 2026-10-11 [July C7 operational test](validation/ami_c7_200807.md) passed:
9,829 granules, 31 days, 248 validated NetCDF files, 1,355,006,280 bytes
(1.2619 GiB). The array processed 30 fresh days plus one resumed day in
514.150 s with at most four concurrent serial tasks. The monthly validation
and summary pass also succeeded. These C7 files include additional native
combined-product diagnostics and are larger than the earlier C6.1 files below.
Scientific C7 validation and multi-year production remain separate tasks.

## September 2020 NOAA-20-only gridding

`config/runs/viirs_noaa20_c2_l2_202009_f09_3hr_gls_rho05.yaml` selects only
NOAA-20 DT and DB, both Collection 002. The `ami-viirs-noaa20` profile reads
`/ASDC_archive/VIIRS/J1/002/2020/{doy}` with exact aerosol-product and collection
filters. This profile is specific to NOAA-20. It does not stage or modify inputs.

A bounded inventory on 2026-10-11 found 3,981 DT and 4,259 DB granules across
all 30 September dates, totaling 80,122,985,210 bytes. No duplicate acquisition
keys were found. Collection 021 directories contained other products but no
September NOAA-20 aerosol files. This is an archive inventory, not an independent
CMR completeness audit. See [daily counts](validation/ami_noaa20_202009_inventory.tsv).

AMI DT headers report product v2.0.1 (filename token `002`); DB reports v2.0.0.
This run is intentionally distinct from the laptop mixed-sensor baseline's DT
Collection 021. Three DT and three DB samples from September 1, 8 and 30 were
read successfully using the catalog fields. DT QA-selected land/ocean AOD
matched the native `Optical_Depth_Land_And_Ocean`; named wavelength dimensions,
coverage-attribute times and expected-error uncertainties use the existing
VIIRS reader. Shared field maps do not imply equal scientific retrievals across
versions. [Sample evidence](validation/ami_noaa20_002_reader_probe.json) records
the inspected paths, versions, dimensions and retained counts.

An additional September 8 19:36 UTC overpass read retained 29,965 DT pixels
(27,567 land, 2,398 ocean; AOD -0.05 to 4.828) and 34,110 DB pixels (32,435
land, 1,675 ocean; AOD 0.00625 to 3.93706), with finite uncertainties throughout.
This checks land, ocean and high-AOD data; it is not a scientific validation.

The run keeps retrieval-team QA, UTC leap-second handling, f09, eight three-hour
windows/day, GLS rho 0.5, Birge inflation, minimum one pixel and final nonnegative
cell means. Edge granules are disabled, matching the existing baseline. Expected
output is 240 CAM7 files under
`/CERES/sarb/dfillmor/MODIS_VIIRS/grids/viirs_noaa20_c2_l2_202009_f09_3hr_gls_rho05`.
Full config, source collections, code revision and runtime environment are
recorded in NetCDF metadata. Only the `grid` command is submitted; evaluation
and plots are deferred.

After committing/pushing on the Mac and pulling on AMI, use the existing Bash
wrapper. Each array task grids one day with one CPU slot; at most four run together:

```bash
source /SPG_ops/utils/altair/current/default/common/settings.sh
SARB_RUN=config/runs/viirs_noaa20_c2_l2_202009_f09_3hr_gls_rho05.yaml
SARB_LOG=/CERES/sarb/dfillmor/MODIS_VIIRS/logs/viirs_noaa20_c2_l2_202009_f09_3hr_gls_rho05
mkdir -p "$SARB_LOG"
"$HOME/miniforge3/envs/sarb-aod/bin/sarb-aod" show-config -c "$SARB_RUN"
qsub -q scf.q -N noaa20_c2_202009 -t 1-30 -tc 4 -o "$SARB_LOG" scripts/ami_grid.sh "$SARB_RUN"
```

The wrapper requests a two-hour limit per task. Array execution only grids and
validates its daily files; it does not run plots or AERONET evaluation.

### September submission record

Submitted **2026-10-11 20:26:34 UTC**, array **5034005.1-30:1**,
`noaa20_c2_202009`, `scf.q`, concurrency four, one slot per task and `h_rt=7200`.
The deployed clean source revision is
`11cd1410977114cac1b5ddef362f3fc77577bf2f`. Preflight discovery matched all
30 daily inventory counts exactly (8,240 granules), and actual Collection 002
catalog recipes read the September 8 19:36 DT/DB samples successfully.

- Config SHA-256: `4b7e478acbfb2b22c5fa10d1bb62ed5c1cd0f34b285aaeb0db42b41779eae200`.
- Processing SHA-256: `1dd872ae13a6698d73f14da8036f04f05b2c654428751af8e430d3388c97c3cf`.
- At 20:26:52 UTC, tasks 1–4 were running on four compute nodes, with tasks
  5–30 queued. Their logs recorded the expected clean revision and Python 3.13.14.
- Logs are `logs/viirs_noaa20_c2_l2_202009_f09_3hr_gls_rho05/noaa20_c2_202009.o5034005.<task>`
  beneath the production root.
- First four tasks completed with `failed=0`, `exit_status=0`, and walltimes
  47.0–48.4 seconds, producing 32 files; tasks 5–8 started next. The September 1
  00:00 file (3,453,829 bytes) passed the CAM7 contract validator and explicit
  checks for the two NOAA-20 streams, Collection 002, processing hash, clean
  source revision, full config/source YAML, development-agent and runtime metadata.

Completion checked on 2026-10-11: all 30 array tasks have `failed=0` and
`exit_status=0`. The last task ended at 20:32:49.825 UTC, 375.382 seconds after
the first started. All 240 expected files passed contract and processing-provenance
validation, with no empty windows. Output size is 828,918,960 bytes (0.7720 GiB).
The original processing revision and hash agree across the files.

The subsequent authorized interactive plotting uses the tracked
`config/plots/viirs_noaa20_c2_202009_regional_summary.yaml` at `65f556d`.
See [the completion and plot-delivery record](validation/ami_noaa20_202009_plots.md).

## Monthly behavior and recovery

The driver splits the inclusive run period at calendar-month boundaries,
including leap days and partial first/last months. It runs days concurrently
within each month and yields results in month order. Each daily worker receives
the original configuration: the run name, CAM filenames, full configuration
hash, and processing hash are unchanged by chunking. Only the numeric monthly
summarizer receives a copy with the month-specific period.

Valid existing files with matching processing provenance are resumed. A corrupt
or stale day is regenerated by the existing daily pipeline. `--overwrite`
requests regeneration. A failed day marks its month failed, retains successful
outputs, records the error, and permits later months to continue. Rerun the same
command to finish missing work. Exit status is 0 when every month succeeds, 1
when a month fails, and 2 for configuration or orchestration errors.

Each successful month writes its numeric TSV through `paths.summary`. A JSON
metrics file beside it replaces the suffix with `.metrics.json`; it records
elapsed time, grid time, processed/resumed/failed day counts, fresh-work
throughput, validated output-file count and bytes, hashes, and per-day failures.
Resume-only months report no fresh-granule throughput. Output counts/bytes cover
successful days; any partial files from a failed day remain on disk but are not
counted as validated output. Failed months do not create a new numeric summary;
the current JSON status is authoritative if an older summary already exists.

Use distinct `{start}` and `{end}` fields in `paths.summary`, for example the
default `summary_{start}_{end}.tsv`. The driver checks all monthly summary,
metrics, and CAM output paths for collisions before starting any work.

## Measured storage

Read-only measurements of the audited staged manifests and completed CAM files
on the development Mac give the following sizes with the current companion and
per-stream diagnostics:

| Input selection / output run | L2 granules | L2 bytes | CAM files | CAM bytes |
|---|---:|---:|---:|---:|
| July 2008 MODIS C6.1, four streams | 9,518 | 25,414,542,845 | 248 | 1,051,764,528 |
| September 2020 mixed baseline, eight streams | 25,597 | 188,063,167,419 | 240 | 1,444,254,240 |

Manifest-advertised sizes equal the observed file sizes for these selections.
The full September staging set, including NOAA-20 DB v2.1 for comparison,
contains 29,856 granules totaling 229,185,820,680 bytes (213.45 GiB).

Current MODIS files occupy 4,240,986 bytes each (4.04 MiB), and mixed files occupy
6,017,726 bytes each (5.74 MiB). At eight files/day this projects to **11.53 GiB
per 365-day MODIS run** or **16.36 GiB per 365-day mixed run**, excluding plots,
evaluation tables, summaries, and temporary files. Multiply by retained
processing variants. Inputs are shared between runs; their monthly volume
depends on sensors, collections, and available granules, so the two observed
months are examples rather than a uniform multi-year archive estimate.

## Measured development throughput

Fresh, isolated one-day outputs were generated under the repository's
`.cache/batch-benchmark` using `grid_months(..., workers=1)` and the same
processing definitions as the existing GLS ρ = 0.5 campaigns. Existing
campaign files were not changed. The recorded measurements are in
[`validation/batch_benchmark.tsv`](validation/batch_benchmark.tsv):

| Day / selection | Input granules processed | Grid seconds | Total seconds | Granules/second |
|---|---:|---:|---:|---:|
| 2008-07-01 MODIS | 310 | 5.165 | 5.298 | 60.02 |
| 2020-09-08 mixed | 865 | 25.354 | 25.503 | 34.12 |

Each produced eight validated files. These are macOS arm64 development results
with previously accessed local inputs and other campaign jobs running; they
are not AMI performance claims. AMI throughput and memory capacity must be
measured with its confirmed filesystem and worker allocation. Monthly metrics
make that measurement repeatable without changing the science configuration.
