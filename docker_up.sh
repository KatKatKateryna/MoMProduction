#!/bin/bash
#
# docker_up.sh -- build and start the MoMProduction container from this
# folder. The data stays where production.cfg puts it on this machine:
# WORKING_DIR and PRODUCT_DIR (as settings.py resolves them, ~ being the home
# of the user running this) are mounted into the container, ~ as its home
# (/root), any other path at the same path. The mounts are written to
# docker-compose.override.yml, which docker compose then reads for every
# command (exec, logs, down, ...).
#
# Usage: ./docker_up.sh [extra docker compose up options]
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if [ ! -f production.cfg ]; then
    echo "docker_up.sh: production.cfg is missing: cp sample_production.cfg production.cfg, fill it in" >&2
    exit 1
fi

# "host path:container path" of WORKING_DIR and PRODUCT_DIR
mapfile -t mounts < <(python3 -c '
import logging, os
logging.basicConfig = lambda *a, **k: None  # settings.py would open its log file
import settings
home = os.path.expanduser("~")
for path in (settings.WORKING_DIR, settings.PRODUCT_DIR):
    path = os.path.abspath(path)
    inside = "/root" + path[len(home):] if os.path.commonpath([home, path]) == home else path
    print(f"{path}:{inside}")
')

{
    echo "# written by docker_up.sh from production.cfg; do not edit"
    echo "services:"
    echo "  mom:"
    echo "    volumes:"
    for m in "${mounts[@]}"; do
        mkdir -p "${m%%:*}"
        echo "      - ${m}"
    done
} > docker-compose.override.yml

for m in "${mounts[@]}"; do
    echo "docker_up.sh: ${m%%:*} (host) -> ${m#*:} (container)"
done
docker compose up -d --build "$@"
