# NetCDF provenance

New CAM7 grid files and AERONET matchup tables record the following global
attributes. The original CAM7 contract remains compatible with older files;
existing files are not relabeled with a newer processing revision.

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
| `python_environment` | JSON Python version/build/compiler, executable/prefix, installed Python package versions, Conda builds/package hashes and native-library details |
| `execution_environment` | JSON of explicitly selected thread-count and scheduler allocation variables |

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
file's complete attributes are retained separately in `reused_l2_provenance`,
along with its path and SHA-256, preserving the source processing history.

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

Software inventory is captured once per Python process. It includes all installed
Python distribution versions, Conda package names/versions/builds/subdirectories
and available package hashes, plus NetCDF-C, HDF5 and NumPy BLAS/LAPACK build
information. It records whether Conda metadata is available and any unreadable
package records. It reads neither credential stores nor arbitrary environment
variables and does not copy package download URLs. The selected execution
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
pipeline currently retains source counts and processing hashes, not a complete
per-L2-file checksum manifest. Do not treat a pathname or a configured template
as proof of unchanged data. Future stochastic steps should also record their
random seed; the current gridding pipeline has no stochastic step.

The monthly PDF panel stays selective: repository link, creation dates, method,
grid and time-window count/duration, code revisions and source/output locations.
Runtime inventories and YAML contents stay in NetCDF rather than crowding the
panel. PDF Keywords retain the figure's existing detailed input/provenance record.
Global maps align the block's first line to the neighboring map title; regional maps use
the full reserved cell so shallow latitude bands do not force tiny text. The
DAVINCI name and expansion sit above the shared colorbar with a clear gap from
the provenance entries.
