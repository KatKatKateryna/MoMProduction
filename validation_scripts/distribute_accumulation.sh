#!/usr/bin/env bash
# Run one accumulation across several machines and combine the parts.
#
# A long date range is split into chunks, each chunk accumulated independently
# on its own machine, and the resulting partial GeoTIFFs summed. That is exact,
# not an approximation: the partials are per-pixel counts of days over disjoint
# date ranges, so their sum equals a single pass over the union of those dates,
# and merge_accumulations.py refuses to combine parts that share a date.
#
# No machine is named in this script. Everything comes from a nodes file, one
# line per machine, whitespace separated, '#' for comments:
#
#   label  ssh_target  ssh_key  python  script  workdir  stagedir  start  end  expect
#
# e.g.
#   partA  root@10.0.0.2  /root/.ssh/k1  /opt/env/bin/python  /root/accumulate_flood_occurrence.py \
#          /root/partA  /root/stageA  20260101  20260228  53
#
# Use "-" for ssh_target to mean the local machine.
#
# Subcommands:
#   run      launch every node's worker, and a stager feeding it
#   status   show how many rasters each node has folded in
#   collect  pull each node's partial to --out-dir and merge them
set -u

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CMD=${1:-}; shift || true

NODES=""; SRC_SSH=""; SRC_KEY=""; SRC_DIRS=(); PRODUCT="viirs"; PATTERN=""
PREFIX=""; SUFFIX=""; BANDS=""; QUEUE=2; WINDOW_ROWS=96; GDAL_CACHE=48
OUT_DIR=""; OUT_SSH=""; OUT_KEY=""; MERGE_OUT=""; MERGE_PY=""; MERGE_PYTHON=""
START_AT=""

while [ $# -gt 0 ]; do
    case "$1" in
        --nodes) NODES=$2; shift 2 ;;
        --src-ssh) SRC_SSH=$2; shift 2 ;;
        --src-key) SRC_KEY=$2; shift 2 ;;
        --src-dir) SRC_DIRS+=("$2"); shift 2 ;;
        --product) PRODUCT=$2; shift 2 ;;
        --pattern) PATTERN=$2; shift 2 ;;
        --bands) BANDS=$2; shift 2 ;;
        --prefix) PREFIX=$2; shift 2 ;;
        --suffix) SUFFIX=$2; shift 2 ;;
        --queue) QUEUE=$2; shift 2 ;;
        --window-rows) WINDOW_ROWS=$2; shift 2 ;;
        --gdal-cache) GDAL_CACHE=$2; shift 2 ;;
        --start-at) START_AT=$2; shift 2 ;;
        --out-dir) OUT_DIR=$2; shift 2 ;;
        --out-ssh) OUT_SSH=$2; shift 2 ;;
        --out-key) OUT_KEY=$2; shift 2 ;;
        --merge-out) MERGE_OUT=$2; shift 2 ;;
        --merge-python) MERGE_PYTHON=$2; shift 2 ;;
        --merge-script) MERGE_PY=$2; shift 2 ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
    esac
done

[ -n "$NODES" ] && [ -r "$NODES" ] || { echo "need a readable --nodes file" >&2; exit 2; }
OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=20 -o BatchMode=yes"
say() { echo "[$(date -u +%H:%M:%S)] $*"; }

# run a command on a node ("-" means local)
on() { local tgt=$1 key=$2; shift 2
       if [ "$tgt" = "-" ]; then bash -c "$*"; else ssh ${key:+-i "$key"} $OPTS "$tgt" "$*"; fi; }

read_nodes() { grep -vE '^\s*(#|$)' "$NODES"; }

delay_seconds() {
    [ -n "$START_AT" ] || { echo 0; return; }
    local t n; t=$(date -u -d "today $START_AT" +%s 2>/dev/null) || { echo 0; return; }
    n=$(date -u +%s); echo $(( t > n ? t - n : 0 ))
}

case "$CMD" in
run)
    [ ${#SRC_DIRS[@]} -gt 0 ] || { echo "need --src-dir" >&2; exit 2; }
    [ -n "$PREFIX" ] && [ -n "$SUFFIX" ] || { echo "need --prefix and --suffix" >&2; exit 2; }
    DELAY=$(delay_seconds)
    [ "$DELAY" -gt 0 ] && say "scheduled to begin in ${DELAY}s (${START_AT} UTC)"
    while read -r label tgt key py script workdir stagedir start end expect; do
        meta="$workdir/${label}_metadata.json"
        say "node $label -> ${tgt} ($start..$end, $expect rasters)"
        on "$tgt" "$key" "mkdir -p '$workdir' '$stagedir'"
        on "$tgt" "$key" "setsid nohup bash -c 'sleep $DELAY; exec env GDAL_CACHEMAX=$GDAL_CACHE GDAL_NUM_THREADS=2 \
            $py $script --product $PRODUCT ${PATTERN:+--pattern \"$PATTERN\"} ${BANDS:+--bands $BANDS} \
            --result-name ${label}.tiff --work $workdir --source $stagedir \
            --start ${start:0:6} --end ${end:0:6} --wait --expect-total $expect \
            --delete-source-after --materialise-every 0 --window-rows $WINDOW_ROWS' \
            < /dev/null > $workdir/${label}.log 2>&1 &"
        srcargs=""; for d in "${SRC_DIRS[@]}"; do srcargs="$srcargs --src-dir $d"; done
        setsid nohup bash -c "sleep $DELAY; exec $HERE/stage_rasters.sh \
            ${SRC_SSH:+--src-ssh $SRC_SSH} ${SRC_KEY:+--src-key $SRC_KEY} $srcargs \
            $( [ "$tgt" = "-" ] || echo "--dst-ssh $tgt ${key:+--dst-key $key}" ) \
            --stage $stagedir --prefix $PREFIX --suffix $SUFFIX \
            --start $start --end $end --queue $QUEUE --meta $meta" \
            < /dev/null > "/tmp/stage_${label}.log" 2>&1 &
    done < <(read_nodes)
    say "all nodes launched"
    ;;

status)
    while read -r label tgt key py script workdir stagedir start end expect; do
        n=$(on "$tgt" "$key" "grep -c '\"file\":' $workdir/${label}_metadata.json 2>/dev/null || echo 0" 2>/dev/null | tail -1)
        sz=$(on "$tgt" "$key" "stat -c %s $workdir/${label}.tiff 2>/dev/null || echo 0" 2>/dev/null | tail -1)
        printf "  %-10s %4s/%-4s  result=%s\n" "$label" "${n:-0}" "$expect" \
               "$( [ "${sz:-0}" -gt 0 ] && echo "${sz} bytes" || echo "not written yet" )"
    done < <(read_nodes)
    ;;

collect)
    [ -n "$OUT_DIR" ] || { echo "need --out-dir" >&2; exit 2; }
    out() { if [ -n "$OUT_SSH" ]; then ssh ${OUT_KEY:+-i "$OUT_KEY"} $OPTS "$OUT_SSH" "$@"; else bash -c "$@"; fi; }
    out "mkdir -p $OUT_DIR"
    inputs=""
    while read -r label tgt key py script workdir stagedir start end expect; do
        for f in "${label}.tiff" "${label}_metadata.json"; do
            if ! out "test -s $OUT_DIR/$f"; then
                say "collecting $f from $label"
                on "$tgt" "$key" "cat $workdir/$f" | out "cat > $OUT_DIR/$f"
            fi
        done
        sz=$(out "stat -c %s $OUT_DIR/${label}.tiff 2>/dev/null || echo 0")
        [ "${sz:-0}" -gt 0 ] || { say "ERROR: $label partial missing or empty"; exit 1; }
        say "  $label: $sz bytes"
        inputs="$inputs $OUT_DIR/${label}.tiff"
    done < <(read_nodes)
    if [ -n "$MERGE_OUT" ]; then
        say "merging into $MERGE_OUT"
        out "${MERGE_PYTHON:-python3} ${MERGE_PY:-$HERE/merge_accumulations.py} \
             --inputs $inputs --out $MERGE_OUT"
    fi
    ;;

*) sed -n '2,30p' "$0"; exit 2 ;;
esac
