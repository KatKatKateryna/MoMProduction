#!/bin/bash
#
# run_gfms_backfill.sh -- cron entry point for the GFMS image/summary backfill.
#
# Holds an exclusive non-blocking lock, so an hourly tick that lands while the
# previous run is still working exits immediately instead of running a second
# copy over the same files.
#
# Runs backfill_gfms.py only -- no GloFAS, HWRF, DFO, VIIRS or composite work.
#
# Overridable via the environment: CONDA_SH, CONDA_ENV, PROJECT_DIR,
# BACKFILL_START, BACKFILL_END, BACKFILL_MAX_DAYS.

set -euo pipefail

CONDA_SH="${CONDA_SH:-/root/miniconda3/etc/profile.d/conda.sh}"
CONDA_ENV="${CONDA_ENV:-myenv}"
PROJECT_DIR="${PROJECT_DIR:-/root/MoMProduction}"
BACKFILL_START="${BACKFILL_START:-20260201}"
BACKFILL_END="${BACKFILL_END:-20261008}"
BACKFILL_MAX_DAYS="${BACKFILL_MAX_DAYS:-4}"

LOCK_FILE="/var/lock/gfms_backfill.lock"

# Re-run under flock. -n = fail rather than queue up behind a running job;
# -E 99 gives "could not acquire" a distinct exit code so it is not confused
# with a genuine failure from the work itself. Not exec'd, so the busy case
# can be reported.
if [ "${_GFMS_BACKFILL_LOCKED:-}" != "1" ]; then
    export _GFMS_BACKFILL_LOCKED=1
    set +e
    flock -n -E 99 "$LOCK_FILE" "$0" "$@"
    rc=$?
    set -e
    if [ "$rc" -eq 99 ]; then
        echo "$(date -u '+[%Y-%m-%dT%H:%M:%SZ]') backfill already running - skipping this tick"
        exit 0
    fi
    exit "$rc"
fi

if [ ! -f "$CONDA_SH" ]; then
    echo "run_gfms_backfill.sh: conda profile not found at $CONDA_SH" >&2
    exit 1
fi

set +u
# shellcheck disable=SC1090
source "$CONDA_SH"
conda activate "$CONDA_ENV"
set -u

if ! command -v gdal_translate >/dev/null; then
    echo "run_gfms_backfill.sh: gdal_translate not on PATH after activating '$CONDA_ENV'" >&2
    exit 1
fi

cd "$PROJECT_DIR"
mkdir -p logs

exec python backfill_gfms.py \
    --start "$BACKFILL_START" \
    --end "$BACKFILL_END" \
    --max-days "$BACKFILL_MAX_DAYS"
