#!/usr/bin/env bash
# Pull the latest committed code onto a POC VM. Run via `az vm run-command`.
set -euo pipefail
git config --global --add safe.directory /opt/poc 2>/dev/null || true
cd /opt/poc
git fetch -q origin
git reset -q --hard origin/main
chown -R pocadmin:pocadmin /opt/poc
echo "SYNC_OK $(git rev-parse --short HEAD)"
