#!/usr/bin/env bash
# Start / stop / status the Order API on the API VM as a systemd service.
# Usage (via az vm run-command):  vm_api.sh start sql | start cosmos | stop | status
set -euo pipefail
ACTION="${1:-status}"
BACKEND="${2:-sql}"
ROOT=/opt/poc
UNIT=/etc/systemd/system/poc-api.service

write_unit() {
  cat > "$UNIT" <<EOF
[Unit]
Description=Order API POC (backend=$BACKEND)
After=network-online.target

[Service]
Type=simple
User=pocadmin
WorkingDirectory=$ROOT
Environment=PYTHONPATH=$ROOT
Environment=STORAGE_BACKEND=$BACKEND
Environment=SQL_SERVER=${SQL_SERVER}
Environment=SQL_DATABASE=${SQL_DATABASE:-OrderDb}
Environment=SQL_POOL_SIZE=${SQL_POOL_SIZE:-32}
Environment=COSMOS_ENDPOINT=${COSMOS_ENDPOINT}
Environment=COSMOS_DATABASE=${COSMOS_DATABASE:-orderdb}
Environment=COSMOS_CONTAINER=${COSMOS_CONTAINER:-orders}
Environment=FABRIC_SQL_ENDPOINT=${FABRIC_SQL_ENDPOINT:-}
Environment=FABRIC_SQL_DATABASE=${FABRIC_SQL_DATABASE:-}
Environment=LOG_LEVEL=WARNING
LimitNOFILE=65535
# Multiple workers: one Python process cannot serialise 250 MB/s of JSON.
# Worker count is a measured variable - see docs/BENCHMARK_METHOD.md.
ExecStart=$ROOT/.venv/bin/uvicorn app.api.main:app --host 0.0.0.0 --port 8000 \
  --workers ${API_WORKERS:-8} --log-level warning --no-access-log \
  --http httptools --loop uvloop --timeout-keep-alive 30
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
}

case "$ACTION" in
  start)
    write_unit
    systemctl daemon-reload
    systemctl enable -q poc-api 2>/dev/null || true
    systemctl restart poc-api
    for i in $(seq 1 40); do
      if curl -fsS -m 5 http://127.0.0.1:8000/health >/dev/null 2>&1; then
        echo "API_UP backend=$BACKEND workers=${API_WORKERS:-8}"
        curl -sS -m 10 http://127.0.0.1:8000/health
        exit 0
      fi
      sleep 1
    done
    echo "API_FAILED"; systemctl status poc-api --no-pager | head -20
    journalctl -u poc-api -n 40 --no-pager | tail -30
    exit 1
    ;;
  stop)   systemctl stop poc-api 2>/dev/null || true; echo "API_STOPPED" ;;
  status) systemctl is-active poc-api || true; curl -sS -m 10 http://127.0.0.1:8000/health || true ;;
  logs)   journalctl -u poc-api -n "${3:-60}" --no-pager ;;
  *) echo "usage: $0 {start <backend>|stop|status|logs [n]}"; exit 2 ;;
esac
