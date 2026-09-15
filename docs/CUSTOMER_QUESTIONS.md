# The customer's questions, answered

The thirteen questions from the clarified brief, each answered from measurement
where measurement exists and marked clearly where it does not.

Every answer is tagged:

| Tag | Meaning |
| --- | --- |
| **MEASURED** | observed on deployed Azure resources; the source file is named |
| **DOCUMENTED** | from current Microsoft documentation; the source is linked in [SOURCES.md](SOURCES.md) |
| **ESTIMATED** | derived from measured values by a stated calculation |
| **PENDING** | not yet measured; what is missing is named |

Terms are used strictly: a **LOGICAL ORDER** is one business order; a **DATABASE
ITEM** is one physical row or document; an **API RESPONSE** is what
`GET /orders/{id}` returns. They are not interchangeable.

---

### 1. Can Microsoft store this customer's complete 1–5 MB order as ONE operational database record?

**Yes — in two products.** **MEASURED**
([`results/document-size-tests.json`](../results/document-size-tests.json))

| Physical storage | Largest accepted | First rejection |
| --- | ---: | ---: |
| Azure SQL, one row, `nvarchar(max)` | **16.70 MB** | none tested |
| Azure SQL, one row, native `json` | **16.70 MB** | none tested |
| Cosmos DB for MongoDB, one BSON document | **15.04 MB** | 16.70 MB, `DocumentTooLarge` |
| Cosmos DB for NoSQL, one item | 1.78 MB | **2.01 MB**, HTTP **413** |

The customer's stated range is 1–5 MB. Azure SQL and Cosmos DB for MongoDB clear
it with a wide margin. Cosmos DB for NoSQL cannot hold such an order as a single
item at all.

### 2. Can Azure SQL store and efficiently serve the entire JSON as one record?

**Yes, and it is the fastest option measured.** **MEASURED**

Point read of the complete order, `nvarchar(max)`: **11.3 ms** at 0.93 MB,
**36.0 ms** at 4.87 MB, **133.2 ms** at 16.70 MB. `GET /orders/{id}` is a
single-row read whose bytes are handed to the HTTP layer without being parsed or
re-serialised.

### 3. Can Cosmos DB for MongoDB with 16-MB support store and efficiently serve the entire JSON as one document?

**Yes for storage and reads; the problem is writes.** **MEASURED**

Stores one complete order per document to 15.04 MB. Point read 48.0 ms at
0.93 MB, 247.2 ms at 4.87 MB — slower than Azure SQL at every size tested but
perfectly serviceable. See question 6 for the write cost, which is where this
design runs into trouble.

### 4. What are the actual performance characteristics at ~50 full-document reads/sec?

**MEASURED.** 46 runs, [FULL_DOCUMENT_BENCHMARK.md](FULL_DOCUMENT_BENCHMARK.md).
Open-model load from a separate VM in the same VNet, so queueing delay appears in
the latency rather than being hidden by coordinated omission.

At the customer's stated 50 RPS, all payload sizes mixed:

| Design | Achieved RPS | p50 | p95 | Errors | MB/s |
| --- | ---: | ---: | ---: | ---: | ---: |
| **A. SQL Full JSON (`nvarchar(max)`)** | **50.0** | **17.2 ms** | 63.1 ms | 0.00% | 73.9 |
| D. SQL hybrid (reassembled) | 49.0 | 29.5 ms | 92.1 ms | 0.00% | 63.3 |
| B. Cosmos Mongo (one document) | 49.9 | 66.9 ms | 181.1 ms | 0.00% | 68.8 |
| A'. SQL Full JSON (native `json`) | 13.9 | **55,591 ms** | 111,533 ms | 3.82% | 19.4 |
| C. Cosmos NoSQL (reassembled) | 12.4 | **65,169 ms** | 126,316 ms | 0.00% | 16.2 |

Three findings, in order of how much they change the recommendation:

1. **The native `json` type cannot serve this workload at all.** It is already
   degraded at 0.49 MB (38.1 RPS, ~8 s p50) and collapses by 25 RPS. Every read
   casts the whole binary document back to `nvarchar`. This is the third
   independent reason against it, after unmirrorable-to-Fabric and ~10x slower
   writes.
2. **The ranking inverts above the stated rate.** At 100 RPS Cosmos Mongo holds
   99.8 RPS at p50 73.1 ms while SQL Full JSON falls to 95.7 RPS at p50 1,532 ms.
   50 RPS is comfortable for Scenario A; it is not the point where Scenario A
   stops being comfortable, and that point is closer than the 50 RPS figures
   suggest.
3. **The ~150 MB/s flattening in both SQL designs is not the network.** Cosmos
   Mongo sustained **240.8 MB/s** at 4.86 MB payloads and 49.6 RPS across the
   *same two VMs and the same API process*, so the SQL ceiling is in the client
   driver's large-value path. Attributing it to the network would have been the
   easy and wrong conclusion.

**Provisioning disparity, stated because it changes how row C must be read.**
The Cosmos NoSQL container was at autoscale max **6,000 RU/s** and the Mongo
collection at **10,000 RU/s** — not a like-for-like comparison. A whole-order
read on C costs ~515 RU, so 6,000 RU/s supports ~11.6 RPS, which is what the
12.4 RPS observation is. Part 1 held 50 RPS on the same design at 40,000 RU/s.
**Row C is throttling, not incapability.** What *is* provisioning-independent is
the ratio: ~515 RU for a reassembled whole-order read (the under-load derivation
used for costing) against ~16 RU for a single-document read of an order of the
same mean size (**ESTIMATED**, interpolated between the measured 13.1 RU at
0.93 MB and 57.2 RU at 4.87 MB) — on the order of **30x** the request cost for
whole-order serving, and more if the isolated 1,187.5 RU figure from Part 1 is
used instead.

### 5. What is the measured cost/resource impact of 1, 2, 3 and 5 MB documents?

**MEASURED** for Cosmos DB for MongoDB
([`results/document-size-tests-mongo-ru.json`](../results/document-size-tests-mongo-ru.json)):

| Payload | read RU | insert RU | one scalar `$set` | full replace |
| ---: | ---: | ---: | ---: | ---: |
| 0.93 MB | 13.1 | 1,250.4 | 1,396.3 | 810.0 |
| 2.21 MB | 27.1 | 2,500.4 | 2,792.2 | 1,875.4 |
| 3.05 MB | 36.3 | 2,634.0 | 2,941.4 | 2,567.2 |
| 4.87 MB | 57.2 | 4,269.0 | 4,767.5 | 3,457.6 |

Approximately **12 RU per MB to read, 1,000 RU per MB to write**.

Azure SQL does not meter per request; its resource impact is CPU and log write on
a fixed vCore allocation, and it showed no capacity difficulty at any size tested.

### 6. Does storing a 5-MB Mongo document create unacceptable update/write cost even if reads are acceptable?

**Yes — unambiguously.** **MEASURED**

Changing **one top-level scalar field** on a 4.87 MB document costs **4,767.5
RU** — *more* than replacing the entire document (3,457.6 RU). Changing one
deeply nested field costs 3,956.1 RU.

The mechanism: the engine rewrites the whole document for any modification, and
`$set` is *more* expensive than `replace_one` because it forces a server-side
read-modify-write. **Update cost is a function of document size, not of change
size. There is no cheap small edit to a large document.**

For comparison, reading that same document costs 57.2 RU. Reads are roughly two
orders of magnitude cheaper than writes.

### 7. Does Cosmos DB for NoSQL remain attractive if the complete order must be split into multiple items?

**Only under a specific, testable condition.** **MEASURED** (Part 1)

The 2 MB item limit is not negotiable — a whole order is refused with HTTP 413
above it — so Cosmos NoSQL *must* decompose. Part 1 measured the consequence: at
50 RPS the realistic endpoint mix costs about **2x** Azure SQL, and a
whole-orders-only API costs about **8.7x**, because reassembly means a full-order
read touches many items. At a summary-only API it is about **half** the cost of
Azure SQL.

So it stays attractive if the API predominantly serves **narrow block-level
reads**. It becomes expensive precisely when the customer's clarified
requirement — serving the complete order — dominates.

### 8. How much overhead does reconstruction introduce for SQL Hybrid and Cosmos NoSQL?

**MEASURED, and it is far cheaper than expected.** Server-side reassembly time
at 50 RPS, from the same sweep as question 4:

| Payload band | SQL hybrid reassembly | Cosmos NoSQL reassembly | full-document designs |
| --- | ---: | ---: | ---: |
| 0.50 MB | 3.55 ms | 0.12 ms | 0.00 ms by construction |
| 1.97 MB | 12.79 ms | 0.13 ms | 0.00 ms |
| 3.01 MB | 6.21 ms | 0.12 ms | 0.00 ms |
| 4.76 MB | 15.06 ms | 0.14 ms | 0.00 ms |

Against 8-37 ms of database time in the same runs, **reassembly is not where
decomposition costs anything.** The price of splitting an order is the round
trips and the request units needed to fetch ~34 items, not the CPU to glue them
back together. (Cosmos NoSQL's reassembly appears near-zero because its items
are returned already-parsed and its cost is entirely in the fetch — 6.5-10 s of
database time per request under throttling.)

This reverses the intuition that motivated the full-document scenarios: removing
reassembly saves single-digit milliseconds, while removing round trips and RU
saves far more.

### 9. Does one physical document materially simplify the API/application?

**Yes, and it is visible in the code.** **MEASURED** (structurally)

`GET /orders/{id}` on a full-document backend is one point read returning stored
bytes. No block reassembly, no chunk merging, no size guard, no cross-item
consistency question. The decomposed backends need
[`block_splitter.py`](../ingestion/parser/block_splitter.py) and its inverse on
every read.

The honest qualifier: that machinery already exists and is tested, so the
simplification is larger for a *new* build than for this customer, who already
has the decomposed implementation working.

### 10. Does that simplicity justify the cost/performance/security tradeoffs?

**For Azure SQL Full JSON: yes — there is no tradeoff to justify.** It was the
fastest on both read and write at every size, stores the complete order, mirrors
to Fabric, and authenticates with Entra like everything else.

**For Cosmos Mongo: no.** The simplicity is real but is paid for with an
irreversible CMK conflict, no Entra data-plane authentication, no native Fabric
mirroring, and a write cost proportional to document size. None of those is
offset by a measured performance advantage at the stated workload — it was
slower than Azure SQL at every payload size tested for a single request, and
slower at 50 RPS.

**One qualification added after the 50 RPS sweep:** Cosmos Mongo *does* overtake
Azure SQL above the stated rate — 99.8 RPS at p50 73.1 ms against 95.7 RPS at
p50 1,532 ms, and 240.8 MB/s against ~150 MB/s at 4.86 MB payloads. So the
answer is "no" *for the workload as stated*. If the real rate is materially
higher than 50 RPS, or payloads sit near 5 MB, the performance side of this
trade stops favouring Azure SQL — and the security side (question 11) still
does.

### 11. Are any enterprise security requirements incompatible with the Mongo 16-MB capability?

**Yes. Two, and the first is permanent.** **DOCUMENTED** (SOURCES.md M.2, M.6)

1. **Customer-managed keys are impossible.** `EnableMongo16MBDocumentSupport` and
   CMK "are not supported together", and the capability **cannot be removed once
   enabled**. An account built for 16 MB documents can never be brought under
   CMK. For a payload carrying SSNs, wire instructions and loan detail, this is
   disqualifying wherever CMK is a stated control — regardless of benchmarks.
2. **No Entra data-plane authentication.** The MongoDB RU API supports account
   keys only. The documented mitigation — fetch the key at runtime with a managed
   identity — requires granting the workload a **control-plane** role able to
   list account keys, which is strictly more powerful than data-plane access and
   cannot be scoped per collection or made read-only per identity.

Two further frictions, **MEASURED** in this environment: capabilities cannot be
set through ARM or Bicep, so the account cannot be fully declared as IaC; and the
account was created with `disableLocalAuth: true` by default, which — because the
API has no alternative to key auth — makes a fresh account silently unusable
while a `ping` handshake still reports healthy.

### 12. If none of the one-document options meets the target, which fallback is better?

The premise does not hold: **Azure SQL Full JSON does meet the target**, so a
fallback is not required for the storage question.

Should the customer nonetheless prefer a decomposed design, Part 1's answer
stands: **SQL hybrid**, on latency, query flexibility and cost stability. A purely
tabular representation was not measured and is not recommended on current
evidence — shredding a deeply nested order into columns would lose fidelity that
both JSON approaches preserve, for no demonstrated benefit.

### 13. Does the operational choice affect the customer's future Fabric analytics architecture?

**Yes, for one of the four.** **DOCUMENTED + MEASURED**

Azure SQL (both designs) and Cosmos NoSQL all reach Fabric through the same
mechanism and produce the same star schema. **Cosmos DB for MongoDB does not**:
Fabric mirroring from Cosmos DB supports the **NoSQL API only**, so Scenario B
needs a different integration.

One constraint shapes the SQL design and is worth stating plainly: **a table
containing a native `json` column cannot be mirrored to Fabric**. That is why
Scenario A's deployable variant uses `nvarchar(max)`. Measurement then showed
this costs nothing — `nvarchar(max)` was faster than the native type on both read
and write at every size — so the analytics requirement and the performance
optimum point the same way.

---

## What would change these answers

- Question 4, 8 and parts of 10 rest on the 50 RPS full-document sweep, which is
  not yet run. Part 1 demonstrated that isolated measurement can understate
  concurrent cost by 1.7–2.1x, so none of the single-request latency figures
  here should be read as a throughput prediction.
- Question 11's CMK finding is the single most likely answer to change the
  recommendation, and it is a **policy** question rather than a technical one. If
  CMK is not required by this customer, Scenario B returns to contention on
  reads — though its write cost and Fabric gap remain.
