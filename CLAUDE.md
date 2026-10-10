# CERES-SARB-MODIS-VIIRS — Claude instructions

MODIS/VIIRS L2 aerosol → CAM7-ready f09 3-hourly AOD constraint files for CERES-SARB.
Project overview, Earthdata credential handling, data layout, the CAM7 contract, brownfield
reference repos (`../CERES-SARB-CAM7`, `../DAVINCI`), and git workflow are in AGENTS.md:

@AGENTS.md

Claude-specific reminders:

- Develop strictly on `main`; no branches or worktrees unless explicitly instructed.
- Use the `sarb-aod` conda env (`~/miniforge3/envs/sarb-aod/bin/python`).
- Never read `~/.netrc` contents into the conversation; check existence/permissions only.
