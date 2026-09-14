# Azure SQL hybrid vs Azure Cosmos DB for large order JSON

A measured proof of concept comparing two operational data-serving architectures
for a title/escrow order workload whose source documents are large, deeply
nested JSON (~0.5–5 MB), read-heavy, at ~50 requests/sec, retained ~2 years, and
destined for Microsoft Fabric analytics.

**Both paths expose the same API, hold the same data, run the same tests, and
feed the same Fabric analytics model.** Neither was assumed to win.

```
            SOURCE ORDER JSON  (extract envelope, 0.5–5 MB)
                        │
              ┌─────────┴─────────┐
              ▼                   ▼
      raw archive          business-block decomposition
      (ADLS Gen2)          (shared, lossless, one code path)
                                  │
                    ┌─────────────┴─────────────┐
                    ▼                           ▼
          PATH A: AZURE SQL              PATH B: COSMOS DB
        hybrid relational + JSON        semantic aggregate items
                    └─────────────┬─────────────┘
                                  ▼
                            SAME REST API   ~50 reads/sec
                                  │
                    ┌─────────────┴─────────────┐
                    ▼                           ▼
            operational use            MICROSOFT FABRIC
                                     OneLake → Lakehouse →
                                     Warehouse → Power BI
```

---

## What is here

| | |
| --- | --- |
| **Start here** | [docs/DECISION_MATRIX.md](docs/DECISION_MATRIX.md) — the evidence-based comparison and the answers to the customer's 20 questions |
| Measured results | [results/BENCHMARK_SUMMARY.md](results/BENCHMARK_SUMMARY.md) (generated, never hand-typed) |
| Architecture | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Source data profile | [docs/DATA_PROFILE.md](docs/DATA_PROFILE.md) |
| PATH A design | [docs/SQL_DESIGN.md](docs/SQL_DESIGN.md) |
| PATH B design | [docs/COSMOS_DESIGN.md](docs/COSMOS_DESIGN.md) |
| API contract | [docs/API_DESIGN.md](docs/API_DESIGN.md) |
| Fabric analytics | [docs/FABRIC_ANALYTICS.md](docs/FABRIC_ANALYTICS.md) |
| Fabric direct-serving control | [docs/FABRIC_DIRECT_SERVING_CONTROL.md](docs/FABRIC_DIRECT_SERVING_CONTROL.md) |
| Two-year retention | [docs/RETENTION_ANALYSIS.md](docs/RETENTION_ANALYSIS.md) |
| Cost | [docs/COST_ANALYSIS.md](docs/COST_ANALYSIS.md) |
| Benchmark method + limitations | [docs/BENCHMARK_METHOD.md](docs/BENCHMARK_METHOD.md) |
| Verified Microsoft docs | [docs/SOURCES.md](docs/SOURCES.md) |
| Environment constraints | [docs/CURRENT_STATE_CONTEXT.md](docs/CURRENT_STATE_CONTEXT.md) |
| Operations | [docs/RUNBOOK.md](docs/RUNBOOK.md) |
| Diagrams | [diagrams/](diagrams/) (Mermaid) |

---

## The idea in one paragraph

The source order is not one indivisible thing. Profiling the real sample shows
**106 top-level sections, of which the top 10 hold 87.9% of the bytes and 76 are
under 1 KiB**, with 33% of all scalars being empty strings. So the order is
decomposed **once**, along business boundaries, by
[`ingestion/parser/block_splitter.py`](ingestion/parser/block_splitter.py) — and
each engine stores those blocks in its own idiom: rows in `OrderJsonBlocks` for
SQL, items in a container for Cosmos. The split is asserted lossless on every
size profile and on the real sample. Because both paths decompose identically,
the benchmark compares *storage engines*, not two different data models.

## Headline findings

> Numbers below are **MEASURED** on the deployed environment. See
> [results/BENCHMARK_SUMMARY.md](results/BENCHMARK_SUMMARY.md) for the full set
> and [docs/BENCHMARK_METHOD.md](docs/BENCHMARK_METHOD.md) for how.

- **A 5 MB order decomposes to a largest block of 760 KB** — 36% of the Cosmos
  2 MB item limit. Array-element chunking never engaged on real data. The
  item-size limit constrains the *document model*, not the viability of Cosmos.
- **The same 760 KB figure clears the 1 MB LOB ceiling that Fabric mirroring
  imposes** on `nvarchar(max)`. Business-boundary splitting satisfied two
  unrelated platform constraints at once, by accident of being the right shape.
- **Both backends sustain the stated 50 RPS**, including the full multi-megabyte
  workload, with zero errors.
- **A single-level `/customerId` partition key would exceed the 20 GiB logical
  partition limit by ~11x** within the two-year horizon. The hierarchical
  `/customerId` + `/orderId` key holds each logical partition at **0.0061%** of
  the limit, permanently.
- **The contract tests caught two real defects** that only appear when two
  implementations are compared: GUID casing and floating-point money
  aggregation.

## Repository layout

```
app/            API + repository abstraction + telemetry
  api/          the SAME endpoints for every backend
  repositories/ base.py · sql_repository.py · cosmos_repository.py · fabric_repository.py
ingestion/      parser (block splitter, relational extract) · archive · fabric push
generator/      deterministic synthetic order generator + size profiles
sql/            schema · queries (incl. the design-control demonstrator)
cosmos/         modeling/size_guard.py
fabric/         provisioning · mirroring · warehouse star schema + analytics queries
infra/bicep/    subscription-scope IaC: shared · sql · cosmos · network · compute · fabric
loadtests/      open-model read harness · write benchmark
tools/          profiler · retention model · cost model · summarizer · reconciliation
tests/          unit · integration · contract
docs/  diagrams/  artifacts/  results/
```

---

## Quick start

### Prerequisites

- Python 3.12+, Azure CLI (`az login`), ODBC Driver 18 for SQL Server
- An Azure subscription where you can create resource groups
- A Fabric-enabled tenant (capacity is created by the deploy script)

### 1. Profile the source and generate data (no Azure needed)

```bash
python -m pip install -r requirements.txt

# Profile the customer sample (structure only; no values are emitted)
python tools/profile_source.py --input data/source/150159_Order.MASKED.json.json

# Calibrate and generate synthetic orders, 500 KB → 5 MB
python generator/synthetic_order_generator.py --calibrate
python generator/synthetic_order_generator.py --orders 100 --seed 42

# The whole decomposition/guard/model layer runs offline
python -m pytest tests/unit -q
```

### 2. Deploy Azure + Fabric

```bash
bash scripts/deploy.sh
# or:  .\scripts\deploy.ps1
#
# Everything is configurable:
LOCATION=eastus2 SQL_SKU=GP_Gen5_4 COSMOS_MAX_RU=10000 FABRIC_SKU=F4 bash scripts/deploy.sh
```

This creates the resource group, ADLS Gen2 archive, Azure SQL (Entra-only auth),
Cosmos DB (hierarchical partition key), the private-endpoint network, two
benchmark VMs, the SQL schema, a Fabric F2 capacity, workspace, lakehouse,
warehouse and the Open Mirroring landing zones.

### 3. Ingest, serve, benchmark

```bash
# Load the same 500 orders into both backends (from inside the VNet)
python -m ingestion.run_ingest --backend both --orders 500 --seed 42 --workers 12

# Serve
bash scripts/vm_api.sh start sql      # or: start cosmos
python tools/smoke_test.py

# Full sweep: 10/25/50/100/200 RPS, all workload shapes
bash scripts/run_benchmarks.sh <api-host> sql
bash scripts/run_benchmarks.sh <api-host> cosmos

# Writes
python loadtests/operational/run_write_bench.py --backend sql    --rates 1,5,10
python loadtests/operational/run_write_bench.py --backend cosmos --rates 1,5,10

# Reports, generated from the machine-readable runs
python tools/summarize_benchmarks.py
```

### 4. Analytics

```bash
python -m ingestion.fabric.push_to_onelake --backend both --mode full
python tools/run_analytics.py --build --query
python tools/run_analytics.py --freshness --backend both
python tools/reconciliation.py --all
```

### 5. Models and reports

```bash
python tools/retention_model.py                  # docs/RETENTION_ANALYSIS.md
python tools/cost_model.py --refresh-prices      # docs/COST_ANALYSIS.md (live Azure prices)
```

### 6. Clean up

```bash
bash scripts/destroy.sh          # prompts; refuses untagged resource groups
bash scripts/destroy.sh --yes
KEEP_FABRIC_CAPACITY=true bash scripts/destroy.sh --yes   # pause instead of delete
```

Full command reference: [docs/RUNBOOK.md](docs/RUNBOOK.md).

---

## Data handling

The customer sample is **customer-derived data even though it is masked**. It is
git-ignored ([`.gitignore`](.gitignore)) and is never committed, never echoed
into logs, tests, documentation examples or benchmark artifacts.

- Committed datasets are **100% synthetic**, generated deterministically from a
  seed. Names, companies, addresses, GUIDs, amounts and dates are invented.
- The profiler emits **structure only** — every string is reduced to a byte
  length, every GUID to an occurrence count.
- Telemetry records payload **sizes**, never payload **contents**.

## Security

- **No secrets in this repository.** [`.env.example`](.env.example) contains
  placeholders only; real `.env` files are git-ignored.
- All Azure access uses **Entra ID** via `DefaultAzureCredential` / managed
  identity. There is no password, connection-string secret, or account key
  anywhere in the code or on the VMs.
- Azure SQL is **Entra-only authentication**; the API's identity is a contained
  user with `db_datareader` + `db_datawriter`, not an admin.
- Cosmos uses **data-plane RBAC** (Cosmos DB Built-in Data Contributor).

## Licence and status

Proof of concept. Not production code — see the "Known limitations" section of
[docs/BENCHMARK_METHOD.md](docs/BENCHMARK_METHOD.md) and the "What evidence is
still required" section of [docs/DECISION_MATRIX.md](docs/DECISION_MATRIX.md).
