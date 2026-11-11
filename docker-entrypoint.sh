#!/bin/bash
# Container start-up: check the configuration, create the folder tree, install
# the cron schedule, then hand over to cron in the foreground. Anything wrong
# stops here with the action to take, before cron is installed.
set -euo pipefail

cd /opt/MoMProduction
mkdir -p /var/lib/mom/logs /var/lib/mom/backfill_logs

fail() {
    echo "docker-entrypoint.sh: ERROR: $*" >&2
    echo "docker-entrypoint.sh: cron not started; fix it and start the container again" >&2
    exit 1
}

if [ ! -f production.cfg ]; then
    fail "production.cfg is missing. Create it in the repository folder" \
        "(cp sample_production.cfg production.cfg, fill it in); docker-compose.yml mounts it"
fi

source /opt/conda/etc/profile.d/conda.sh
conda activate momenv

# the data folders must be mounted from the host (docker_up.sh does it):
# anything else would be written inside the container and gone when it is
# recreated
python -c '
import os, sys
import settings
bad = [p for p in (settings.WORKING_DIR, settings.PRODUCT_DIR) if not os.path.ismount(p)]
for p in bad:
    print(f"not mounted from the host: {p}", file=sys.stderr)
sys.exit(1 if bad else 0)
' || fail "WORKING_DIR / PRODUCT_DIR are not mounted: start the container with ./docker_up.sh"

# folder tree, watershed shapefile, credentials
python initialize.py || fail "initialize.py stopped (see above): complete production.cfg"

# the five jobs, through run_mom_job.sh
./setup_crontab.sh

# any argument is run instead of cron, e.g.
#   docker compose run --rm mom ./run_mom_job.sh -j GFMS
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

echo "cron started; job output goes to logs/<job>.log (docker_run/logs on the host)"
exec cron -f
