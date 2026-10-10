# CERES-SARB-MODIS-VIIRS — Claude instructions

MODIS/VIIRS L2 aerosol → CAM7-ready f09 3-hourly AOD constraint files for CERES-SARB.
`AGENTS.md` holds the project overview, design decisions, environment, Earthdata credential
handling, data layout and status, the CAM7 contract, the brownfield reference repos
(`../CERES-SARB-CAM7`, `../DAVINCI`), plotting rules, the cross-model handoff convention and
the git workflow. `docs/IMPLEMENTATION_PLAN.md` holds the task-by-task plan.

@AGENTS.md

Claude-specific reminders:

- **Keep responses brief:** about three points per message. The user cannot scroll up in the
  CLI, so put long material in repo files and point to it.
- At session start, check for `HANDOFF_*.md` / `REVIEW_*.md` in the repo root.
- Develop strictly on `main`; no branches or worktrees unless explicitly instructed. Commit and
  push after each task is complete, tested and reviewed (`/code-review` for the review step).
- Use the `sarb-aod` conda env (`~/miniforge3/envs/sarb-aod/bin/python`).
- Never read `~/.netrc` contents into the conversation; check existence and permissions only.
