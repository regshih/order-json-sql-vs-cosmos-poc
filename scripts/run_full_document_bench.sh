#!/usr/bin/env bash
# SECTION 6 - the 50 RPS FULL-DOCUMENT benchmark across the four storage designs.
#
# GET /orders/{id} returning the COMPLETE logical order is now the most important
# endpoint in the POC, so it gets its own sweep: every backend, every rate, and -
# separately - every payload band.
#
# Runs ON THE LOAD VM against the API VM, both inside the VNet, so multi-megabyte
# responses cross a real network boundary. The API is restarted per backend
# because STORAGE_BACKEND is read once at startup.
#
# Payload bands matter more than usual here. At 50 RPS a 5 MB response is about
# 250 MB/s before HTTP overhead, and the question the brief asks is whether the
# DATABASE or the NETWORK becomes the constraint. Mixing payload sizes in one run
# averages that away, so each band is measured on its own.
#
#     bash scripts/run_full_document_bench.sh              # full sweep
#     bash scripts/run_full_document_bench.sh --quick      # 50 RPS only
set -euo pipefail

API_URL="${API_URL:-http://10.0.1.4:8000}"
OUT="${OUT:-results/fulldoc}"
DURATION="${DURATION:-60}"
WARMUP="${WARMUP:-15}"
QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1

BACKENDS="${BACKENDS:-sql-full-json sql-full-json-native cosmos-mongo sql-hybrid cosmos-nosql}"

if [ "$QUICK" = 1 ]; then
  RATES="50"
else
  RATES="10 25 50 100"
fi

# name:min_bytes:max_bytes - bands, not exact sizes, because the generator hits a
# target size rather than a byte-exact one.
BANDS="p500k:400000:700000 p1m:800000:1200000 p2m:1900000:2400000 p3m:2800000:3400000 p5m:4400000:5400000"

mkdir -p "$OUT"

say() { printf '\n=== %s\n' "$*"; }

for B in $BACKENDS; do
  say "backend $B"

  # Restart the API on this backend and wait for it to actually serve.
  bash scripts/vm_api.sh start "$B" >/dev/null 2>&1 || true
  ok=0
  for i in $(seq 1 30); do
    if curl -fsS --max-time 5 "$API_URL/health" >/dev/null 2>&1; then ok=1; break; fi
    sleep 3
  done
  if [ "$ok" != 1 ]; then
    echo "  API did not become healthy on $B - skipping"
    continue
  fi
  # Prove the API is on the backend we think it is. Benchmarking one backend and
  # labelling it another is the single worst failure mode available here.
  ACTUAL="$(curl -fsS --max-time 5 "$API_URL/health" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("backend",""))')"
  if [ "$ACTUAL" != "$B" ]; then
    echo "  REFUSING: /health reports backend=$ACTUAL but expected $B"
    continue
  fi
  echo "  healthy, backend=$ACTUAL"

  # ---- rate sweep, all payload sizes mixed ----
  for R in $RATES; do
    echo "  full @ ${R} rps (all sizes)"
    python3 loadtests/operational/run_load.py \
      --base-url "$API_URL" --backend "$B" --workload full \
      --rps "$R" --duration "$DURATION" --warmup "$WARMUP" \
      --out "$OUT" --note "fulldoc-rate-${R}" >/dev/null 2>&1 || echo "    run failed"
  done

  # ---- payload bands, all at the customer's stated 50 RPS ----
  for BAND in $BANDS; do
    NAME="${BAND%%:*}"; REST="${BAND#*:}"; MIN="${REST%%:*}"; MAX="${REST##*:}"
    echo "  full @ 50 rps band ${NAME} (${MIN}-${MAX} bytes)"
    python3 loadtests/operational/run_load.py \
      --base-url "$API_URL" --backend "$B" --workload full \
      --rps 50 --duration "$DURATION" --warmup "$WARMUP" \
      --size-min "$MIN" --size-max "$MAX" \
      --out "$OUT" --note "fulldoc-band-${NAME}" >/dev/null 2>&1 || echo "    run failed"
  done
done

say "DONE"
ls -1 "$OUT" | wc -l
echo "files in $OUT"
