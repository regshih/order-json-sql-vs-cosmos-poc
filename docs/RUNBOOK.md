# Runbook

Every command needed to reproduce this POC end to end, plus the operational
notes for running it in an environment like the one it was built in.

---

## 0. Prerequisites

| | |
| --- | --- |
| Python | 3.12+ |
| Azure CLI | logged in (`az login`) to a subscription where you can create resource groups |
| ODBC | Microsoft ODBC Driver 18 for SQL Server |
| Fabric | a Fabric-enabled tenant; the deploy script creates the capacity |
| Permissions | Contributor on the subscription; ability to create Entra role assignments |

```bash
python -m pip install -r requirements.txt
cp .env.example .env     # placeholders only; the code uses managed identity
```

---

## 1. Profile the source (Phase 2)

```bash
python tools/profile_source.py \
  --input data/source/150159_Order.MASKED.json.json \
  --json-out artifacts/data-profile.json \
  --md-out docs/DATA_PROFILE.md
```

Emits **structure only** — no customer values. Safe to commit the outputs; the
input is git-ignored.

## 2. Generate synthetic data (Phase 4)

```bash
# One-time: find the growth scale that hits each target size
python generator/synthetic_order_generator.py --calibrate

# Generate a corpus (writes files + a manifest + size statistics)
python generator/synthetic_order_generator.py --orders 500 --seed 42 --customers 8

# Statistics only, no files written
python generator/synthetic_order_generator.py --orders 1000 --seed 42 --no-write

# A single size profile
python generator/synthetic_order_generator.py --orders 20 --profile p5m
```

Determinism: a given `(seed, order_index)` always produces byte-identical JSON,
and order ids derive from the seed rather than the RNG stream — so regenerating
after a code change **upserts** over the same orders instead of duplicating them.

## 3. Local tests (Phase 8)

```bash
python -m pytest tests/unit -q             # no Azure needed
python -m pytest tests/unit -q -k splitter # split/reassemble losslessness
python -m pytest tests/unit -q -k guard    # Cosmos size guard escalation
```

## 4. Deploy (Phases 9–10)

```bash
bash scripts/deploy.sh
# Windows:
.\scripts\deploy.ps1
```

Configurable via environment variables (bash) or parameters (PowerShell):

| Setting | Default | |
| --- | --- | --- |
| `LOCATION` | `westus3` | Azure region |
| `RESOURCE_GROUP` | `rg-order-json-poc-<location>` | |
| `SQL_SKU` | `GP_S_Gen5_2` | any Azure SQL vCore SKU |
| `SQL_MAX_CAPACITY` / `SQL_MIN_CAPACITY` | `4` / `0.5` | serverless autoscale range |
| `COSMOS_MAX_RU` | `4000` | autoscale ceiling |
| `FABRIC_SKU` | `F2` | smallest practical |
| `FABRIC_CAPACITY` / `FABRIC_WORKSPACE` | `fabordjsonpoc915d` / `ws-order-json-poc` | |
| `DEPLOY_VMS` | `true` | set `false` to skip the benchmark VMs |
| `SKIP_FABRIC` | `false` | set `true` for Azure-only |

Individual steps, if you need them separately:

```bash
# Azure only
az deployment sub create --name orderjsonpoc --location westus3 \
  --template-file infra/bicep/main.bicep --parameters infra/bicep/main.parameters.json \
  --parameters adminPrincipalObjectId=$(az ad signed-in-user show --query id -o tsv) \
               adminPrincipalName=$(az ad signed-in-user show --query userPrincipalName -o tsv) \
               sshPublicKey="$(cat ~/.ssh/orderjsonpoc.pub)"

# SQL schema + grant the API identity (run from inside the VNet)
python tools/sql_bootstrap.py --server <sql-fqdn> --database OrderDb \
    --grant-identity vm-orderjsonpoc-api

# Fabric
python fabric/provision_fabric.py --capacity fabordjsonpoc915d --workspace ws-order-json-poc
python fabric/setup_mirroring.py --open-mirroring
python fabric/provision_fabric.py --show          # inspect what exists
```

## 5. Ingest (Phases 6–7)

```bash
export SQL_SERVER=<sql-fqdn> SQL_DATABASE=OrderDb
export COSMOS_ENDPOINT=https://<account>.documents.azure.com:443/
export ARCHIVE_BACKEND=adls ARCHIVE_ACCOUNT_NAME=<storage> ARCHIVE_FILESYSTEM=raw

python -m ingestion.run_ingest --backend both --orders 500 --seed 42 --workers 12
```

Writes a per-run JSON summary to `results/ingestion/`. Re-running with the same
seed upserts.

## 6. Serve the API

```bash
STORAGE_BACKEND=sql uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --workers 8

# On the POC VMs, as a systemd service:
bash scripts/vm_api.sh start sql
bash scripts/vm_api.sh start cosmos
bash scripts/vm_api.sh status
bash scripts/vm_api.sh logs 100
bash scripts/vm_api.sh stop
```

Verify:

```bash
python tools/smoke_test.py --base-url http://<api-host>:8000 --json-out artifacts/smoke.json
```

## 7. Contract and integration tests (Phase 8)

```bash
# Requires SQL_SERVER and COSMOS_ENDPOINT; run from inside the VNet
python -m pytest tests/contract -m contract -v
python -m pytest tests/integration -m integration -v -s
```

The contract suite ingests one order per size profile into **both** backends and
compares every endpoint's response.

## 8. Benchmarks (Phases 11–12)

```bash
# Full sweep for one backend
bash scripts/run_benchmarks.sh <api-private-ip> sql    60 15
bash scripts/run_benchmarks.sh <api-private-ip> cosmos 60 15

# A single point
python loadtests/operational/run_load.py \
  --base-url http://<api-host>:8000 --backend sql \
  --workload mix --rps 50 --duration 60 --warmup 15 --repeats 3

# Latency vs payload size, isolated to the large tail
python loadtests/operational/run_load.py --base-url … --backend sql \
  --workload full --rps 25 --size-min 3000000

# Writes
python loadtests/operational/run_write_bench.py --backend sql    --rates 1,5,10 --duration 30
python loadtests/operational/run_write_bench.py --backend cosmos --rates 1,5,10 --duration 30

# Generate the reports (never hand-type a number)
python tools/summarize_benchmarks.py
```

Workload shapes: `summary`, `title`, `cdf`, `block`, `full`, `search`, `mix`.

## 9. Fabric analytics (Phases 13–15)

```bash
# Land both operational stores in OneLake
python -m ingestion.fabric.push_to_onelake --backend both --mode full
python -m ingestion.fabric.push_to_onelake --backend sql  --mode incremental

# Build the star schema and run the analytics queries
python tools/run_analytics.py --build --query

# Measure operational-write-to-Fabric-visible latency
python tools/run_analytics.py --freshness --backend both --poll-seconds 5 --timeout 900

# Reconcile
python tools/reconciliation.py --all
```

## 10. Models and reports (Phase 17)

```bash
python tools/retention_model.py                                  # docs/RETENTION_ANALYSIS.md
python tools/retention_model.py --orders-per-day 2000 --avg-versions 12
python tools/cost_model.py --refresh-prices --region westus3     # docs/COST_ANALYSIS.md
python tools/summarize_benchmarks.py                             # results/BENCHMARK_SUMMARY.md
```

`cost_model.py --refresh-prices` fetches live rates from the Azure Retail Prices
API and caches them to `artifacts/pricing-snapshot.json` with the retrieval date,
region, meter name and SKU. No price is hard-coded.

## 11. Secret scan before sharing

```bash
python tools/secret_scan.py     # gates on both credentials AND sample-value leaks
python tools/check_docs.py      # every relative link resolves; Mermaid parses
```

`secret_scan.py` does two things. The first is the usual credential pattern
scan. The second matters more: it extracts distinctive values from the **real
customer sample** — GUIDs, emails, phone numbers, money amounts, long free text —
and proves none of them appear in any tracked file. That verifies the "only
synthetic data is committed" claim against the source data instead of trusting
`.gitignore`.

Last run: **0 of 1,194 GUIDs, 0 emails, 0 phone numbers, 0 money amounts and
0 long free-text values** from the sample found in 116 readable tracked files.

CI-friendly additions: `gitleaks detect`, `trufflehog filesystem .`.

## 12. Clean up (destroy)

```bash
bash scripts/destroy.sh                # interactive; refuses untagged groups
bash scripts/destroy.sh --yes
KEEP_FABRIC_CAPACITY=true bash scripts/destroy.sh --yes   # suspend instead of delete
```

The destroy scripts **refuse to act on a resource group that is not tagged**
`project=order-json-sql-vs-cosmos`. That guard exists specifically so the script
can never delete unrelated resources.

Not deleted automatically: the GitHub repository, and local `data/` and
`results/` directories.

### Cost control while the POC is idle

```bash
# Fabric capacity is the largest idle cost
az fabric capacity suspend -g <rg> --capacity-name fabordjsonpoc915d
az fabric capacity resume  -g <rg> --capacity-name fabordjsonpoc915d

# Benchmark VMs
az vm deallocate -g <rg> -n vm-orderjsonpoc-api
az vm deallocate -g <rg> -n vm-orderjsonpoc-load

# Azure SQL serverless auto-pauses after 120 minutes idle (configured in Bicep)
```

---

## Operational notes

### Driving the VMs without SSH

If outbound SSH is blocked (it was in the environment this POC was built in),
every VM operation works through the Azure VM agent:

```bash
az vm run-command invoke -g <rg> -n vm-orderjsonpoc-api \
  --command-id RunShellScript --scripts "$(cat scripts/vm_bootstrap.sh)" \
  --query "value[0].message" -o tsv
```

Two gotchas that cost time:

- **`export HOME=/root` first.** The run-command shell has no `HOME`, and `git`
  fails with `fatal: $HOME not set`.
- **`git config --global --add safe.directory /opt/poc`**, because the checkout
  is owned by `pocadmin` but the run-command executes as root.

Only one run-command can execute per VM at a time; a second call returns
`Conflict` until the first finishes.

### Getting code onto the VMs

The VMs `git clone` from the public repository and `git reset --hard origin/main`
to update — which is why nothing sensitive may ever be committed.

```bash
az vm run-command invoke -g <rg> -n <vm> --command-id RunShellScript \
  --scripts "export HOME=/root; cd /opt/poc && git fetch -q origin && \
             git reset -q --hard origin/main && echo SYNC_OK \$(git rev-parse --short HEAD)"
```

### If native Fabric mirroring is wanted over private endpoints

```bash
# 1. Create a Fabric managed private endpoint (works on F2)
#    see fabric/setup_mirroring.py, managedPrivateEndpoints API
# 2. Approve the pending connection on the Azure resource
az network private-endpoint-connection approve \
  --id "$(az sql server show -g <rg> -n <server> --query id -o tsv)/privateEndpointConnections/<name>" \
  --description "Approved for Fabric mirroring"
```

### Retrieving results off the VMs

Benchmark output is written on the VM, and the VMs were observed being
deallocated mid-session by something outside this POC
([CURRENT_STATE_CONTEXT.md](CURRENT_STATE_CONTEXT.md) section 3.3). **Pull results
into the repository as soon as a run finishes**, not at the end of a session.

```bash
python scripts/fetch_vm_results.py vm-orderjsonpoc-load
python scripts/fetch_vm_results.py vm-orderjsonpoc-api --remote results
python scripts/fetch_vm_results.py vm-orderjsonpoc-load --remote results/fabric
```

The fetcher works without SSH: it tars the directory on the VM and returns it as
base64 chunks through `az vm run-command`. Two constraints are baked in because
both were hit:

- **Chunks must stay under ~4,000 characters.** `az vm run-command` truncates
  stdout at 4,096 chars and returns the **tail** when it truncates, so an
  oversized chunk corrupts the stream silently rather than failing. The fetcher
  verifies each chunk's length and aborts if it is short.
- **Transient resets and VM deallocation are retried.** A 150 KB payload takes
  ~40 calls; an occasional failure is expected.

If a VM has been deallocated, the disks are intact — just restart and re-fetch:

```bash
az vm start -g <rg> -n vm-orderjsonpoc-api
az vm start -g <rg> -n vm-orderjsonpoc-load
```

### Common failures

| Symptom | Cause | Fix |
| --- | --- | --- |
| `DenyPublicEndpointEnabled` on deploy | tenant policy forces private-only | expected; the Bicep already guards firewall-rule creation behind `allowPublicFirewallRules` |
| `publicNetworkAccess` reverts to `Disabled` | same policy, Modify effect | use the private endpoints; do not fight the policy |
| `Login failed for user '<token-identity>'` | the identity has no contained DB user | re-run `tools/sql_bootstrap.py --grant-identity <name>` |
| Cosmos `Forbidden` on data plane | missing data-plane RBAC (ARM RBAC is not enough) | `az cosmosdb sql role assignment create --role-definition-id 00000000-0000-0000-0000-000000000002` |
| `Violation of PRIMARY KEY` during ingestion | duplicate GUIDs within one source order | already handled — `_unique_id` in `relational_extract.py` falls back to a surrogate |
| Fabric capacity not visible to the API | propagation delay or the caller is not a capacity admin | wait a minute, then `python fabric/provision_fabric.py --show` |
| `HY104 Invalid precision value (0)` writing a JSON block | a raised `conn.maxwrite` forces direct binding of long `nvarchar(max)` parameters | already fixed — do not set `conn.maxwrite`; let pyodbc use data-at-execution |
| Incremental push reports success but nothing appears in Fabric | the replicator deletes consumed files, so a listing-derived sequence resets to 1 and Fabric ignores it | already fixed — the highest sequence is persisted in `artifacts/fabric-push-watermarks.json` |
| `az vm run-command` returns `Conflict` | only one run-command may execute per VM at a time | wait for the current one; the helper scripts retry |
| VM shows `VM deallocated` mid-run | external cost governance (cause not identified) | `az vm start`, then re-fetch results; disks are intact |
| `Unable to complete the action because this Fabric capacity is currently not active` | the capacity was paused (by governance, or by `destroy.sh --KeepFabricCapacity`) | `az fabric capacity resume -g <rg> --capacity-name fabordjsonpoc915d` |
| Server-side metrics show operations from a previous run | each uvicorn worker has its own in-process metrics ring | set `API_WORKERS=1` when the server-side split is the object of study |
