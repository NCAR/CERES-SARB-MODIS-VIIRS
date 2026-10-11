# Configuration naming and deployment

Track all production run and machine-profile YAML files in this repository.
Run filenames describe processing, not the machine:

```text
<sensors>_<collections>_l2_<YYYYMM>_<grid>_<cadence>_<weighting>.yaml
modis_c7_l2_200807_f09_3hr_gls_rho05.yaml
```

The filename stem is the run ID and output-directory name. A smoke test uses
the same YAML with `grid --day YYYY-MM-DD`; it does not need a duplicate config.
Existing C6.1/VIIRS campaign names remain stable so their recorded provenance
and output paths continue to work. Name future collection/version comparisons
explicitly; mixed-sensor runs must identify each selected collection in the
config and distinguish alternatives in the run name.

Profiles describe machine paths and input layout. `ami-modis-c7` reads the
existing AMI C7 archive and writes beneath `/CERES/sarb/dfillmor/MODIS_VIIRS`:

```text
grids/<run>/YYYY/MM/DD/MODIS_VIIRS_AOD_3hr_f09.YYYYMMDD.SSSSS.nc
summaries/<run>/summary_YYYYMMDD_YYYYMMDD.tsv
summaries/<run>/summary_YYYYMMDD_YYYYMMDD.metrics.json
evaluation/<run>/
plots/<run>/
logs/<run>/
```

The top-level directory is general; the current AOD-specific filename stem
continues to identify the current CAM product. Additional future quantities
can define their own contracts. Source archive files are not copied into this
tree or changed. `stage` is disabled by `archive_read_only: true`; `grid`
discovers archive inputs directly using the configured basename filter. Staging
`audit` checks download manifests and is not an inventory of the AMI archive.

Use `-p mac` to override machine paths for local C7 development data. The generic
`ami` profile remains suitable for a separately staged tree with an explicitly
set `SARB_AOD_DATA_ROOT`; it is not the mixed-product archive profile.

Develop on the laptop, test/review, commit and push `main`, then on AMI run
`git -C "$HOME/CERES-SARB-MODIS-VIIRS" pull --ff-only origin main`.
Invoke conda commands in Bash, and use the environment's binaries directly for
jobs. Site-only config fixes may be made remotely when needed, but bring them
back to the laptop and commit them before continuing production. Do not hide
effective production settings in untracked YAML or shell edits.
