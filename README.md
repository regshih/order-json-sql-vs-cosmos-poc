# Storing and serving a complete order JSON on Azure

A measured proof of concept for a title/escrow order workload whose source
documents are large, deeply nested JSON (~0.5–5 MB), read-heavy, at ~50
requests/sec, retained ~2 years, and destined for Microsoft Fabric analytics.

**Every design exposes the same API, holds the same data, runs the same tests,
and feeds the same Fabric analytics model.** None was assumed to win.

The POC has two parts, because the question was sharpened partway through.

**Part 1 — can the API return the whole order?** Two *decomposed* designs, both of
which reassemble the order on read: Azure SQL hybrid (relational + JSON blocks)
and Azure Cosmos DB for NoSQL (semantic aggregate).

**Part 2 — can the DATABASE hold the whole order as one item?** Two
*full-document* designs that store one logical order per physical item: Azure SQL
Full JSON (one row) and Azure Cosmos DB for MongoDB (one BSON document).

| Design | `STORAGE_BACKEND` | Physical storage | One item? | API response |
| --- | --- | --- | :---: | --- |
| SQL Full JSON | `sql-full-json` | one row, `nvarchar(max)` | **yes** | full JSON |
| SQL Full JSON (native) | `sql-full-json-native` | one row, `json` type | **yes** | full JSON |
| Cosmos Mongo | `cosmos-mongo` | one BSON document | **yes** | full JSON |
| SQL hybrid | `sql-hybrid` | 8 tables + JSON blocks | no | reassembled |
| Cosmos NoSQL | `cosmos-nosql` | ~34 documents | no | reassembled |
| Fabric (control) | `fabric` | Delta | n/a | full JSON |

**Can Microsoft store the complete 1–5 MB order as ONE database item? Yes — in
two products**, measured: Azure SQL accepts one row to **16.70 MB**, Cosmos DB for
MongoDB one document to **15.04 MB**, and Cosmos DB for NoSQL refuses above
**2 MB** (HTTP 413), which is exactly why its design decomposes.
See [docs/DOCUMENT_SIZE_RESULTS.md](docs/DOCUMENT_SIZE_RESULTS.md).

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

> Every number below is **MEASURED** on the deployed environment. Full set:
> [results/BENCHMARK_SUMMARY.md](results/BENCHMARK_SUMMARY.md). Method and
> limitations: [docs/BENCHMARK_METHOD.md](docs/BENCHMARK_METHOD.md).

**Both architectures meet the stated 50 reads/sec on every workload shape, with
zero errors** - including whole multi-megabyte orders. They differ in cost shape,
not in capability.

| At 50 RPS | Azure SQL | Cosmos DB |
| --- | --- | --- |
| Realistic mix, p50 / p95 | **9.0 / 38.7 ms** | 39.5 / 88.8 ms |
| Full 0.5-5 MB order, p50 / p95 | **37.5 / 110.7 ms** | 110.4 / 194.3 ms |
| Throughput, full-order workload | 67.4 MB/s | 69.6 MB/s |
| Capacity required | **2 vCore** GP serverless | **40,000 RU/s** autoscale |
| Errors / throttling across the sweep | 0 / 0 | 0 / 0 |

- **A 5 MB order decomposes to a largest block of 760 KB** - 36% of the Cosmos
  2 MB item limit. Array chunking never engaged on real data.
- **A 2.007 MiB monolithic item is rejected with HTTP 413**, while the same order
  stored as semantic documents succeeds. The item limit constrains the *document
  model*, not the viability of Cosmos.
- **Decomposition costs ~8x RU**: a monolithic point read is 145.9 RU / 20.1 ms;
  the same order as 32 items is 1,187.5 RU / 183.4 ms. That is the real price of
  the 2 MB limit.
- **A whole-order read costs 269x the RU of a summary** (1,152 vs 4.28). Serving
  business blocks instead of whole orders is worth ~3.4x on Cosmos RU and ~3.4x
  on SQL latency.
- **Cosmos cost depends almost entirely on what the API serves.** At the same
  50 RPS: **$23/month** for a summary-only API, **$426** for the realistic mix,
  **$2,254** for a whole-order API. Azure SQL is **$222 regardless**. That
  asymmetry is the real decision axis.
- **RU measured three ways spanned a 4x range.** Splitting the container across
  more physical partitions cut query RU by **~75% with no latency change**, and
  concurrency then raises effective RU **1.7-2.1x** above an isolated
  single-request measurement. Point reads and writes were unaffected by either.
  **Isolated RU is not a safe basis for sizing** - it was wrong in both
  directions here.
- **Under-provisioned Cosmos fails hard, not gracefully.** At a 4,000 RU/s
  ceiling the mix collapsed to 23 RPS with a 34-second p50 and 100k+ 429s per
  15 minutes.
- **A single-level `/customerId` partition key would exceed the 20 GiB logical
  partition limit by ~11x** within the two-year horizon. The hierarchical
  `/customerId` + `/orderId` key holds each partition at **0.0061%** of the
  limit, permanently - which fixes the customer's stated current problem.
- **Both paths reach Fabric losslessly.** The SQL path's `nvarchar(max)` blocks
  arrived byte-intact (max 759,857 chars, **0 rows >= 1 MiB, 0 invalid JSON, 0
  length mismatches**), so the same business-boundary split that satisfies the
  Cosmos item limit also clears Fabric's 1 MB LOB truncation ceiling. Measured
  freshness: **106.9 s** (SQL) and **64.2 s** (Cosmos) end-to-end.
- **The contract tests caught two real defects** that only appear when two
  implementations are compared: GUID casing and floating-point money
  aggregation. A third - RU under-reporting by ~42x - was caught by measuring the
  same thing two ways.

- **Direct Fabric/Delta serving is not viable** for this API: **26x slower** than
  SQL on summary reads (218 ms vs 8.2 ms), **31-48% error rates** at 50 RPS,
  **107 s stale**, and read-only. Measured, not assumed - see
  [docs/FABRIC_DIRECT_SERVING_CONTROL.md](docs/FABRIC_DIRECT_SERVING_CONTROL.md).

**Current recommendation: Azure SQL with the hybrid model** - on latency (2-4x
lower), query flexibility, and cost *stability*, at ~1.9x the cost of Cosmos on
the realistic mix. The conditions that flip it are specific and two are cheap;
they are stated explicitly in
[docs/DECISION_MATRIX.md](docs/DECISION_MATRIX.md) section 8.

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
# Load the same 500 orders into every backend (from inside the VNet)
python -m ingestion.run_ingest --backend all --orders 500 --seed 42 --workers 12
# or one at a time:
python -m ingestion.run_ingest --backend sql-full-json --orders 500 --seed 42

# Serve (STORAGE_BACKEND is read once at startup, so the API restarts per design)
bash scripts/vm_api.sh start sql-full-json     # or cosmos-mongo, sql-hybrid, ...
python tools/smoke_test.py

# Physical storage limits: what each backend ACTUALLY does per size
python tools/document_size_probe.py            # local, no Azure needed
python tools/document_size_tests.py --backend all
python tools/summarize_document_sizes.py

# The full-document 50 RPS sweep (rate sweep + per-payload-band)
bash scripts/run_full_document_bench.sh

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
