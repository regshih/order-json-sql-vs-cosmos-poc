# Decision matrix — Azure SQL hybrid vs Azure Cosmos DB

Evidence-based comparison. Every performance number here is **MEASURED** on the
deployed environment and traceable to a file in
[`results/`](../results/BENCHMARK_SUMMARY.md); every price is **VERIFIED** live
from the Azure Retail Prices API; every volume is **MODELLED** from measured
payload sizes and clearly-labelled assumed rates.

Method and limitations: [BENCHMARK_METHOD.md](BENCHMARK_METHOD.md).

---

## 0. The short version

**Both architectures meet the customer's stated 50 reads/sec, on every workload
shape, with zero errors** — including whole multi-megabyte orders.

They are not equivalent, and the difference is not where it is usually assumed
to be. It is not the Cosmos item-size limit, and it is not JSON handling. It is
**cost shape**:

| At the 50 RPS operating point | Azure SQL | Cosmos DB |
| --- | --- | --- |
| Realistic mix, p50 / p95 | **9.0 / 38.7 ms** | 39.5 / 88.8 ms |
| Full multi-MB order, p50 / p95 | **37.5 / 110.7 ms** | 110.4 / 194.3 ms |
| Errors across the whole sweep | 0 | 0 |
| Compute needed to achieve it | **2 vCore** General Purpose serverless | **40,000 RU/s** autoscale ceiling |
| Sustained throughput at 50 RPS full-order | 67.4 MB/s | 69.6 MB/s |

Cosmos required roughly **10x the provisioned capacity cost** to reach parity on
throughput, and still ran 2–3x higher latency. The cause is measurable and
specific: a whole-order read costs **1,149 RU** because it returns ~1 MB across
~32 items, whereas the same read is a single indexed query plus a LOB fetch in
SQL.

**That is a statement about this workload, not about Cosmos.** The same
measurements show Cosmos matching or beating SQL on the operations it is built
for, and scaling linearly to 200 RPS where SQL's 2-vCore instance was already
near its ceiling.

---

## 1. Comparison table

Legend: **MEASURED** · *MODELLED* · ~ASSUMED~ · (qualitative judgement)

| Dimension | Azure SQL hybrid | Cosmos DB aggregate | Evidence |
| --- | --- | --- | --- |
| **Handling 1–5 MB logical orders** | Works unchanged. Largest JSON block 760 KB in `nvarchar(max)`; no row-size pressure | Works, but **only** with semantic decomposition. A 2.007 MiB monolithic item is rejected **HTTP 413** | MEASURED — [negative test](../results/cosmos/negative-test.json) |
| **50 RPS operational performance** | **Met.** mix p50 9.0 / p95 38.7 ms | **Met.** mix p50 39.5 / p95 88.8 ms | MEASURED |
| **Point reads** | Summary p50 **8.2 ms** (one indexed row) | Summary p50 33.9 ms. The read itself is 1.04 RU / 3.8 ms; the rest is the tenant-lookup tax (§4) | MEASURED |
| **Search / filter** | p50 **6.9 ms**. Arbitrary predicates, joins, aggregates, ranges, `ORDER BY` | p50 30.1 ms, 8.0 RU. Only pre-projected `search.*` fields are filterable; new predicates need an index policy change and a backfill | MEASURED |
| **JSON flexibility** | JSON blocks are opaque to the engine but queryable with `JSON_VALUE`/`OPENJSON` when needed | Native JSON throughout; any path is addressable without a schema change | (qualitative) |
| **Schema evolution** | Unrecognised sections fall into `MISC/MAIN` and round-trip losslessly; no migration | No schema at all; new fields need no DDL. **But** hierarchical partition keys can never be changed after container creation | MEASURED (lossless round-trip) + docs |
| **Partition scalability** | No partitioning concern at this volume. Hyperscale is the growth path | **Solved by design.** `/customerId`+`/orderId` puts one order per logical partition = **0.0061%** of the 20 GiB limit, permanently. A single-level `/customerId` key would hit **1,119% of the limit** in 2 years | *MODELLED* from MEASURED sizes |
| **Two-year retention** | *441 GiB* hot (medium scenario), independent of engine | *441 GiB* hot, same. Archive is *1.89 TiB* gzipped for both | *MODELLED* |
| **Operational complexity** | Familiar. Indexes, DMVs, query plans, one connection pool. Entra-only auth, contained DB user | Fewer knobs but less introspection. RU is the only lever and it must be sized from measurement, not intuition | (qualitative) |
| **Development complexity** | Ingestion writes 8 tables + ~34 block rows in one transaction. ~34 `json.loads` per full read | Ingestion upserts ~35 items with deterministic ids — idempotent by construction. SDK returns parsed objects, so reassembly is a dict merge | MEASURED (see reconstruction, §3) |
| **Indexing** | 14 nonclustered indexes, each tied to a named API predicate. `JsonPayload` deliberately never included | `/data/*` excluded; only routing + `search.*` indexed. Composite indexes for the two documented search shapes | MEASURED — [index impact](../results/cosmos/index-impact.json) |
| **Observability** | `sys.dm_db_resource_stats` gives CPU/IO/log/worker/session %. Query plans available | RU per request is exact and per-operation, which is *better* than SQL for cost attribution. No plan visibility | MEASURED both |
| **Cost predictability** | Flat vCore + storage. Cost does **not** move with request shape | Cost is a direct function of request shape and payload size. A whole-order endpoint costs **269x** the RU of a summary endpoint | MEASURED + VERIFIED prices |
| **Ingestion / write cost** | 500 orders in 333 s (1.5/s, 12 workers). Header update p50 **6.4 ms** | 500 orders in 140 s (3.6/s) — **2.4x faster**. 1,024 RU per order version | MEASURED |
| **Full-order reconstruction** | db 13 ms + reconstruct 5.5 ms (~34 `json.loads`) | db 28 ms + reconstruct **0.1 ms** (SDK already parsed). Cost moved, not removed | MEASURED |
| **Analytics integration** | Open Mirroring → Delta. 8 tables, 199 MB Parquet, 104 s for 500 orders | Open Mirroring → Delta. 1 table, 534 KB Parquet, **1.2 s** — 370x less to move | MEASURED |
| **Data freshness into Fabric** | **107 s** end-to-end (write 81 ms, incremental push 2.8 s, Fabric merge + metadata sync 104 s) | Same mechanism, same order of magnitude | MEASURED |
| **Maintainability** | 10 tables is a schema a team can hold in its head. The design rule is one sentence | No schema to maintain; the risk moves to the *unwritten* contract in `search.*` and the irreversible partition key | (qualitative) |

---

## 2. Full measured results at 50 RPS

| Workload | SQL p50 | SQL p95 | SQL p99 | Cosmos p50 | Cosmos p95 | Cosmos p99 | Cosmos RU/req |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `summary` | **8.2** | **9.8** | **11.4** | 33.9 | 37.1 | 40.1 | 4.28 |
| `search` | **6.9** | **8.7** | **9.9** | 30.1 | 76.7 | 116.0 | 8.00 |
| `title` (~300 KB) | **10.9** | **19.2** | **27.2** | 56.4 | 77.5 | 105.5 | 339.41 |
| `cdf` (~240 KB) | **11.3** | **27.0** | **42.2** | 68.0 | 107.8 | 167.3 | 352.12 |
| `mix` | **9.0** | **38.7** | 113.5 | 39.5 | 88.8 | **123.0** | 185.74 |
| `full` (0.5–5 MB) | **37.5** | **110.7** | **169.3** | 110.4 | 194.3 | 251.3 | 1,151.94 |

All runs: **0 errors, 0 throttling** at the ceilings stated. Milliseconds.

### Scaling behaviour

| Target RPS | SQL mix p50 / p95 | Cosmos mix p50 / p95 |
| ---: | --- | --- |
| 10 | 9.0 / 38.7 † | 39.9 / 92.7 |
| 25 | — | 37.4 / 85.2 |
| 50 | 9.0 / 38.7 | 39.5 / 88.8 |
| 100 | 8.2 / 28.6 | 45.2 / 98.7 |
| 200 | 8.4 / 30.1 | 76.1 / 199.1 |

† SQL's low-rate runs are in [`results/sql/`](../results/sql/); latency is flat
from 10 to 200 RPS, which says the 2-vCore instance was **not** the constraint at
any tested rate.

**Neither system reached a practical limit before 200 RPS** once provisioned
correctly. That is worth stating plainly: the 50 RPS requirement is not
demanding for either engine.

---

## 3. The capacity finding — why Cosmos needed 10x the provisioning

This is the most decision-relevant measurement in the POC, and it was found by
first getting it wrong.

The Cosmos sweep was initially run at a **4,000 RU/s** autoscale ceiling. Every
payload-returning workload collapsed: `mix` at a 50 RPS target achieved 23 RPS
with a **34-second** p50; `full` at 10 RPS achieved 3.9 RPS. The container sat at
**100% normalized RU consumption** with **100k+ 429s per 15 minutes**.

Per-operation RU was then measured directly, and it predicts the collapse to
within ~10%:

| Workload | Measured RU/req | RU/s at 50 RPS | Predicted max RPS at 4,000 | Observed |
| --- | ---: | ---: | ---: | ---: |
| `summary` | 4.28 | 214 | 934.6 | 50.0 ✓ |
| `search` | 8.00 | 400 | 500.0 | 50.0 ✓ |
| `mix` | 185.74 | **9,287** | 21.5 | 23.0 |
| `title` | 339.41 | **16,970** | 11.8 | 13.1 |
| `cdf` | 352.12 | **17,606** | 11.4 | 12.7 |
| `full` | 1,151.94 | **57,597** | 3.5 | 3.9 |

Both result sets are kept: [`results/cosmos/`](../results/cosmos/) at 40,000 RU/s
and [`results/cosmos/ceiling-4000ru/`](../results/cosmos/ceiling-4000ru/) at
4,000. The pair *is* the finding — "Cosmos needs ~10–58k RU/s for this workload"
is only visible by measuring both sides of the constraint.

### Why a whole-order read costs 1,149 RU

Cosmos charges queries roughly per KB returned. A full order is ~1 MB spread
across ~32 items, so the query is charged for all of it. Measured directly, for
orders that fit inside one item:

| | RU | ms |
| --- | ---: | ---: |
| Monolithic order, **point read** | **145.9** | **20.1** |
| Aggregate order, full read (32 items) | 1,187.5 | 183.4 |
| Ratio | **8.1x** | 9.1x |

Decomposition is **mandatory** above 2 MB and costs **~8x the RU** below it.
That is the real price of the item-size limit — not a failure, a tax.

Writes go the other way: aggregate 506.8 RU vs monolithic 627.7 RU (**0.8x**),
because only changed items are rewritten.

---

## 4. The lookup tax — an API-contract defect, not a database one

The required contract is `GET /orders/{orderId}` with **no tenant in the route**,
but the partition key starts with `/customerId`. Every Cosmos read therefore
begins with a cross-partition lookup:

| | RU | ms |
| --- | ---: | ---: |
| Cross-partition lookup | 3.24 | **9.8** |
| Point read with full PK | 1.04 | **3.8** |

The lookup is **2.6x the latency and 3x the RU of the read it precedes**. A
Cosmos summary read is 33.9 ms p50 against SQL's 8.2 ms, and this is most of the
gap.

**The fix is free and belongs in the API**: put `customerId` in the route or take
it from the caller's token. This POC keeps the tax because the brief specifies
that contract for both backends, but any production comparison should subtract
it. It is measured separately so it can be.

---

## 5. Cost

Rates VERIFIED from the Azure Retail Prices API, `westus3`, USD, 730 h/month.
Full derivation: [COST_ANALYSIS.md](COST_ANALYSIS.md).

| Configuration actually used | Rate | $/month |
| --- | --- | ---: |
| Azure SQL GP serverless, 2 vCore (always active) | $0.521758 /vCore/h | **$761.77** |
| Azure SQL GP **provisioned**, 2 vCore (equivalent, cheaper) | $0.152217 /vCore/h | **$222.24** |
| Cosmos autoscale at 40,000 RU/s sustained | $0.012 /100 RU/s/h | **$3,504.00** |
| Cosmos autoscale idle floor (10% = 4,000 RU/s) | $0.012 /100 RU/s/h | **$350.40** |
| Cosmos storage, 441 GB | $0.25 /GB/mo | **$110.25** |
| Azure SQL storage, 441 GB | $0.115 /GB/mo | **$50.72** |
| ADLS archive, 1,935 GB hot | $0.0169 /GB/mo | **$32.70** |
| ADLS archive, 1,935 GB cool | $0.01 /GB/mo | **$19.35** |
| Fabric F2 (24×7) | $0.18 /CU/h | **$262.80** |

**Cosmos costs ~4.5–16x more to serve the same 50 RPS**, depending on whether you
compare against SQL serverless or provisioned. Fabric is identical either way.

The cheapest lever in the whole design is unrelated to this choice: moving the
archive to Cool tier saves more than the difference between many SQL SKUs.

---

## 6. When each is preferable

### SQL is preferable when…

- **The query surface is not fully known.** Arbitrary predicates, joins,
  aggregates and ranges are free; in Cosmos each new filterable field is an
  indexing-policy change plus a backfill.
- **Cost predictability matters.** SQL cost is flat; Cosmos cost is a direct
  function of payload size and request mix, so an endpoint change can multiply
  the bill. Measured spread: 4.28 RU to 1,152 RU per request — **269x**.
- **Payload-heavy reads are common.** This is the decisive one for the stated
  workload: it is read-oriented over 1–5 MB documents, which is exactly what RU
  pricing charges most for.
- **The team is a SQL team.** Indexes, plans and DMVs are a known toolkit; RU
  capacity planning is a new discipline that must be driven by measurement.
- **Analytics needs payload-level facts.** `FactOrderCharge` shreds CDF lines
  straight out of the mirrored `nvarchar(max)` blocks with `OPENJSON`; the Cosmos
  mirror carries the payload as a string that must be re-parsed.

### Cosmos is preferable when…

- **Elastic scale beyond one machine is required.** Cosmos scaled to 200 RPS by
  raising a number; SQL would eventually need Hyperscale or read replicas
  (General Purpose has **no read scale-out** — see [SOURCES.md](SOURCES.md)).
- **Reads are small and keyed.** Summary and search hit 50 RPS on **214–400
  RU/s**, i.e. near the 400 RU/s minimum. If the API served only summaries and
  searches, Cosmos would cost ~$35/month.
- **Tenant isolation and predictable partitioning matter.** The hierarchical key
  makes partition growth structurally impossible to get wrong — and it directly
  fixes the customer's *stated current problem*.
- **Write throughput matters.** Ingestion was **2.4x faster**, and per-item
  upserts with deterministic ids are idempotent without transactions.
- **Global distribution is on the roadmap.** Multi-region writes are a
  configuration change, not a re-architecture.

### Remaining questions include…

1. **What is the real update/version rate?** The single biggest unknown. The
   sample was at version 59; the scenarios in
   [RETENTION_ANALYSIS.md](RETENTION_ANALYSIS.md) differ by **6x** in archive
   volume. Nothing should be procured before this is known.
2. **What is the real request mix?** Cosmos cost swings 269x between a
   summary-only and a full-order-only API. This single number decides the cost
   comparison.
3. **Will the API carry tenant scope?** Removing the lookup tax closes most of
   the Cosmos latency gap at zero cost.
4. **What happens when the working set exceeds memory?** The POC dataset is
   ~660 MB. SQL results in particular will change at 441 GiB; Cosmos is far less
   sensitive to this.
5. **Is Hyperscale needed?** It is the SQL growth path for this volume, at
   $0.18266/vCore/h, and it was not tested.
6. **What SLA does the business actually need?** No latency SLA was invented
   here. Both engines are comfortably inside anything a human-facing title/escrow
   workflow would require; the answer may change for machine-to-machine use.

---

## 7. Does the Fabric requirement change the recommendation?

**No.** This was a genuine risk going in and it is now settled by measurement:

- Both paths land in OneLake as Delta and populate the **same** star schema.
- The SQL path's `nvarchar(max)` blocks arrived **byte-intact** — max 759,857
  chars, 0 rows ≥ 1 MiB, 0 invalid JSON, 0 length mismatches — because
  business-boundary splitting keeps every block under Fabric's 1 MB LOB ceiling.
- Freshness is the same order of magnitude for both (**107 s** measured for SQL),
  and is dominated by the Fabric replicator and extractor cadence, not by the
  source engine.
- Fabric capacity cost is identical either way.

**One Fabric-specific caveat does affect the SQL design**: a table containing a
native `json`-typed column **cannot be mirrored today**. That is why this POC
uses `nvarchar(max)` + `ISJSON` rather than the GA `json` type — adopting the
modern type would forfeit the simplest analytics integration. See
[SQL_DESIGN.md](SQL_DESIGN.md) §3.1.

---

## 8. Which architecture currently appears strongest

**Azure SQL Database with the hybrid relational + JSON model**, for this
workload as described.

The reasoning, in order of weight:

1. **It meets the requirement at ~4.5–16x lower cost**, on 2 vCores against
   40,000 RU/s.
2. **The workload's defining characteristic — read-heavy over 1–5 MB documents —
   is precisely what RU pricing penalises.** 1,149 RU per whole-order read is not
   a tuning problem; it is what returning a megabyte costs in Cosmos.
3. **Latency is 2–4x lower at every tested rate**, with more headroom on smaller
   compute.
4. **The search surface is open.** A title/escrow operational API accumulates
   query patterns; SQL absorbs them without a data migration.
5. **Fabric integration is proven and lossless**, including payload-level facts.

**This recommendation is conditional, and the conditions are the point:**

- If the API is changed to serve **business blocks rather than whole orders**,
  Cosmos's cost disadvantage shrinks by ~3.4x (336 RU vs 1,149 RU). If it served
  only summaries and searches, **Cosmos would be the cheaper option**.
- If **elastic scale past a single database** becomes a requirement, Cosmos's
  scaling story is simpler than Hyperscale plus read replicas.
- If the customer's real problem is the **partition growth** they reported, note
  that this POC shows a hierarchical key fixes it — Cosmos need not be abandoned
  to solve that.

## 9. Answer to "whole orders or business blocks?"

**Business blocks — and the evidence is unambiguous in both engines.**

| | SQL p50 | Cosmos p50 | Cosmos RU |
| --- | ---: | ---: | ---: |
| Whole order | 37.5 ms | 110.4 ms | 1,151.94 |
| TITLE block | 10.9 ms | 56.4 ms | 339.41 |
| CHECKLIST block | — | — | 49.79 |
| Summary | 8.2 ms | 33.9 ms | 4.28 |

A whole-order endpoint is **3.4x** the SQL latency and **3.4x** the Cosmos RU of
the largest single block, and **269x** the RU of a summary. The block-granular
endpoints already exist in the API; the recommendation is to make them the
default and treat `GET /orders/{id}` as an export path rather than an
operational one.

---

## 10. Evidence still required before a production recommendation

Stated plainly, because the measurements above do not cover it:

1. **Scale test at realistic volume.** 500 orders / 660 MB vs a modelled 441 GiB
   hot set. SQL buffer-pool behaviour and index maintenance are untested at
   volume.
2. **Sustained soak.** 60-second steady states, single repeat. No evidence on
   index fragmentation, statistics drift, serverless auto-pause, or Cosmos
   partition rebalancing over days.
3. **The real request mix and version rate**, from production telemetry.
4. **Failure and recovery behaviour.** No failover, no region loss, no restore
   drill, no poison-message handling in ingestion.
5. **Concurrent read+write.** Reads and writes were benchmarked separately; the
   interaction is untested.
6. **Security review at depth.** Tenant isolation is a design note, not an
   implementation — the API is unauthenticated inside the VNet.
7. **Cosmos minimum viable RU/s.** The ceiling was set to 40,000 to establish
   parity, not tuned down to the cheapest configuration that still meets 50 RPS.
8. **Hyperscale evaluation** as the SQL growth path.
