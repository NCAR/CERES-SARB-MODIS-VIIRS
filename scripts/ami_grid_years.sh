#!/bin/bash
#$ -S /bin/bash
#$ -q scf.q
#$ -cwd
#$ -j y
#$ -l h_rt=12:00:00
# Each array task runs one tracked period config in one serial slot.
# Set -t from the manifest length, -tc 4, and an existing log directory via -o.
set -euo pipefail
cd "${SARB_AOD_REPO:-$HOME/CERES-SARB-MODIS-VIIRS}"
SARB_ENV_BIN="${SARB_AOD_ENV:-$HOME/miniforge3/envs/sarb-aod}/bin"
SARB_MANIFEST="${1:-config/campaigns/modis_c7_200004_202203.txt}"
SARB_TASK="${SGE_TASK_ID:-}"
if ! [[ "$SARB_TASK" =~ ^[1-9][0-9]*$ ]]; then
    echo 'A positive SGE_TASK_ID is required (one period config per task).' >&2
    exit 2
fi
SARB_RUN_CONFIG=$(sed -n "${SARB_TASK}p" "$SARB_MANIFEST")
if [ -z "$SARB_RUN_CONFIG" ] || [ ! -f "$SARB_RUN_CONFIG" ]; then
    echo "No tracked run config for task $SARB_TASK in $SARB_MANIFEST" >&2
    exit 2
fi
if [ "${NSLOTS:-1}" != 1 ]; then
    echo 'This job requires one serial slot; do not request a parallel environment.' >&2
    exit 2
fi
export MPLCONFIGDIR="$PWD/.cache/matplotlib"
export NUMBA_NUM_THREADS=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
date -u
git rev-parse HEAD
echo "Analysis config: $SARB_RUN_CONFIG"
"$SARB_ENV_BIN/sarb-aod" grid-months -c "$SARB_RUN_CONFIG" --workers 1
date -u
