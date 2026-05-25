#!/bin/bash
# setup_crontab.sh — Installs MoMProduction cron jobs for all data-source runs.
# The monitor job is intentionally excluded; add it separately if needed.
#
# Usage:
#   chmod +x first_setup/setup_crontab.sh   # make executable (only needed once)
#   ./first_setup/setup_crontab.sh
#
# Safe to re-run — strips all existing MoM_run.py crontab entries first, then
# appends the definitions below fresh. All other cron jobs are untouched.

set -e

CRON_JOBS=(
    "0 4,11,14,21 * * * cd /home/tester/MoMProduction && /home/tester/miniconda3/envs/mom/bin/python MoM_run.py -j GFMS > /dev/null 2>&1"
    "0 2,7,13,19 * * * cd /home/tester/MoMProduction && /home/tester/miniconda3/envs/mom/bin/python MoM_run.py -j HWRF  >/dev/null 2>&1"
    "0 3,10,23 * * * cd /home/tester/MoMProduction && /home/tester/miniconda3/envs/mom/bin/python MoM_run.py -j DFO >/dev/null 2>&1"
    "0 5,12,17 * * * cd /home/tester/MoMProduction && /home/tester/miniconda3/envs/mom/bin/python MoM_run.py -j VIIRS  >/dev/null 2>&1"
)

# Remove all existing MoM_run.py entries, then append the current definitions
(crontab -l 2>/dev/null | grep -vF "MoM_run.py" || true; printf '%s\n' "${CRON_JOBS[@]}") | crontab -

echo "Done. MoM_run.py cron jobs installed:"
printf '  %s\n' "${CRON_JOBS[@]}"
