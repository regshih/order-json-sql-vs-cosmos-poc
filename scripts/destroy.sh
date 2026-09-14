#!/usr/bin/env bash
# Tear down everything this POC created. Nothing else is touched.
#
# Deletes ONLY:
#   - the POC resource group (all Azure resources inside it)
#   - the Fabric workspace and its items
#   - the Fabric capacity
#
#   bash scripts/destroy.sh                # prompts before deleting
#   bash scripts/destroy.sh --yes          # no prompt
#   KEEP_FABRIC_CAPACITY=true bash scripts/destroy.sh --yes   # pause, don't delete
set -euo pipefail

LOCATION="${LOCATION:-westus3}"
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-order-json-poc-${LOCATION}}"
FABRIC_CAPACITY="${FABRIC_CAPACITY:-fabordjsonpoc915d}"
FABRIC_WORKSPACE="${FABRIC_WORKSPACE:-ws-order-json-poc}"
KEEP_FABRIC_CAPACITY="${KEEP_FABRIC_CAPACITY:-false}"
ASSUME_YES=false
[ "${1:-}" = "--yes" ] && ASSUME_YES=true

echo "=== POC teardown ==="
echo "  resource group  : $RESOURCE_GROUP"
echo "  Fabric workspace: $FABRIC_WORKSPACE"
echo "  Fabric capacity : $FABRIC_CAPACITY (keep=$KEEP_FABRIC_CAPACITY)"
echo

# Safety: refuse to delete a resource group that is not tagged as this POC.
if az group show -n "$RESOURCE_GROUP" >/dev/null 2>&1; then
  PROJECT_TAG=$(az group show -n "$RESOURCE_GROUP" --query "tags.project" -o tsv 2>/dev/null || echo "")
  if [ "$PROJECT_TAG" != "order-json-sql-vs-cosmos" ]; then
    echo "REFUSING: resource group '$RESOURCE_GROUP' is not tagged"
    echo "  project=order-json-sql-vs-cosmos (found: '${PROJECT_TAG:-<none>}')."
    echo "  This guard exists so the script can never delete unrelated resources."
    echo "  If this really is the POC group, set the tag or delete it manually."
    exit 1
  fi
  echo "  resource group tag verified: project=$PROJECT_TAG"
  echo
  echo "  resources that will be deleted:"
  az resource list -g "$RESOURCE_GROUP" --query "[].{name:name,type:type}" -o tsv | sed 's/^/    /'
else
  echo "  resource group $RESOURCE_GROUP does not exist (already deleted?)"
fi

if [ "$ASSUME_YES" != "true" ]; then
  echo
  read -r -p "Delete ALL of the above? Type 'destroy' to confirm: " REPLY
  [ "$REPLY" = "destroy" ] || { echo "aborted"; exit 1; }
fi

echo
echo "-- Fabric workspace --"
python - "$FABRIC_WORKSPACE" <<'PY' || echo "  (workspace cleanup skipped)"
import sys, pathlib
sys.path.insert(0, str(pathlib.Path.cwd()))
from fabric.provision_fabric import Fabric, get_token
name = sys.argv[1]
fab = Fabric(get_token())
ws = fab.find_workspace(name)
if not ws:
    print(f"  workspace {name} not found")
else:
    for item in fab.list_items(ws["id"]):
        s, b, _ = fab.call("DELETE", f"/workspaces/{ws['id']}/items/{item['id']}")
        print(f"  deleted {item['type']:<20} {item['displayName']:<24} -> {s}")
    s, b, _ = fab.call("DELETE", f"/workspaces/{ws['id']}")
    print(f"  deleted workspace {name} -> {s}")
PY

echo
echo "-- Fabric capacity --"
if [ "$KEEP_FABRIC_CAPACITY" = "true" ]; then
  az fabric capacity suspend -g "$RESOURCE_GROUP" --capacity-name "$FABRIC_CAPACITY" -o none 2>/dev/null \
    && echo "  suspended $FABRIC_CAPACITY (billing stopped, data retained)" \
    || echo "  could not suspend $FABRIC_CAPACITY"
else
  echo "  (deleted with the resource group below)"
fi

echo
echo "-- resource group --"
if az group show -n "$RESOURCE_GROUP" >/dev/null 2>&1; then
  az group delete -n "$RESOURCE_GROUP" --yes --no-wait
  echo "  deletion started (async). Track with:"
  echo "    az group show -n $RESOURCE_GROUP"
fi

echo
echo "=== teardown initiated ==="
echo "NOT deleted (delete by hand if you want them gone):"
echo "  - the GitHub repository"
echo "  - local data/ and results/ directories"
