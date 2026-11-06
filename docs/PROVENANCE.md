# NetCDF provenance

New grid files and AERONET matchup tables record the following global attributes.
Grid readers accept both the historical CAM7 and new CERES variable names; see
[the output specification](../VARNAME.md). Existing files retain their original
processing history and metadata.

| Attribute | Meaning |
|---|---|
| `repository_url` | `https://github.com/NCAR/CERES-SARB-MODIS-VIIRS` |
| `date_created` | UTC creation time of this output |
| `git_commit` | Full producing-code commit SHA, or `unknown` outside a checkout |
| `git_describe` | Nearest tag plus distance/SHA, or abbreviated SHA when untagged; `-dirty` means tracked files differ from the commit |
| `git_tags` | Comma-separated tags pointing directly at HEAD; `none` if untagged, `unknown` when Git is unavailable |
| `config` | JSON of the effective, validated configuration, including defaults, expanded paths and runtime overrides |
| `config_sha256` | SHA-256 of the exact canonical JSON stored in `config` |
| `config_files` | JSON list of original run/profile YAML: role, absolute pathname, complete text and file SHA-256 |
| `development_agents` | JSON list of declared LLM development tools, providers, model names/versions, reasoning efforts, roles and notes |
| `compute_platform` | JSON host, OS/kernel name and version, distribution, architecture/processor, logical CPU count, affinity when available and libc version |
| `software_versions` | Compact versions of sarb_aod, Python, NumPy, netCDF4-Python, NetCDF-C and HDF5; no environment paths, package inventory, Conda builds or hashes |
| `execution_environment` | JSON of explicitly selected thread-count and scheduler allocation variables |
| `input_file_paths` | Grid files: newline-separated absolute L2 granule pathnames actually read for the UTC processing day, including configured previous-day edge granules |
| `input_file_count` | Grid files: number of distinct pathnames in `input_file_paths` |
| `input_file_paths_scope` | Explains that the list covers the processing day, rather than only valid contributors to this individual three-hour window |

The pipeline adds configuration attributes when it has a `RunConfig`. Low-level
writers called directly without a configuration can only record code and runtime
provenance. Programmatically constructed configs have an empty `config_files`
list. File-loaded configs retain the exact YAML read at load time, even if those
files are subsequently edited or removed. Copies and worker processes preserve
these snapshots; `config` separately records later overrides. Snapshot bookkeeping
is excluded from configuration hashes. Descriptive development provenance changes
the full configuration hash, but does not change `processing_sha256` or invalidate
existing numerical grid results.

When verified L2 matchups are reused for a target configuration, the target
file records its own code/runtime and configuration provenance. The original
file's processing attributes are retained separately in `reused_l2_provenance`,
along with its path and SHA-256, preserving the source processing history.
The retired `python_environment` inventory is omitted from both top-level
metadata and nested reuse history in new files; `omitted_metadata_attributes`
records that omission. The archived source file itself is never changed.

For example, declare known development information in a tracked run config:

```yaml
provenance:
  development_agents:
    - tool: Codex
      provider: OpenAI
      model_name: unknown
      model_version: unknown
      reasoning_effort: unknown
      role: code_development
      notes: "Scope and date of the code changes assisted by this agent."
```

Replace `unknown` only with verified information; a model alias and an exact
version/snapshot are different facts. Multiple entries can describe separate
development or review agents. This is declared code-development history, not
evidence that an LLM executed the aerosol calculation or a complete authorship
record. The July C6.1/C7 configurations identify the 2026-10-11 provenance/plot
session and explicitly leave its unverified model details unknown.
They separately record the verified review-command report: Codex CLI 0.162.1,
model alias `gpt-6-astra`, reasoning effort `xhigh`, exact snapshot unknown.
Those values describe the review agent, not an inferred identity for the
development session.

The user's 2026-11-06 revision removes the full Python/Conda environment inventory
from newly written metadata. The compact `software_versions` record contains
only six relevant version strings and is captured once per Python process.
It includes no environment prefix, interpreter pathname, installed-package list,
Conda metadata, build strings or package hashes. Archived files that already
contain `python_environment` remain unchanged.

Grid outputs now record exact absolute L2 input pathnames. A DT/DB granule read
once appears once. The list covers all granules read for the UTC day, including
configured previous-day edge inputs; it does not claim that every listed granule
contributes a valid pixel to every individual three-hour window. A day with no
input granules records an empty list string and count zero. Matchup files retain
their existing `source_files` and `aeronet_cache_files` pathname lists.

The selected execution
variables are `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`,
`NUMBA_NUM_THREADS`, `VECLIB_MAXIMUM_THREADS`, `BLIS_NUM_THREADS`,
`NUMEXPR_NUM_THREADS`, `JOB_ID`, `SGE_TASK_ID`, `NSLOTS`, `QUEUE`, `SLURM_JOB_ID`
and `SLURM_CPUS_PER_TASK`.

## Remaining reproducibility requirements

This metadata explains how an output was produced; it is not a promise of
bit-for-bit regeneration. Preserve the matching Git checkout, including any
uncommitted tracked changes indicated by `-dirty`, and an archived environment
or platform-specific lockfile/package artifacts. Git describe does not flag
untracked files. Exact external input files and their checksums (L2, AERONET,
custom catalog and grid files) remain necessary for full input identity; the
pipeline retains exact L2 pathnames, source counts and processing hashes,
but does not record a complete per-L2-file checksum manifest. Do not treat a pathname or a configured template
as proof of unchanged data. Future stochastic steps should also record their
random seed; the current gridding pipeline has no stochastic step.

The monthly PDF panel stays selective: repository link, creation dates, method,
grid and time-window count/duration, code revisions and source/output locations.
Compact runtime metadata and YAML contents stay in NetCDF rather than crowding the
panel. PDF Keywords retain the figure's existing detailed input/provenance record.
Global maps align the block's first line to the neighboring map title; regional maps use
the full reserved cell so shallow latitude bands do not force tiny text. The
DAVINCI name and expansion form a bold, wrapped final entry in the provenance
block, in both spatial and regional summary figures. Spatial AOD labels include
550 nm; the shared colorbar sits under only the center/right retrieval columns.
