#!/usr/bin/env bash
# Prepare a POC benchmark VM: OS packages, ODBC Driver 18, Python venv, repo.
# Idempotent - safe to re-run. Executed via `az vm run-command invoke`.
set -euo pipefail
REPO_URL="${REPO_URL:-https://github.com/regshih/order-json-sql-vs-cosmos-poc.git}"
ROOT=/opt/poc

export DEBIAN_FRONTEND=noninteractive
if ! dpkg -s msodbcsql18 >/dev/null 2>&1; then
  curl -sSL -o /tmp/msprod.deb https://packages.microsoft.com/config/ubuntu/24.04/packages-microsoft-prod.deb
  dpkg -i /tmp/msprod.deb
  apt-get update -qq
  ACCEPT_EULA=Y apt-get install -y -qq msodbcsql18 unixodbc unixodbc-dev >/dev/null
fi
apt-get install -y -qq git python3-venv python3-dev build-essential jq >/dev/null

# Raise file-descriptor and ephemeral-port limits: the open-model load
# generator and the API connection pools both need many concurrent sockets.
grep -q 'poc-limits' /etc/security/limits.conf || cat >> /etc/security/limits.conf <<'EOF'
# poc-limits
* soft nofile 65535
* hard nofile 65535
EOF
sysctl -qw net.ipv4.ip_local_port_range="10000 65000" || true
sysctl -qw net.core.somaxconn=4096 || true

if [ -d "$ROOT/.git" ]; then
  git -C "$ROOT" fetch -q origin && git -C "$ROOT" reset -q --hard origin/main
else
  rm -rf "$ROOT"; git clone -q "$REPO_URL" "$ROOT"
fi

cd "$ROOT"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt
./.venv/bin/pip install -q psutil

mkdir -p /var/log/poc "$ROOT/results" "$ROOT/artifacts" "$ROOT/data"
chown -R pocadmin:pocadmin "$ROOT" /var/log/poc

echo "BOOTSTRAP_OK $(git -C "$ROOT" rev-parse --short HEAD) python=$(./.venv/bin/python -V 2>&1) odbc=$(odbcinst -q -d | tr '\n' ' ')"
