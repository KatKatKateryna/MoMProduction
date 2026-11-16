#!/bin/bash
#
# docker_up.sh -- build and start the MoMProduction container from this
# folder. The data stays where production.cfg puts it on this machine:
# WORKING_DIR and PRODUCT_DIR (as settings.py resolves them, ~ being the home
# of the user running this) are mounted into the container, ~ as its home
# (/root), any other path at the same path. The mounts and the container name
# are written to docker-compose.override.yml, which docker compose then reads
# for every command (exec, logs, down, ...).
#
# Usage: ./docker_up.sh [--name CONTAINER] [extra docker compose up options]
#   --name CONTAINER  the container's name; without it, the name of the last
#                     run from this folder, else container_name of
#                     docker-compose.yml
#
# A container of that name started from another folder (or not by docker
# compose) stops the script before anything is built.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

name=""
args=()
while [ "$#" -gt 0 ]; do
    case "$1" in
        --name)
            [ "$#" -ge 2 ] || { echo "docker_up.sh: --name needs a container name" >&2; exit 1; }
            name="$2"
            shift 2
            ;;
        --name=*)
            name="${1#--name=}"
            shift
            ;;
        *)
            args+=("$1")
            shift
            ;;
    esac
done

if [ ! -f production.cfg ]; then
    echo "docker_up.sh: production.cfg is missing: cp sample_production.cfg production.cfg, fill it in" >&2
    exit 1
fi

# the container name: --name, else the one of the last run, else the default
if [ -z "$name" ] && [ -f docker-compose.override.yml ]; then
    name=$(sed -n 's/^    container_name: *//p' docker-compose.override.yml)
fi
if [ -z "$name" ]; then
    name=$(sed -n 's/^    container_name: *//p' docker-compose.yml)
fi
if ! [[ "$name" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]]; then
    echo "docker_up.sh: not a valid container name: '$name'" >&2
    exit 1
fi

# a container of that name from another folder would only be found when
# docker compose creates the container, after the image is built
owner=$(docker ps -a --filter "name=^/${name}$" \
    --format '{{.Label "com.docker.compose.project.working_dir"}}' | head -n 1)
if [ -n "$(docker ps -aq --filter "name=^/${name}$")" ] && [ "$owner" != "$(pwd -P)" ]; then
    echo "docker_up.sh: a container named '$name' already exists, started from ${owner:-outside docker compose}" >&2
    echo "docker_up.sh: choose another name: ./docker_up.sh --name <name>" >&2
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
    echo "    container_name: ${name}"
    echo "    volumes:"
    for m in "${mounts[@]}"; do
        mkdir -p "${m%%:*}"
        echo "      - ${m}"
    done
} > docker-compose.override.yml

echo "docker_up.sh: container ${name}"
for m in "${mounts[@]}"; do
    echo "docker_up.sh: ${m%%:*} (host) -> ${m#*:} (container)"
done
docker compose up -d --build "${args[@]}"
