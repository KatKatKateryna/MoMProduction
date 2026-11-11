#!/bin/bash
# setup_crontab.sh — Installs MoMProduction cron jobs for all data-source runs.
# The monitor job is intentionally excluded; add it separately if needed.
#
# Usage:
#   chmod +x setup_crontab.sh   # make executable (only needed once)
#   ./setup_crontab.sh
#   Or just:
#   bash ./setup_crontab.sh
#
# Jobs are launched through run_mom_job.sh, which activates the conda
# environment before calling MoM_run.py. Calling the env's python by absolute
# path is NOT sufficient: without activation the env's bin/ is missing from
# PATH and the GDAL/PROJ activate.d hooks never run, so console tools such as
# gdal_translate cannot be resolved and image products are silently skipped.
#
# Safe to re-run — strips this clone's existing MoM entries (both the current
# run_mom_job.sh form and the legacy direct MoM_run.py form) before appending
# the definitions below fresh. All other cron jobs, including the MoM jobs of
# other clones, are untouched.

set -euo pipefail

# default: the folder of this script (the repository root)
PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
RUNNER="$PROJECT_DIR/run_mom_job.sh"
LOG_DIR="$PROJECT_DIR/logs"

if [ ! -f "$RUNNER" ]; then
    echo "setup_crontab.sh: runner not found at $RUNNER" >&2
    exit 1
fi

chmod +x "$RUNNER"
mkdir -p "$LOG_DIR"

CRON_JOBS=()
while read -r job minute hours; do
    CRON_JOBS+=("$minute $hours * * * $RUNNER -j $job >> $LOG_DIR/${job,,}.log 2>&1")
done < <(cd "$PROJECT_DIR" && python3 -c "
import settings
for job, (minute, hours) in settings.CRON_SCHEDULE.items():
    print(job, minute, hours)
")

if [ "${#CRON_JOBS[@]}" -eq 0 ]; then
    echo "setup_crontab.sh: no jobs read from settings.py, crontab not changed" >&2
    exit 1
fi

# Remove this clone's existing MoM entries (current and legacy forms), then
# append fresh. Entries of other clones on the machine (the global run,
# another box) are kept.
(crontab -l 2>/dev/null | awk -v runner="$RUNNER " -v project="$PROJECT_DIR/" '
    !(index($0, runner) || (index($0, project) && /MoM_run\.py/))
 ' || true; \
 printf '%s\n' "${CRON_JOBS[@]}") | crontab -

echo "Done. MoM cron jobs installed:"
printf '  %s\n' "${CRON_JOBS[@]}"
