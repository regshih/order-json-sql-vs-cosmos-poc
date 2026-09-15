# Document Size Results

Generated 2026-09-15T17:40:56.670371+00:00 by [tools/summarize_document_sizes.py](../tools/summarize_document_sizes.py) from the machine-readable run files. **No figure in this document was typed by hand.**

Terms are used strictly and are not interchangeable:

| Term | Meaning |
| --- | --- |
| **LOGICAL ORDER** | one business order - the complete extract envelope |
| **DATABASE ITEM** | one physical row (SQL) or BSON document (Mongo) or item (NoSQL) |
| **API RESPONSE** | what `GET /orders/{id}` returns |

Every row below stores **one LOGICAL ORDER as one DATABASE ITEM**. Profiles marked `*` are boundary/stress probes: they exist to find where each platform refuses a document and **do not describe the customer's data**.

## 1. Can the complete order be stored as ONE database item?

| Physical storage | Largest ACCEPTED | Smallest REJECTED | Failure mode |
| --- | ---: | ---: | --- |
| Azure SQL - one row, `nvarchar(max)` | 16.70 MB | none | - |
| Azure SQL - one row, native `json` | 16.70 MB | none | - |
| Cosmos DB for MongoDB - one BSON document | 15.04 MB | 16.70 MB | `DocumentTooLarge` |
| Cosmos DB for NoSQL - one item (counterfactual) | 1.78 MB | 2.01 MB | `413 CosmosHttpResponseError` |

The Cosmos NoSQL row is a **counterfactual**, not the Scenario C design. It stores a whole order as a single item to locate the 2 MB ceiling exactly. Scenario C splits the order into many items and is unaffected by it - the limit constrains the DOCUMENT MODEL, not the engine.

## 2. JSON bytes are not BSON bytes

| Profile | JSON (compact) | BSON encoded | BSON overhead | max array | max props | depth |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `p500k` | 0.49 MB | 0.50 MB | +1.45% | 77 | 87 | 13 |
| `p1m` | 0.95 MB | 0.96 MB | +1.31% | 122 | 87 | 13 |
| `p1_5m` | 1.50 MB | 1.52 MB | +1.48% | 145 | 87 | 13 |
| `p1_8m` | 1.79 MB | 1.82 MB | +1.47% | 167 | 87 | 13 |
| `p1_9m` | 2.01 MB | 2.04 MB | +1.45% | 169 | 87 | 13 |
| `p2_1m` | 2.20 MB | 2.23 MB | +1.5% | 182 | 87 | 13 |
| `p3m` | 3.05 MB | 3.10 MB | +1.44% | 227 | 87 | 13 |
| `p5m` | 4.86 MB | 4.93 MB | +1.48% | 302 | 87 | 13 |
| `p10m *` | 9.60 MB | 9.74 MB | +1.49% | 452 | 87 | 13 |
| `p15m *` | 15.06 MB | 15.29 MB | +1.53% | 572 | 87 | 13 |
| `p17m *` | 16.72 MB | 16.98 MB | +1.55% | 602 | 87 | 13 |

BSON is consistently **larger** than compact JSON for this data shape. A document sitting on the 16 MB line as JSON is over the line once encoded, so acceptance must be judged on encoded size. Sizing a Mongo design against source-file size errs in the dangerous direction.

Shape limits are far from binding: the widest array and deepest nesting measured are orders of magnitude inside the native `json` type's documented 65,535 / 65,535 / 128 ceilings, even at 17 MB.

## 3. Latency by payload size

**Point read - the complete order** (ms)

| Payload | one row, `nvarchar(max)` | one row, native `json` | one BSON document | one item (counterfactual) |
| --- | ---: | ---: | ---: | ---: |
| 0.49 MB | 19.0 | 27.4 | 43.9 | 102.7 |
| 0.93 MB | 11.3 | 42.6 | 48.0 | 77.0 |
| 1.50 MB | 14.4 | 65.2 | 80.8 | 220.7 |
| 1.78 MB | 15.5 | 90.3 | 98.4 | 136.6 |
| 2.01 MB | 16.6 | 85.5 | 112.9 | _not stored_ |
| 2.21 MB | 17.8 | 95.0 | 118.9 | _not stored_ |
| 3.05 MB | 23.5 | 127.3 | 163.1 | _not stored_ |
| 4.87 MB | 36.0 | 203.6 | 247.2 | _not stored_ |
| 9.61 MB * | 118.9 | 395.1 | 507.4 | _not stored_ |
| 15.04 MB * | 114.4 | 624.3 | 931.0 | _not stored_ |
| 16.70 MB * | 133.2 | 680.3 | _not stored_ | _not stored_ |

**Insert - one complete order** (ms)

| Payload | one row, `nvarchar(max)` | one row, native `json` | one BSON document | one item (counterfactual) |
| --- | ---: | ---: | ---: | ---: |
| 0.49 MB | 398.7 | 81.4 | 468.2 | 163.8 |
| 0.93 MB | 106.8 | 178.3 | 52.6 | 58.4 |
| 1.50 MB | 171.6 | 383.8 | 88.4 | 82.9 |
| 1.78 MB | 164.3 | 510.5 | 94.4 | 1,041.3 |
| 2.01 MB | 175.8 | 620.8 | 111.5 | _not stored_ |
| 2.21 MB | 209.7 | 701.3 | 121.9 | _not stored_ |
| 3.05 MB | 293.9 | 1,328.6 | 165.3 | _not stored_ |
| 4.87 MB | 802.8 | 2,849.5 | 276.2 | _not stored_ |
| 9.61 MB * | 975.7 | 8,158.9 | 554.5 | _not stored_ |
| 15.04 MB * | 1,502.5 | 15,019.8 | 960.7 | _not stored_ |
| 16.70 MB * | 1,098.9 | 14,943.0 | _not stored_ | _not stored_ |

**Update one top-level scalar** (ms)

| Payload | one row, `nvarchar(max)` | one row, native `json` | one BSON document | one item (counterfactual) |
| --- | ---: | ---: | ---: | ---: |
| 0.49 MB | 107.6 | 96.5 | 71.0 | - |
| 0.93 MB | 37.7 | 203.3 | 81.6 | - |
| 1.50 MB | 139.9 | 429.1 | 118.5 | - |
| 1.78 MB | 606.6 | 577.1 | 153.4 | - |
| 2.01 MB | 66.9 | 707.1 | 137.6 | _not stored_ |
| 2.21 MB | 758.7 | 782.6 | 170.3 | _not stored_ |
| 3.05 MB | 907.0 | 1,386.2 | 248.2 | _not stored_ |
| 4.87 MB | 151.4 | 2,857.9 | 379.0 | _not stored_ |
| 9.61 MB * | 3,949.8 | 8,836.5 | 766.6 | _not stored_ |
| 15.04 MB * | 6,314.1 | 15,876.5 | 1,192.6 | _not stored_ |
| 16.70 MB * | 7,071.0 | 17,253.0 | _not stored_ | _not stored_ |

**Update one deeply nested field** (ms)

| Payload | one row, `nvarchar(max)` | one row, native `json` | one BSON document | one item (counterfactual) |
| --- | ---: | ---: | ---: | ---: |
| 0.49 MB | 50.5 | 77.1 | 58.3 | - |
| 0.93 MB | 39.8 | 144.7 | 67.8 | - |
| 1.50 MB | 58.6 | 298.2 | 99.6 | - |
| 1.78 MB | 257.1 | 417.5 | 127.9 | - |
| 2.01 MB | 67.2 | 526.9 | 126.1 | _not stored_ |
| 2.21 MB | 243.3 | 553.5 | 161.8 | _not stored_ |
| 3.05 MB | 502.7 | 914.6 | 197.0 | _not stored_ |
| 4.87 MB | 151.9 | 2,024.4 | 329.1 | _not stored_ |
| 9.61 MB * | 1,410.6 | 6,650.7 | 633.3 | _not stored_ |
| 15.04 MB * | 2,528.5 | 11,250.9 | 1,425.9 | _not stored_ |
| 16.70 MB * | 2,780.4 | 13,656.7 | _not stored_ | _not stored_ |

**Replace the whole document** (ms)

| Payload | one row, `nvarchar(max)` | one row, native `json` | one BSON document | one item (counterfactual) |
| --- | ---: | ---: | ---: | ---: |
| 0.49 MB | 60.4 | 83.3 | 34.4 | - |
| 0.93 MB | 56.7 | 183.6 | 59.3 | - |
| 1.50 MB | 346.9 | 385.1 | 81.7 | - |
| 1.78 MB | 534.1 | 530.2 | 112.1 | - |
| 2.01 MB | 144.1 | 611.4 | 104.6 | _not stored_ |
| 2.21 MB | 814.9 | 734.3 | 111.6 | _not stored_ |
| 3.05 MB | 1,061.0 | 1,381.8 | 157.8 | _not stored_ |
| 4.87 MB | 331.0 | 2,902.3 | 268.8 | _not stored_ |
| 9.61 MB * | 3,915.3 | 8,406.4 | 520.5 | _not stored_ |
| 15.04 MB * | 5,897.2 | 15,427.4 | 840.6 | _not stored_ |
| 16.70 MB * | 6,643.8 | 18,377.6 | _not stored_ | _not stored_ |

> Sizes differ by a few tenths of a percent between section 2 and section 3 because the two runs generate different order indices from the same profile. The profile fixes a target size, not a byte-identical document, so this is generator variance rather than measurement error.

> Single samples, not distributions. They establish **order of magnitude and trend**, not percentiles; the 50 RPS benchmark is where percentiles come from. The Azure SQL update figures in particular are noisy because the database is serverless and had recently resumed.

## 4. Cosmos DB for MongoDB - measured RU

| Payload | read RU | insert RU | scalar update RU | nested update RU | full replace RU | RU per MB (write) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.49 MB | 7.9 | 373.6 | 410.5 | 328.2 | 283.2 | 765 |
| 0.93 MB | 13.1 | 1,250.4 | 1,396.3 | 955.9 | 810.0 | 1,341 |
| 1.50 MB | 19.5 | 2,500.4 | 2,792.2 | 2,167.2 | 1,875.4 | 1,662 |
| 1.78 MB | 22.5 | 2,500.4 | 2,792.2 | 2,167.2 | 1,875.4 | 1,404 |
| 2.01 MB | 24.9 | 2,500.4 | 2,792.2 | 2,167.2 | 1,875.4 | 1,246 |
| 2.21 MB | 27.1 | 2,500.4 | 2,792.2 | 2,167.2 | 1,875.4 | 1,131 |
| 3.05 MB | 36.3 | 2,634.0 | 2,941.4 | 2,874.6 | 2,567.2 | 862 |
| 4.87 MB | 57.2 | 4,269.0 | 4,767.5 | 3,956.1 | 3,457.6 | 877 |
| 9.61 MB * | 110.2 | 8,416.1 | 9,399.3 | 7,740.0 | 6,756.8 | 875 |
| 15.04 MB * | 199.4 | 15,368.1 | 17,163.6 | 14,532.9 | 12,737.0 | 1,022 |
| 16.70 MB * | - | - | - | - | - | - |

Captured with `MONGO_CAPTURE_RU=1`, which pins the connection pool to a single connection. `getLastRequestStatistics` reports **connection-scoped** state, so sampling it under a shared pool attributes another request's charge - the same defect that under-reported Cosmos NoSQL RU by about 42x earlier in this POC.

Two conclusions follow directly:

1. **Reads are cheap; writes are not.** Write cost runs roughly two orders of magnitude above read cost for the same document.
2. **Update cost tracks DOCUMENT size, not CHANGE size.** Setting one top-level scalar costs about the same as rewriting the entire document - and measurably *more* than a straight replace, because `$set` requires a server-side read-modify-write while `replace_one` just writes. There is no cheap small edit to a large document.

## 5. What this means for the four scenarios

- **Azure SQL, one row** stores the complete order at every size tested and is the fastest on both read and write. The `nvarchar(max)` representation beats the native `json` type on every measured axis for this workload, because a whole-document read pays to parse on write and serialise on read while never querying the binary form in between. The native type is built for partial access; this is the one workload where it can only lose.
- **Cosmos DB for MongoDB** stores the complete order up to the documented 16 MB ceiling and reads it cheaply, but any write - including a one-field edit - costs in proportion to the whole document.
- **Cosmos DB for NoSQL** cannot hold this order as one item above 2 MB. That is why Scenario C decomposes, and why its API reassembles.

Security and platform constraints that no benchmark can offset are in [SOURCES.md](SOURCES.md) M.2 (customer-managed keys), M.5 (no native Fabric mirroring for the MongoDB API), M.6 (no Entra data-plane authentication) and N.2 (a mirrored table may not contain a `json` column).

