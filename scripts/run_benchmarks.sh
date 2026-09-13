#!/usr/bin/env bash
# Full operational benchmark sweep for one backend.
#
# Runs on the LOAD VM and drives the API VM over the POC VNet, so the
# multi-megabyte responses in Workload C actually cross a network boundary and
# the generator never competes with the API for CPU.
#
#   bash scripts/run_benchmarks.sh <api-host> <backend> [duration] [warmup]
set -euo pipefail
API_HOST="${1:?usage: run_benchmarks.sh <api-host> <backend> [duration] [warmup]}"
BACKEND="${2:?}"
DURATION="${3:-60}"
WARMUP="${4:-15}"
BASE="http://${API_HOST}:8000"
PY=/opt/poc/.venv/bin/python
cd /opt/poc

echo "== health =="
curl -fsS -m 20 "$BASE/health"; echo

run() {  # workload rps
  echo "-- $BACKEND $1 @ $2 rps --"
  $PY loadtests/operational/run_load.py \
      --base-url "$BASE" --backend "$BACKEND" \
      --workload "$1" --rps "$2" --duration "$DURATION" --warmup "$WARMUP" \
      --note "sweep $(date -u +%Y%m%dT%H%M%SZ)" || echo "  (run failed - continuing)"
}

# §15 requires 10/25/50/100/200 RPS. 50 is the PRIMARY point.
for RPS in 10 25 50 100 200; do run mix "$RPS"; done

# §16 workload shapes at the primary 50 RPS point.
for W in summary title cdf search; do run "$W" 50; done

# Workload C - full multi-megabyte orders. Stepped separately and stopped early
# if the system is clearly past its practical limit.
for RPS in 10 25 50; do run full "$RPS"; done

echo "BENCH_DONE $BACKEND"
