#!/bin/bash
#$ -S /bin/bash
#$ -q scf.q
#$ -cwd
#$ -j y
#$ -l h_rt=02:00:00
# Serial job: no PE assumes multiple cores on one host. Caller selects log path.
set -euo pipefail
cd "${SARB_AOD_REPO:-$HOME/CERES-SARB-MODIS-VIIRS}"
SARB_ENV_BIN="${SARB_AOD_ENV:-$HOME/miniforge3/envs/sarb-aod}/bin"
export MPLCONFIGDIR="$PWD/.cache/matplotlib"
export NUMBA_NUM_THREADS=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
SARB_RUN_CONFIG="${1:-config/runs/modis_c7_l2_200807_f09_3hr_gls_rho05.yaml}"
date -u
git rev-parse HEAD
"$SARB_ENV_BIN/python" --version
if [ "${SGE_TASK_ID:-undefined}" != undefined ]; then
    SARB_DAY=$("$SARB_ENV_BIN/python" - "$SARB_RUN_CONFIG" "$SGE_TASK_ID" <<'PY'
import sys
from sarb_aod.config.loader import load_config
days = list(load_config(sys.argv[1]).period.days())
index = int(sys.argv[2]) - 1
if not 0 <= index < len(days):
    raise SystemExit("array task index outside configured period")
print(days[index].isoformat())
PY
)
    "$SARB_ENV_BIN/sarb-aod" grid -c "$SARB_RUN_CONFIG" --day "$SARB_DAY" --workers 1
elif [ "$#" -gt 1 ]; then
    "$SARB_ENV_BIN/sarb-aod" grid -c "$SARB_RUN_CONFIG" --day "$2" --workers 1
else
    "$SARB_ENV_BIN/sarb-aod" grid-months -c "$SARB_RUN_CONFIG" --workers 1
fi
date -u
