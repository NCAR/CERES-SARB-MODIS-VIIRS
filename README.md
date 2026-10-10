# CERES-SARB-MODIS-VIIRS

Preprocessing of MODIS (and later VIIRS) Level-2 aerosol retrievals into CAM7-ready
550 nm AOD constraint files for CERES-SARB. The package is `sarb_aod`; the CLI is
`sarb-aod`.

Lineage: the gridding, four-stream (Terra/Aqua × Dark Target/Deep Blue) count-weighted
averaging and the CAM7 obs-file contract come from `../CERES-SARB-CAM7`
(`scripts/prepare_modis_era5_grid.py`, `sp1_aod_pipeline/`); the pydantic configuration
pattern, environment expansion and Earthdata staging come from `../DAVINCI`. This repo is
standalone: it ports what it needs rather than importing `davinci_monet`.

## Environment

`environment.yml` is a minimal subset of the DAVINCI conda env (same pins), conda-forge only
with `nodefaults`, so it works under Miniconda as well as Miniforge:

```bash
conda env create -f environment.yml      # or mamba
conda activate sarb-aod
python -m pip install --no-deps -e .
pytest
```

Earthdata Login credentials are read from `~/.netrc` (`chmod 600`):

```text
machine urs.earthdata.nasa.gov
  login <earthdata-username>
  password <earthdata-password>
```

## Configuration

A run config (`config/runs/*.yaml`) states *what* to process; a machine profile
(`config/profiles/{mac,derecho,ami}.yaml`) states *where* things live. The run config is
deep-merged over the profile, so either may set any key.

- Profile selection: `--profile` > `$SARB_AOD_PROFILE` > the run config's `profile:` key.
- Profile search: `$SARB_AOD_PROFILE_DIR`, then `profiles/` beside or one level above the run config.
- String values expand `${VAR}`, bare `$VAR` path segments, and a leading `~`; an unset
  variable is an error.
- Unknown keys, duplicate YAML keys, and unknown template placeholders are errors.
- `sarb-aod show-config -c <run.yaml>` prints the fully resolved configuration.

Path templates are `str.format` strings. Granule directories may use `data_root`,
`platform`, `instrument`, `short_name`, `collection`, `cmr_version`, `file_token`, `year`,
`month`, `day`, `doy`, `yyyymmdd`; manifests may use the product fields plus `start`, `end`.

Products and collections are defined in `sarb_aod/data/catalog.yaml`:

| Products | Collection (= CMR version) | Filename token | Format | Status (2026-10-10) |
|---|---|---|---|---|
| MOD04_L2, MYD04_L2 | 6.1 | `061` | HDF4 | available |
| MOD04_L2, MYD04_L2 | 7 | `007` | NetCDF4 | not yet published on LAADS (only C7 L1B/geolocation) |
| AERDB_L2_VIIRS_SNPP, _NOAA20 | 2 | `002` | NetCDF4 | available |
| AERDB_L2_VIIRS_SNPP, _NOAA20 | 2.1 | `021` | NetCDF4 | NOAA-20 from 2018; SNPP only from 2026-06 |
| AERDT_L2_VIIRS_SNPP, _NOAA20 | 2.1 | `021` | NetCDF4 | available |

Run configs choose collections per product, e.g.
`stage.products: {MOD04_L2: ["6.1", "7"], AERDB_L2_VIIRS_SNPP: ["2"]}`.

## Staging L2 granules

```bash
sarb-aod stage -c config/runs/modis_l2_200807.yaml --dry-run   # CMR search, counts, sizes
sarb-aod stage -c config/runs/modis_l2_200807.yaml             # download (resumable)
sarb-aod audit -c config/runs/modis_l2_200807.yaml             # re-verify against manifests
sarb-aod stage -c config/runs/modis_viirs_l2_202009.yaml       # Sept 2020 MODIS + VIIRS
```

Default layout (mac profile), following NASA DOI naming `<SHORTNAME>.<token>`:

```text
~/Data/Terra/MODIS/MOD04_L2.061/2008/07/01/MOD04_L2.A2008183.0020.061.2017292033454.hdf
~/Data/Aqua/MODIS/MYD04_L2.061/2008/07/01/MYD04_L2.A2008183.0015.061.2018035100054.hdf
~/Data/Terra/MODIS/MOD04_L2.061/manifests/manifest_20080701_20080731.tsv
```

Granules are bucketed by the acquisition date in their filename (`.AYYYYDDD.`), downloaded
into `<day>/.partial/`, and moved into place only after the byte count matches CMR and the
file starts with the expected HDF4/HDF5 signature. Re-runs skip verified files and replace
truncated or corrupt ones. A collection with no granules in CMR is reported as unavailable,
not as an error.
