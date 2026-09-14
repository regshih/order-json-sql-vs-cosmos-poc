# PATH A — Azure SQL Database hybrid relational + JSON design

Schema: [`sql/schema/01_schema.sql`](../sql/schema/01_schema.sql) ·
Repository: [`app/repositories/sql_repository.py`](../app/repositories/sql_repository.py) ·
Diagram: [sql-hybrid.mmd](../diagrams/sql-hybrid.mmd)

---

## 1. The rule

> **Frequently searched, joined, filtered, secured, sorted or reported fields →
> relational columns.
> Deeply nested, variable, sparse, largely pass-through structures → JSON blocks.**

Applied to the measured profile, that produces **8 business tables + 2
operational tables** and about 34 JSON block rows per order.

## 2. Tables

| # | Table | Purpose | Why relational |
| --- | --- | --- | --- |
| 1 | `ord.Customers` | tenant boundary | every query is scoped by it; it is the security boundary |
| 2 | `ord.Orders` | operational search surface | status, project, type, dates and the denormalised state/loan accelerators are in every list query |
| 3 | `ord.OrderVersions` | source-version lineage | links an order version to its archive URI and payload hash for replay |
| 4 | `ord.Properties` | address + parcel identity | state and county drive reporting; city/zip drive search |
| 5 | `ord.Parties` | **one** party table | buyers, sellers, lenders, title companies and "others" share a shape |
| 6 | `ord.OrderParties` | role assignment | the role is an edge property, not a separate entity |
| 7 | `ord.Loans` | loan identity + amount | `minLoanAmount` is a documented API filter |
| 8 | `ord.OrderJsonBlocks` | **the hybrid core** | one row per logical business block |
| 9 | `ord.IngestionRuns` | operational | run-level audit |
| 10 | `ord.AuditEvents` | operational | event-level audit |

### 2.1 Why one `Parties` table and not six

The source carries `Buyers`, `Sellers`, `Lenders`, `TitleCompanies` and `Others`
as five arrays of **near-identical objects** — same `Name`, `Address`, `Email`,
`Phone`, `People[]` shape. Six structurally identical tables would mean:

- six near-duplicate `INSERT` paths in ingestion,
- a six-way `UNION ALL` for "every party on this order",
- a new table every time the source adds a role.

A role discriminator on `OrderParties` gives one insert path, one index
(`OrderId, Role`), and new roles cost zero DDL. The measured dataset already
exercises `BUYER`, `SELLER`, `LENDER`, `TITLE_COMPANY` plus derived roles from
the `Others` array.

### 2.2 The denormalised accelerators on `Orders`

`PrimaryState`, `PrimaryCounty` and `MaxLoanAmount` are copies of values that
also live in `Properties` and `Loans`. That is deliberate:

`GET /orders?state=CA&minLoanAmount=500000` is a **list** query. Without these
columns it needs a join to `Properties` and an aggregate over `Loans` for every
candidate order before it can filter. With them it is a single covering index
seek. They are maintained by ingestion (which already has the whole order in
memory), not by a trigger.

The measured cost: `search` at 50 RPS runs at **p50 6.9 ms / p95 8.7 ms** — see
[BENCHMARK_SUMMARY.md](../results/BENCHMARK_SUMMARY.md).

## 3. `OrderJsonBlocks` — the hybrid core

```sql
JsonBlockId   bigint IDENTITY
OrderId       uniqueidentifier
OrderVersion  int
BlockType     varchar(40)     -- CDF | TITLE | PARTIES | NOTES | CHECKLIST | ...
BlockSubType  varchar(40)     -- COMMITMENTS | POLICIES | DISBURSEMENTS | ...
Sequence      smallint
JsonPayload   nvarchar(max)   -- CHECK (ISJSON(JsonPayload) = 1)
PayloadBytes  int
PayloadHash   char(64)
LastModified  datetime2(3)
```

Block boundaries come from [`block_splitter.py`](../ingestion/parser/block_splitter.py)
and are **business** boundaries, never byte offsets. The composite sections are
divided along their own structure before size is ever considered:

| Source section | Becomes |
| --- | --- |
| `CDFs[]` | `CDF/ORIGINATION`, `CDF/SERVICES`, `CDF/OTHER_COSTS`, `CDF/DUE_FROM_BUYER`, `CDF/DUE_FROM_SELLER`, `CDF/TOTALS`, `CDF/MAIN` |
| `CDFAmounts` | `CDF/DISBURSEMENTS`, `CDF/RECEIPTS`, `CDF/AMOUNTS` |
| `Title` | `TITLE/COMMITMENTS`, `TITLE/POLICIES`, `TITLE/PRODUCTS`, `TITLE/ENDORSEMENTS`, `TITLE/CHARGES`, `TITLE/MAIN` |
| `Buyers`/`Sellers`/… | `PARTIES/BUYERS`, `PARTIES/SELLERS`, … |
| scalar header fields | `ORDER/HEADER` |
| everything unrecognised | `MISC/MAIN` |

`MISC/MAIN` is what keeps the split **lossless when the source evolves**: a
property the splitter has never seen still round-trips, it simply is not
promoted to its own block until someone decides it deserves one.

### 3.1 `nvarchar(max)` and not the native `json` type

Azure SQL Database now has a GA native `json` type (see
[SOURCES.md](SOURCES.md)). This POC deliberately uses `nvarchar(max)` with an
`ISJSON` check constraint, for one measured reason:

> A table containing a `json`-typed column **cannot be mirrored to Fabric today**.

Since Fabric analytics is a hard requirement for this customer, adopting the
`json` type would forfeit the simplest analytics integration. `nvarchar(max)` +
`ISJSON` gives validation at write time at negligible cost (we only write through
ingestion) and keeps every Fabric option open. Revisit when mirroring supports
the type.

### 3.2 Measured block sizes

From the 500-order dataset (identical in both backends):

| Measure | Value |
| --- | --- |
| Blocks per order | ~34 |
| Largest single block, whole dataset | **759,857 bytes** |
| Largest block for a 5 MB order | ~760 KB |

**That number matters twice.** It is well under the Cosmos 2 MB item limit
(§[COSMOS_DESIGN.md](COSMOS_DESIGN.md)), and it is under the 1 MB LOB ceiling
that Fabric mirroring imposes on `nvarchar(max)` columns — so the JSON blocks
survive the trip to OneLake intact. Business-boundary splitting turned out to
satisfy two unrelated platform constraints at once.

## 4. Indexing

Every index is tied to a real API predicate. None was added speculatively.

| Index | Serves |
| --- | --- |
| `IX_Orders_Customer_Status` (+ INCLUDE) | `GET /orders?customerId=&status=` — covering |
| `IX_Orders_State_Customer` | `GET /orders?state=` |
| `IX_Orders_MaxLoanAmount` | `GET /orders?minLoanAmount=` range seek |
| `IX_Orders_ModifiedDate` | incremental extraction into Fabric |
| `IX_Properties_Order`, `IX_Properties_State` | property lookup and state reporting |
| `IX_OrderParties_Order_Role`, `IX_OrderParties_Party` | parties of an order; orders of a party |
| `IX_Loans_Order`, `IX_Loans_Amount` | loan lookup and amount range |
| `IX_Blocks_Order_Version_Type` | full-order read **and** single-block read |
| `IX_Blocks_LastModified` | incremental extraction into Fabric |

**`JsonPayload` is deliberately excluded from every INCLUDE list.** Including a
15 KB–760 KB LOB in a nonclustered index would duplicate the entire dataset.

## 5. Connection pooling

[`ConnectionPool`](../app/repositories/sql_repository.py) is explicit rather
than relying on pyodbc's opaque process-global pool, so that checkout time is
**measurable** (`sql_pool_checkout_ms` in the telemetry) and the size is a
deliberate knob (`SQL_POOL_SIZE`, 32 in the benchmark). Entra tokens are cached
until five minutes before expiry so a pool refill does not cost an HTTP round
trip.

---

## 6. Design control — why not the two extremes

Runnable demonstrator: [`sql/queries/design_control.sql`](../sql/queries/design_control.sql)

### 6.1 Control A — fully normalise every JSON node

The measured source has 106 top-level sections and max nesting depth 16. A
faithful relational decomposition needs a table per repeating object type.

The demonstrator normalises **one** section — `CDFs` — and already needs
**five tables** (`CdfDocument`, `CdfSection`, `CdfLine`, `CdfLineCharge`,
`CdfDisbursement`). Extrapolated across the sections that actually contain
repeating structures, a faithful model runs to **well over a hundred tables**.

The costs, in order of how much they hurt:

1. **Query complexity.** Returning the CDF section of one order becomes a
   four-way join that still has to be re-nested into JSON by application code.
   The hybrid model answers it with `WHERE OrderId = ? AND BlockType = 'CDF'`.
2. **Schema-evolution burden.** The source is a vendor extract. Every new nested
   field is a migration across a hundred tables, coordinated with a release.
   In the hybrid model an unrecognised field lands in `MISC/MAIN` and nothing breaks.
3. **Maintenance burden.** ~100 tables × ~10 columns is ~1,000 column definitions
   to keep aligned with a schema you do not control.
4. **Write amplification.** One order becomes hundreds of small inserts instead
   of ~34 block rows.

**And the payoff is near zero**, because the fields those hundred tables would
expose are overwhelmingly *pass-through*: 33% of scalars in the sample are empty
strings, and the large sections (title exceptions, CDF line descriptions, RTF
notes) are read back verbatim and never filtered on.

### 6.2 Control B — one giant JSON column, nothing extracted

`ctrl.OrderBlob(OrderId, OrderJson nvarchar(max))`.

| Operation | Blob-only model | Hybrid model |
| --- | --- | --- |
| `?customerId=&status=&minLoanAmount=` | full scan; JSON-parse **every** row; `OPENJSON` aggregate per row to get the loan amount. No index can help — the predicate values are inside the LOB | covering index seek on `IX_Orders_Customer_Status` |
| `GET /orders/{id}/title` | read the **entire** 0.5–5 MB document to return one section | read only the `TITLE/*` rows |
| Row size | one row up to 5 MB; every read is a LOB read | ~34 rows of 15–760 KB; reads touch only what is asked for |
| Fabric analytics | a single opaque LOB column; every fact must be parsed at query time, and values over 1 MB are truncated in transit | relational facts arrive as columns |

Computed columns plus indexes can rescue *some* of this, but each one is a
schema change that pins a JSON path — which is the normalisation burden of
Control A reintroduced one field at a time, without the clarity.

### 6.3 The conclusion

Neither extreme is wrong in principle; both are wrong *for this shape of data*.
The measured profile — a few big pass-through sections, a long tail of small
ones, and roughly twenty fields that carry every query predicate — is precisely
the shape a hybrid model is for.
