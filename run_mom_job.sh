#!/bin/bash
#
# run_mom_job.sh -- cron entry point for MoM Production jobs.
#
# Activates the conda environment rather than just calling the env's
# interpreter by absolute path. Activation is what puts $CONDA_PREFIX/bin on
# PATH and runs the GDAL/PROJ activate.d hooks (GDAL_DATA, GDAL_DRIVER_PATH,
# PROJ_DATA, CPL_ZIP_ENCODING). Console tools such as gdal_translate are only
# resolvable by name once that has happened.
#
# Usage -- arguments are passed straight through to MoM_run.py:
#   run_mom_job.sh -j GFMS
#   run_mom_job.sh -j DFO --fixdate 20261007
#
# Overridable via the environment: CONDA_SH, CONDA_ENV, PROJECT_DIR.

set -euo pipefail

CONDA_SH="${CONDA_SH:-/root/miniconda3/etc/profile.d/conda.sh}"
CONDA_ENV="${CONDA_ENV:-myenv}"
PROJECT_DIR="${PROJECT_DIR:-/root/MoMProduction}"

if [ ! -f "$CONDA_SH" ]; then
    echo "run_mom_job.sh: conda profile not found at $CONDA_SH" >&2
    exit 1
fi

# conda.sh and the activate.d hooks reference unset variables; relax nounset
# while they run, then restore it for our own code.
set +u
# shellcheck disable=SC1090
source "$CONDA_SH"
conda activate "$CONDA_ENV"
set -u

if ! command -v gdal_translate >/dev/null; then
    echo "run_mom_job.sh: gdal_translate not on PATH after activating '$CONDA_ENV'" >&2
    exit 1
fi

cd "$PROJECT_DIR"
mkdir -p logs

# exec so the PID MoM_run.py reports is the real one and signals reach Python.
exec python MoM_run.py "$@"
