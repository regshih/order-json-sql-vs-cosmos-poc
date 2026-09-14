#!/usr/bin/env bash
# Provision the complete POC: Azure resources, SQL schema, Fabric environment.
#
# Everything is configurable; the defaults are what this POC actually deployed.
# Safe to re-run - every step is idempotent.
#
#   bash scripts/deploy.sh
#   LOCATION=eastus2 SQL_SKU=GP_Gen5_4 COSMOS_MAX_RU=10000 bash scripts/deploy.sh
set -euo pipefail

LOCATION="${LOCATION:-westus3}"
BASE_NAME="${BASE_NAME:-orderjsonpoc}"
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-order-json-poc-${LOCATION}}"
SQL_SKU="${SQL_SKU:-GP_S_Gen5_2}"
SQL_MAX_CAPACITY="${SQL_MAX_CAPACITY:-4}"
SQL_MIN_CAPACITY="${SQL_MIN_CAPACITY:-0.5}"
COSMOS_MAX_RU="${COSMOS_MAX_RU:-4000}"
FABRIC_CAPACITY="${FABRIC_CAPACITY:-fabordjsonpoc915d}"
FABRIC_SKU="${FABRIC_SKU:-F2}"
FABRIC_WORKSPACE="${FABRIC_WORKSPACE:-ws-order-json-poc}"
DEPLOY_VMS="${DEPLOY_VMS:-true}"
SSH_KEY_PATH="${SSH_KEY_PATH:-$HOME/.ssh/orderjsonpoc}"
SKIP_FABRIC="${SKIP_FABRIC:-false}"

echo "=== POC deployment ==="
echo "  location        : $LOCATION"
echo "  resource group  : $RESOURCE_GROUP"
echo "  SQL SKU         : $SQL_SKU"
echo "  Cosmos max RU/s : $COSMOS_MAX_RU"
echo "  Fabric capacity : $FABRIC_CAPACITY ($FABRIC_SKU)"
echo "  benchmark VMs   : $DEPLOY_VMS"

command -v az >/dev/null || { echo "az CLI not found"; exit 1; }
az account show >/dev/null || { echo "run 'az login' first"; exit 1; }

OID=$(az ad signed-in-user show --query id -o tsv)
UPN=$(az ad signed-in-user show --query userPrincipalName -o tsv)
MYIP=$(curl -fsS -4 https://ifconfig.me || echo "")
echo "  admin principal : $UPN"
echo "  client IP       : ${MYIP:-<none>}"

if [ "$DEPLOY_VMS" = "true" ]; then
  [ -f "${SSH_KEY_PATH}.pub" ] || {
    echo "-- generating SSH key ${SSH_KEY_PATH}"
    ssh-keygen -t rsa -b 4096 -f "$SSH_KEY_PATH" -N "" -q
  }
  SSH_PUB=$(cat "${SSH_KEY_PATH}.pub")
else
  SSH_PUB=""
fi

echo
echo "-- [1/4] Azure infrastructure (Bicep) --"
DEPLOY_NAME="${BASE_NAME}-$(date +%Y%m%d%H%M%S)"
az deployment sub create \
  --name "$DEPLOY_NAME" --location "$LOCATION" \
  --template-file infra/bicep/main.bicep \
  --parameters baseName="$BASE_NAME" location="$LOCATION" \
               resourceGroupName="$RESOURCE_GROUP" \
               sqlSkuName="$SQL_SKU" sqlMaxCapacity="$SQL_MAX_CAPACITY" \
               sqlMinCapacity="$SQL_MIN_CAPACITY" \
               cosmosMaxThroughput="$COSMOS_MAX_RU" \
               adminPrincipalObjectId="$OID" adminPrincipalName="$UPN" \
               clientIpAddress="$MYIP" \
               deployBenchmarkVms="$DEPLOY_VMS" sshPublicKey="$SSH_PUB" \
  --query "properties.outputs" -o json > artifacts/_deploy_raw.json

python - <<'PY'
import json, pathlib
raw = json.load(open("artifacts/_deploy_raw.json"))
out = {k: v["value"] for k, v in raw.items()}
pathlib.Path("artifacts").mkdir(exist_ok=True)
json.dump(out, open("artifacts/deployment-outputs.json", "w"), indent=2)
for k in sorted(out):
    if "ConnectionString" not in k:
        print(f"  {k} = {out[k]}")
PY
rm -f artifacts/_deploy_raw.json

SQL_FQDN=$(python -c "import json;print(json.load(open('artifacts/deployment-outputs.json'))['sqlServerFqdn'])")
COSMOS_ACC=$(python -c "import json;print(json.load(open('artifacts/deployment-outputs.json'))['cosmosAccountName'])")
API_MI=$(python -c "import json;print(json.load(open('artifacts/deployment-outputs.json')).get('apiVmPrincipalId',''))")
LOAD_MI=$(python -c "import json;print(json.load(open('artifacts/deployment-outputs.json')).get('loadVmPrincipalId',''))")
STORAGE=$(python -c "import json;print(json.load(open('artifacts/deployment-outputs.json'))['storageAccountName'])")
SUB=$(az account show --query id -o tsv)

echo
echo "-- [2/4] data-plane RBAC for the VM identities --"
for MI in $API_MI $LOAD_MI; do
  [ -z "$MI" ] && continue
  az cosmosdb sql role assignment create -g "$RESOURCE_GROUP" -a "$COSMOS_ACC" \
    --role-definition-id 00000000-0000-0000-0000-000000000002 --principal-id "$MI" \
    --scope "/subscriptions/$SUB/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.DocumentDB/databaseAccounts/$COSMOS_ACC" \
    -o none 2>/dev/null || echo "    (cosmos role for $MI already present)"
  az role assignment create --assignee-object-id "$MI" --assignee-principal-type ServicePrincipal \
    --role "Storage Blob Data Contributor" \
    --scope "/subscriptions/$SUB/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.Storage/storageAccounts/$STORAGE" \
    -o none 2>/dev/null || echo "    (storage role for $MI already present)"
done
echo "  done"

echo
echo "-- [3/4] VM bootstrap + SQL schema --"
if [ "$DEPLOY_VMS" = "true" ]; then
  for VM in vm-orderjsonpoc-api vm-orderjsonpoc-load; do
    echo "  bootstrapping $VM ..."
    az vm run-command invoke -g "$RESOURCE_GROUP" -n "$VM" --command-id RunShellScript \
      --scripts "$(cat scripts/vm_bootstrap.sh)" --query "value[0].message" -o tsv \
      | grep -E "BOOTSTRAP_OK|error" || true
  done
  echo "  creating SQL schema and granting the API identity ..."
  TOKEN=$(az account get-access-token --resource https://database.windows.net/ --query accessToken -o tsv)
  az vm run-command invoke -g "$RESOURCE_GROUP" -n vm-orderjsonpoc-api --command-id RunShellScript \
    --scripts "export HOME=/root; cd /opt/poc; export SQL_ACCESS_TOKEN='$TOKEN'; \
      ./.venv/bin/python tools/sql_bootstrap.py --server $SQL_FQDN --database OrderDb \
      --grant-identity vm-orderjsonpoc-api 2>&1 | tail -20" \
    --query "value[0].message" -o tsv | grep -E "SQL_BOOTSTRAP_OK|rows=|FAILED|Error" || true
else
  echo "  SKIPPED (DEPLOY_VMS=false). Run tools/sql_bootstrap.py from inside the VNet."
fi

echo
echo "-- [4/4] Fabric environment --"
if [ "$SKIP_FABRIC" = "true" ]; then
  echo "  SKIPPED (SKIP_FABRIC=true)"
else
  az deployment group create -g "$RESOURCE_GROUP" --name "fabric-$(date +%H%M%S)" \
    --template-file infra/bicep/modules/fabric.bicep \
    --parameters location="$LOCATION" capacityName="$FABRIC_CAPACITY" skuName="$FABRIC_SKU" \
                 adminMembers="['$UPN']" \
                 tags='{"purpose":"POC","project":"order-json-sql-vs-cosmos","createdBy":"deploy.sh"}' \
    --query "properties.outputs.capacityName.value" -o tsv
  python fabric/provision_fabric.py --capacity "$FABRIC_CAPACITY" --workspace "$FABRIC_WORKSPACE"
  python fabric/setup_mirroring.py --open-mirroring
fi

echo
echo "=== deployment complete ==="
echo "Next: docs/RUNBOOK.md"
