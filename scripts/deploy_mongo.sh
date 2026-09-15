#!/usr/bin/env bash
# SCENARIO B - provision Azure Cosmos DB for MongoDB with 16 MB document support.
#
# Deliberately a shell script rather than Bicep. From the capability
# documentation (SOURCES.md M.1):
#
#     "Changing capabilities using Azure Resource Manager is not available for
#      Azure Cosmos DB for MongoDB accounts."
#
# So this account CANNOT be fully declared as infrastructure-as-code the way
# every other resource in this POC is. That is a real operational finding for
# anyone running a Bicep/Terraform estate, not a shortcut taken here.
#
# ORDER MATTERS AND IS ENFORCED:
#   1. create the account
#   2. add EnableMongo16MBDocumentSupport
#   3. VERIFY it is present  <-- hard gate, script aborts if absent
#   4. only then create the database and collection
#
# Step 3 is not ceremony. The 16 MB limit "applies only to collections created
# after enabling the feature": a collection created one step too early silently
# keeps the 2 MB limit and every later result would be wrong for a reason
# invisible at the wire.
#
# IRREVERSIBLE: EnableMongo16MBDocumentSupport is documented Removable: No, and
# is incompatible with customer-managed keys. This account can never be brought
# under CMK. See SOURCES.md M.2.
#
#     bash scripts/deploy_mongo.sh
#     bash scripts/deploy_mongo.sh --dry-run
set -euo pipefail

RG="${RG:-rg-order-json-poc-westus3}"
LOCATION="${LOCATION:-westus3}"
BASE="${BASE:-orderjsonpoc}"
VNET="${VNET:-vnet-orderjsonpoc}"
SUBNET="${SUBNET:-snet-private-endpoints}"
MONGO_DB="${MONGO_DB:-orderdb}"
MONGO_COLLECTION="${MONGO_COLLECTION:-orders_full}"
SERVER_VERSION="${SERVER_VERSION:-7.0}"
THROUGHPUT="${THROUGHPUT:-10000}"
TAG_PROJECT="project=order-json-sql-vs-cosmos"
DNS_ZONE="privatelink.mongo.cosmos.azure.com"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

# Account name must be globally unique and <=44 chars. Derived from the RG id
# the same way the Bicep modules do, so re-running lands on the same name.
SUFFIX="$(az group show -g "$RG" --query id -o tsv | sha1sum | cut -c1-13)"
ACCOUNT="${ACCOUNT:-mongo-${BASE}-${SUFFIX}}"

say() { printf '\n=== %s\n' "$*"; }

say "target"
echo "  resource group : $RG"
echo "  account        : $ACCOUNT"
echo "  server version : $SERVER_VERSION"
echo "  database       : $MONGO_DB"
echo "  collection     : $MONGO_COLLECTION"
if [ "$DRY_RUN" = 1 ]; then echo; echo "DRY RUN - nothing will be created"; exit 0; fi

# ---------------------------------------------------------------------------
say "1. account"
# ---------------------------------------------------------------------------
if az cosmosdb show -g "$RG" -n "$ACCOUNT" >/dev/null 2>&1; then
  echo "  exists already - reusing"
else
  az cosmosdb create \
    --resource-group "$RG" \
    --name "$ACCOUNT" \
    --kind MongoDB \
    --server-version "$SERVER_VERSION" \
    --locations regionName="$LOCATION" failoverPriority=0 isZoneRedundant=False \
    --default-consistency-level Session \
    --public-network-access DISABLED \
    --tags $TAG_PROJECT scenario=B-full-document \
    --output none
  echo "  created"
fi

# ---------------------------------------------------------------------------
say "2. capabilities (NOT settable via ARM/Bicep - CLI only)"
# ---------------------------------------------------------------------------
# The list must be inclusive: only explicitly named capabilities are kept.
#   EnableMongo                    - default, must be restated or it is removed
#   EnableMongo16MBDocumentSupport - the point of this scenario. NOT removable.
#   DisableRateLimitingResponses   - server-side retry, which the 16 MB docs
#                                    explicitly recommend for large documents
CURRENT="$(az cosmosdb show -g "$RG" -n "$ACCOUNT" --query "capabilities[].name" -o tsv | tr '\n' ' ')"
echo "  before: ${CURRENT:-<none>}"
az cosmosdb update \
  --resource-group "$RG" \
  --name "$ACCOUNT" \
  --capabilities EnableMongo EnableMongo16MBDocumentSupport DisableRateLimitingResponses \
  --output none
echo "  update submitted"

# ---------------------------------------------------------------------------
say "3. VERIFY the capability before any collection exists"
# ---------------------------------------------------------------------------
CAPS="$(az cosmosdb show -g "$RG" -n "$ACCOUNT" --query "capabilities[].name" -o tsv | tr '\n' ' ')"
echo "  after : $CAPS"
case "$CAPS" in
  *EnableMongo16MBDocumentSupport*) echo "  OK - 16 MB document support is enabled" ;;
  *)
    echo "  FAIL - EnableMongo16MBDocumentSupport is NOT present."
    echo "  Refusing to create the collection: it would silently keep the 2 MB"
    echo "  limit and every size result from it would be wrong."
    exit 1
    ;;
esac

# Record what the account actually reports, for the write-up.
mkdir -p artifacts
az cosmosdb show -g "$RG" -n "$ACCOUNT" \
  --query "{name:name, kind:kind, serverVersion:apiProperties.serverVersion, \
            capabilities:capabilities[].name, publicNetworkAccess:publicNetworkAccess, \
            backupPolicy:backupPolicy.type, consistency:consistencyPolicy.defaultConsistencyLevel, \
            location:location}" \
  -o json > artifacts/mongo-account.json
echo "  -> artifacts/mongo-account.json"

# ---------------------------------------------------------------------------
say "4. database and collection (only now)"
# ---------------------------------------------------------------------------
az cosmosdb mongodb database create \
  --resource-group "$RG" --account-name "$ACCOUNT" --name "$MONGO_DB" \
  --output none 2>/dev/null || echo "  database exists"

# Shard key is _id (the order id). A point read by _id is then single-partition,
# which is what a full-document API does on every request. Sharding on
# customerId instead would make the hot path a cross-partition fan-out to
# optimise a query this workload rarely issues.
if az cosmosdb mongodb collection show -g "$RG" -a "$ACCOUNT" -d "$MONGO_DB" -n "$MONGO_COLLECTION" >/dev/null 2>&1; then
  echo "  collection exists"
else
  az cosmosdb mongodb collection create \
    --resource-group "$RG" --account-name "$ACCOUNT" \
    --database-name "$MONGO_DB" --name "$MONGO_COLLECTION" \
    --shard "_id" \
    --max-throughput "$THROUGHPUT" \
    --output none
  echo "  created (autoscale max ${THROUGHPUT} RU/s, shard key _id)"
fi

# ---------------------------------------------------------------------------
say "5. private endpoint + DNS (public access is disabled by tenant policy)"
# ---------------------------------------------------------------------------
PE="pe-mongo-${BASE}"
ACCOUNT_ID="$(az cosmosdb show -g "$RG" -n "$ACCOUNT" --query id -o tsv)"

if az network private-endpoint show -g "$RG" -n "$PE" >/dev/null 2>&1; then
  echo "  private endpoint exists"
else
  az network private-endpoint create \
    --resource-group "$RG" --name "$PE" --location "$LOCATION" \
    --vnet-name "$VNET" --subnet "$SUBNET" \
    --private-connection-resource-id "$ACCOUNT_ID" \
    --group-id MongoDB \
    --connection-name "${PE}-conn" \
    --tags $TAG_PROJECT \
    --output none
  echo "  private endpoint created"
fi

if az network private-dns zone show -g "$RG" -n "$DNS_ZONE" >/dev/null 2>&1; then
  echo "  dns zone exists"
else
  az network private-dns zone create -g "$RG" -n "$DNS_ZONE" --tags $TAG_PROJECT --output none
  az network private-dns link vnet create \
    -g "$RG" -n "link-${VNET}-mongo" -z "$DNS_ZONE" \
    -v "$VNET" -e false --output none
  echo "  dns zone + vnet link created"
fi

az network private-endpoint dns-zone-group create \
  -g "$RG" --endpoint-name "$PE" -n default \
  --private-dns-zone "$DNS_ZONE" --zone-name mongo \
  --output none 2>/dev/null || echo "  dns zone group exists"

say "DONE"
echo "  account    : $ACCOUNT"
echo "  add to the API environment:"
echo "    MONGO_ACCOUNT=$ACCOUNT"
echo "    MONGO_DATABASE=$MONGO_DB"
echo "    MONGO_COLLECTION=$MONGO_COLLECTION"
echo "    AZURE_RESOURCE_GROUP=$RG"
echo "  (the connection string is fetched from ARM at runtime and never stored)"
