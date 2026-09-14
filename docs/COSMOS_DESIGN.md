# PATH B — Azure Cosmos DB for NoSQL aggregate design

Repository: [`app/repositories/cosmos_repository.py`](../app/repositories/cosmos_repository.py) ·
Size guard: [`cosmos/modeling/size_guard.py`](../cosmos/modeling/size_guard.py) ·
Container definition: [`infra/bicep/modules/cosmos.bicep`](../infra/bicep/modules/cosmos.bicep) ·
Diagram: [cosmos-aggregate.mmd](../diagrams/cosmos-aggregate.mmd)

---

## 1. Document model

An order is **not** one item. It is one header item plus one item per logical
business block — the same blocks the SQL path stores as rows, produced by the
same [`block_splitter.py`](../ingestion/parser/block_splitter.py).

```json
{
  "id": "{orderId}:v{version}:{BLOCKTYPE}.{SUBTYPE}.{seq}",
  "docType": "orderBlock",
  "customerId": "POC001",
  "orderId": "…",
  "orderVersion": 1,
  "blockType": "TITLE",
  "blockSubType": "COMMITMENTS",
  "sequence": 0,
  "chunkIndex": 0,
  "chunkCount": 1,
  "payloadBytes": 183540,
  "payloadHash": "…",
  "data": { … }
}
```

and one header per order version:

```json
{
  "id": "{orderId}:v{version}:HEADER",
  "docType": "orderHeader",
  "customerId": "POC001",
  "orderId": "…",
  "orderVersion": 1,
  "isCurrent": true,
  "payloadBytes": 1316797,
  "search":  { "status": "Closed", "state": "CA", "maxLoanAmount": 812345.67, … },
  "summary": { …the exact GET /orders/{id}/summary body… },
  "extract": { "timestamp": "…", "server": "…", "type": "FULL" }
}
```

**The `id` is deterministic**, derived from `(orderId, version, blockKey)`. Re-ingesting
the same order version is therefore idempotent — an upsert, not a duplicate.

### Why the header carries a pre-built `summary`

`GET /orders/{id}/summary` is 50% of the realistic workload mix. Storing the
response body on the header makes that endpoint a **point read plus a dictionary
lookup**, with no reconstruction at all - SQL assembles the same body from a join
and three sub-selects. (The exact reconstruction milliseconds come from
telemetry that is a one-worker sample; see
[BENCHMARK_METHOD.md](BENCHMARK_METHOD.md) section 6. The structural difference is
the point, not the number.)

### Why the header carries a `search` projection

Without it, `GET /orders?state=CA&minLoanAmount=…` would have to open payload
items to find the values. With it, the search touches only header items — which
is why `data/*` can be excluded from indexing entirely.

This is also what keeps the comparison honest: the Cosmos model is given the
*same* searchable fields the SQL model gets as columns, from the same
[`relational_extract.py`](../ingestion/parser/relational_extract.py) projection.

---

## 2. Partition design

```
partitionKey: { paths: ["/customerId", "/orderId"], kind: "MultiHash", version: 2 }
```

### Why these two levels

| Candidate | Verdict |
| --- | --- |
| `/orderId` only | Even distribution, but no tenant locality — a per-customer list query fans out across every physical partition. |
| `/customerId` only | **This is the customer's current problem.** A tenant's partition grows without bound; see the table below. |
| `/customerId` → `/orderId` | **Chosen.** Tenant locality for list queries, one order per logical partition for reads. |
| `/customerId` → `/orderId` → `/id` | Rejected. See below. |

### Why *not* a third `/id` level

The brief suggests `/customerId, /orderId, /id`. Current documented behaviour
(see [SOURCES.md](SOURCES.md)) makes that actively worse here:

> With a three-level key, only a filter supplying **all three** values is a
> single-partition operation. A `/customerId + /orderId` prefix read — which is
> *exactly* what full-order reassembly does — becomes a targeted **cross-**
> sub-partition query rather than a single-partition one.

Reassembling an order means reading ~34 items that share `(customerId, orderId)`
but differ in `id`. Two levels makes that one single-partition query. Three
levels makes it a fan-out for no benefit. Hierarchical partition keys **cannot be
changed after container creation**, so this is a decision with no cheap undo.

### Distribution and growth

The logical partition is `(customerId, orderId)` = **one order's item set**.

| Property | Value | Source |
| --- | --- | --- |
| Bytes per logical partition | ~1.3 MB (mean order) | MEASURED |
| Cosmos logical partition limit | 20 GiB | [SOURCES.md](SOURCES.md) |
| Percent of limit used | **0.0061%** | computed |
| Order versions before the limit | ~16,300 | computed |
| Same data under a `/customerId`-only key (2 years, 8 tenants, medium scenario) | **223.8 GiB per partition — 1,119% of the limit** | [RETENTION_ANALYSIS.md](RETENTION_ANALYSIS.md) |

That last row is the whole argument. A single-level tenant key does not merely
distribute unevenly — **it hits a hard service limit** inside the stated
two-year retention horizon. The hierarchical key removes the failure mode by
construction, and the headroom does not shrink as tenants grow, because tenant
size no longer determines partition size.

Physical distribution stays even because `customerId` is hashed first and
`orderId` (a GUID) provides high cardinality within each tenant.

---

## 3. The item-size limit

Documented hard maximum: **2 MB per item** ([SOURCES.md](SOURCES.md)).
Working budget: `COSMOS_ITEM_TARGET_BYTES = 1,800,000`, minus a 2 KB envelope
reserve for the `_rid`/`_self`/`_etag`/`_ts` Cosmos adds plus our routing fields.

### The measured finding

| Source order | Items | Largest item | % of 2 MB limit | Array chunking needed |
| --- | ---: | ---: | ---: | --- |
| 500 KB | 32 | ~60 KB | 2.9% | no |
| 1 MB | 32 | ~125 KB | 6.0% | no |
| 5 MB | 39 | **760 KB** | **36%** | **no** |

**Business-domain decomposition alone keeps every item at or below ~36% of the
limit, even for the largest orders the customer describes.** Array-element
chunking — the escalation step — never engaged on real data at the default
budget. It is a safety net, not a routine code path.

This is the direct answer to "does the Cosmos item-size limit materially
complicate this workload?": **no, once the document model follows business
boundaries.** It would be fatal to a monolithic model; see the
[negative test](../tests/integration/test_cosmos_negative.py).

### The guard's escalation

[`size_guard.py`](../cosmos/modeling/size_guard.py) enforces the budget *before*
anything is sent:

1. **Business-domain split** — already done by the block splitter.
2. **Array-element chunking** — find the largest array *anywhere* in the block
   (real blocks nest, e.g. `OriginationChargeSection → Lines`), slice it by
   **whole elements**, and recurse if a chunk is still over budget because the
   block held several large arrays.
3. **Refuse** — if a single indivisible element exceeds the budget, raise.
   Never truncate. Never split serialized JSON text mid-object.

Every chunk is itself valid JSON and carries `chunkIndex`/`chunkCount`/
`chunkOfPath`, so `merge_chunks` reassembles it exactly. The unit tests force
the escalation with an artificially tight budget and assert losslessness at
300 KB, 250 KB and 200 KB.

---

## 4. Indexing policy

```
includedPaths: /customerId, /orderId, /orderVersion, /blockType, /blockSubType,
               /sequence, /docType, /search/*, /isCurrent, /modifiedUtc
excludedPaths: /data/*, /*
compositeIndexes: (search.status ASC, search.maxLoanAmount DESC)
                  (search.state ASC, search.status ASC)
```

The rule: **index what routes and filters; exclude what is only ever returned.**

`/customerId` and `/orderId` are listed explicitly. They are the partition-key
paths, and measurement showed they were **already effectively indexed** without
being declared — the cross-partition lookup on `orderId` cost only **3.24 RU**
across 17,928 items, which is index-seek behaviour, not a scan. They are declared
anyway so the policy states its own intent rather than relying on that; the
before/after measurement in
[`results/cosmos/index-impact.json`](../results/cosmos/index-impact.json) shows
the change made no material difference, which is the honest result.

`data/*` is the entire business payload — deeply nested, highly variable, and
never a query predicate. Indexing it would inflate write RU and index storage
for queries that are never issued. The default `/*` exclusion plus an explicit
include list means a new field in the payload does not silently start costing
write RU.

**Measured write cost:** 1,024 RU to ingest one complete order version as ~35
items (500-order run). Per-operation read RU is in
[BENCHMARK_SUMMARY.md](../results/BENCHMARK_SUMMARY.md) and priced in
[COST_ANALYSIS.md](COST_ANALYSIS.md).

### Scaling up raises the floor permanently

Worth knowing before provisioning for a burst. After the container was scaled to
40,000 RU/s (which triggered physical partition splits), it **could not be
returned to its original 4,000 RU/s ceiling**:

```
The offer should have valid throughput values between 6000 and 100000 ...
Requested throughput 4000 is less than required minimum throughput 6000.
Minimum limit 6000 is because of Highest RUs provisioned 60000.
```

The minimum autoscale maximum is a function of the highest throughput ever
provisioned, because the partition count it created does not shrink. Autoscale
bills a floor of 10% of the maximum, so **a temporary scale-up permanently raises
the idle bill** - here from 400 to 600 RU/s. Small in absolute terms, but it is a
one-way door and it belongs in any capacity plan.

---

## 5. The lookup tax

This is the most important operational finding about the Cosmos path, and it is
an **API-contract** problem rather than a Cosmos problem.

The required contract is `GET /orders/{orderId}` — **no customer in the route**.
But the partition key starts with `/customerId`. So every read must first
discover which tenant the order belongs to:

```
1. cross-partition query:  SELECT TOP 1 c.customerId, c.orderVersion
                           FROM c WHERE c.orderId = @oid AND c.docType = 'orderHeader'
2. point read:             read_item(id, partition_key=[customerId, orderId])
```

Step 1 is the only cross-partition query in the whole design, and it costs a
round trip plus RU on **every** read. It shows up directly in the measurements:
a Cosmos summary read is ~14 ms server-side versus ~4 ms for SQL, and the
difference is almost entirely this lookup.

**The fix is free and belongs in the API, not the database:** put `customerId`
in the route (`GET /customers/{customerId}/orders/{orderId}`) or take it from the
caller's token. Then the summary read is a pure point read — the cheapest
operation Cosmos offers.

The POC keeps the tax in place because the brief specifies that contract and
both backends must expose the *same* one. Its cost is measured and reported
separately (`lookupRu` in the write results) so it can be subtracted when
evaluating a production design.

---

## 6. Consistency, throughput, auth

| Setting | Value | Why |
| --- | --- | --- |
| Consistency | **Session** | The documented default; correct for a read-your-writes operational API. Strong would raise read RU and constrain multi-region without buying anything here. |
| Throughput | **Autoscale.** 4,000 RU/s max initially; raised to 40,000 to reach parity on payload-heavy reads | Autoscale costs 1.5x provisioned per RU but absorbs burstiness without manual resizing. The required ceiling is a *measured* quantity - see [DECISION_MATRIX.md](DECISION_MATRIX.md) section 3. [COST_ANALYSIS.md](COST_ANALYSIS.md) prices both modes. |
| Regions | Single (westus3) | Multi-region is a separate decision; adding it changes RU cost and consistency trade-offs. |
| Auth | **Entra data-plane RBAC** (Cosmos DB Built-in Data Contributor) | The application never sees an account key. |
| Backup | Continuous 7-day PITR | POC-appropriate. |
