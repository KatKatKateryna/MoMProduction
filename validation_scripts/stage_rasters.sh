#!/usr/bin/env bash
# Copy daily source rasters from wherever they live to wherever they are to be
# processed, keeping a few staged ahead so transfers overlap with processing.
#
# Both ends are given as arguments, so nothing about any particular machine is
# baked in. Either end may be local (omit its --*-ssh option). Source files are
# only ever read.
#
# The accumulation worker deletes each staged file once it has folded it in
# (--delete-source-after), so this just keeps the queue topped up, and it skips
# anything the worker's metadata already records as done.
#
# Example:
#   stage_rasters.sh \
#     --src-ssh root@10.0.0.1 --src-key ~/.ssh/id_src \
#     --src-dir /data/VIIRS_image --src-dir /archive/VIIRS_image \
#     --dst-ssh root@10.0.0.2 --dst-key ~/.ssh/id_dst \
#     --stage /root/stage --prefix VIIRS_1day_composite --suffix _flood.tiff \
#     --start 20260101 --end 20260831 \
#     --meta /root/work/acc_metadata.json
set -u

SRC_SSH=""; SRC_KEY=""; DST_SSH=""; DST_KEY=""
SRC_DIRS=(); STAGE=""; PREFIX=""; SUFFIX=""; START=""; END=""; META=""; QUEUE=2

while [ $# -gt 0 ]; do
    case "$1" in
        --src-ssh) SRC_SSH=$2; shift 2 ;;
        --src-key) SRC_KEY=$2; shift 2 ;;
        --src-dir) SRC_DIRS+=("$2"); shift 2 ;;
        --dst-ssh) DST_SSH=$2; shift 2 ;;
        --dst-key) DST_KEY=$2; shift 2 ;;
        --stage) STAGE=$2; shift 2 ;;
        --prefix) PREFIX=$2; shift 2 ;;
        --suffix) SUFFIX=$2; shift 2 ;;
        --start) START=$2; shift 2 ;;
        --end) END=$2; shift 2 ;;
        --meta) META=$2; shift 2 ;;
        --queue) QUEUE=$2; shift 2 ;;
        -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
    esac
done

[ ${#SRC_DIRS[@]} -gt 0 ] || { echo "missing --src-dir" >&2; exit 2; }
for v in STAGE PREFIX SUFFIX START END META; do
    [ -n "${!v}" ] || { echo "missing --${v,,}" >&2; exit 2; }
done

OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=20 -o BatchMode=yes -o ServerAliveInterval=30"
src() { if [ -n "$SRC_SSH" ]; then ssh ${SRC_KEY:+-i "$SRC_KEY"} $OPTS "$SRC_SSH" "$@"; else bash -c "$@"; fi; }
dst() { if [ -n "$DST_SSH" ]; then ssh ${DST_KEY:+-i "$DST_KEY"} $OPTS "$DST_SSH" "$@"; else bash -c "$@"; fi; }
say() { echo "[$(date -u +%H:%M:%S)] $*"; }

say "staging $PREFIX*$SUFFIX $START..$END -> ${DST_SSH:-local}:$STAGE (queue $QUEUE)"
say "sources: ${SRC_DIRS[*]}"
dst "mkdir -p $STAGE"

DONE=$(dst "grep -o '\"file\": \"[^\"]*\"' $META 2>/dev/null | sed 's/.*: \"//;s/\"//'" || true)
say "already accumulated: $(echo "$DONE" | grep -c . || true)"

sent=0; missing=0; failed=0
d=$START
while [ "$d" -le "$END" ]; do
    name="${PREFIX}${d}${SUFFIX}"
    if echo "$DONE" | grep -qxF "$name"; then
        d=$(date -u -d "$d + 1 day" +%Y%m%d); continue
    fi

    while :; do
        n=$(dst "ls $STAGE/*${SUFFIX} 2>/dev/null | wc -l" 2>/dev/null || echo 99)
        [ "$n" -lt "$QUEUE" ] && break
        sleep 20
    done

    found=""; want=""
    for dir in "${SRC_DIRS[@]}"; do
        size=$(src "stat -c %s '$dir/$name' 2>/dev/null" || true)
        if [ -n "$size" ]; then found=$dir; want=$size; break; fi
    done
    if [ -z "$found" ]; then
        missing=$((missing + 1))
        d=$(date -u -d "$d + 1 day" +%Y%m%d); continue
    fi

    ok=0
    for attempt in 1 2 3; do
        src "cat '$found/$name'" | dst "cat > $STAGE/$name.part"
        got=$(dst "stat -c %s $STAGE/$name.part 2>/dev/null" || echo 0)
        if [ "$got" = "$want" ]; then dst "mv $STAGE/$name.part $STAGE/$name"; ok=1; break; fi
        say "attempt $attempt for $name: $got of $want bytes, retrying"
        dst "rm -f $STAGE/$name.part"; sleep 10
    done
    if [ "$ok" = 1 ]; then sent=$((sent + 1)); say "staged $name ($sent)"
    else failed=$((failed + 1)); say "GAVE UP on $name"; fi

    d=$(date -u -d "$d + 1 day" +%Y%m%d)
done

say "done: $sent staged, $missing missing at source, $failed failed"
