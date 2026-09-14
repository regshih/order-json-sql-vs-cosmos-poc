# Microsoft Fabric analytics

**Both operational paths reach the same Fabric analytics model.** That is the
question this section exists to settle, and it is settled by measurement:
SQL → Fabric and Cosmos → Fabric both landed as Delta tables in OneLake, both
populate the same `FactOrder`, and the multi-hundred-KB JSON blocks arrived
byte-intact.

Diagram: [fabric-analytics.mmd](../diagrams/fabric-analytics.mmd) ·
Provisioning: [`fabric/provision_fabric.py`](../fabric/provision_fabric.py) ·
Mirroring: [`fabric/setup_mirroring.py`](../fabric/setup_mirroring.py) ·
Extractor: [`ingestion/fabric/push_to_onelake.py`](../ingestion/fabric/push_to_onelake.py) ·
Model: [`fabric/warehouse/analytics_model.sql`](../fabric/warehouse/analytics_model.sql)

---

## 1. Environment created

| Item | Name | Notes |
| --- | --- | --- |
| Capacity | `fabordjsonpoc915d` | **F2** — smallest practical SKU, created for this POC |
| Workspace | `ws-order-json-poc` | |
| Lakehouse | `lh_orders_poc` | |
| Warehouse | `wh_orders_poc` | hosts the curated star schema |
| Mirrored DB (SQL path) | `mir_sql_orders` | Open Mirroring |
| Mirrored DB (Cosmos path) | `mir_cosmos_orders` | Open Mirroring |

All created through the Fabric REST API as the signed-in user (the
`MirroredDatabase` API does not accept service principals — see
[SOURCES.md](SOURCES.md)). Both benchmark VM managed identities were granted
workspace **Contributor** so the extractor can write with managed identity
rather than a secret.

## 2. Choosing the integration mechanism

The brief asks for the simplest *currently supported* pattern, verified rather
than assumed. Two were attempted, in order.

### Attempt 1 — native Fabric Mirroring

`POST /workspaces/{ws}/mirroredDatabases` with an `AzureSqlDatabase` /
`CosmosDb` source was **accepted (HTTP 201)**, but the items were inert shells:
`getMirroringStatus` returned `404 EntityNotFound`, because native mirroring
also needs a Fabric *connection* carrying credentials, and Fabric must be able
to reach the source.

The reachability question turned out to be solvable: a **Fabric managed private
endpoint was created successfully on an F2 capacity** and approved on the SQL
server.

```
POST /workspaces/{ws}/managedPrivateEndpoints   -> 201 Provisioning -> Succeeded
az network private-endpoint-connection approve  -> Approved
```

**Finding:** managed private endpoints are *not* gated to large SKUs — F2 was
sufficient to create and approve one. That removes the usual objection to native
mirroring against private-only sources. Completing the native path additionally
requires a Fabric connection with a credential (a service principal with
database access), which is a credential-management decision rather than a
technical blocker.

### Attempt 2 — Open Mirroring (the path actually measured)

A `GenericMirror` mirrored database exposes a OneLake landing zone. An extractor
**inside the VNet** reads the operational store over its private endpoint and
pushes Parquet **outbound** to that landing zone; Fabric's managed replicator
merges it into Delta with insert/update/delete semantics.

```
<workspaceId>/<mirroredDatabaseId>/Files/LandingZone/<Table>/_metadata.json
                                                            /00000000000000000001.parquet
```

Every row carries `__rowMarker__` (4 = upsert, the correct semantic for a
watermark-based pull) and `_extractedUtc`, which is the freshness clock.

**Why this is not a consolation prize.** It requires no inbound path to the
databases, so it works under the tenant's private-only policy unchanged; it puts
the customer in control of exactly which columns leave the operational store;
and it is a first-class supported Fabric feature. The same pattern was already
proven at sub-30-second latency for this customer's domain in a prior POC
([CURRENT_STATE_CONTEXT.md](CURRENT_STATE_CONTEXT.md) §4).

## 3. What landed — MEASURED

Full push of the 500-order dataset:

| Path | Files | Rows | Parquet bytes | Elapsed |
| --- | ---: | ---: | ---: | ---: |
| SQL → OneLake | 9 | 59,020 | 199,245,586 | 104.6 s |
| Cosmos → OneLake | 1 | 17,928 | 534,185 | 1.2 s |

Delta tables materialised by the Fabric replicator:

| `mir_sql_orders` | Rows | | `mir_cosmos_orders` | Rows |
| --- | ---: | --- | --- | ---: |
| `Orders` | 506 | | `CosmosOrderItems` | 17,928 |
| `OrderJsonBlocks` | 17,079 | | | |
| `Parties` | 20,668 | | | |
| `OrderParties` | 18,369 | | | |
| `Loans` | 917 | | | |
| `Properties` | 917 | | | |
| `OrderVersions` | 555 | | | |
| `Customers` | 9 | | | |

The Cosmos path is ~370x smaller in Parquet because the extractor projects the
nested payload to a JSON string column rather than carrying every block's full
`data` tree — a deliberate choice, since analytics needs the *facts*, not the
pass-through payload.

## 4. The LOB question — the headline Fabric finding

Current documentation warns that **LOB columns over 1 MB are silently truncated
to 1 MB** on the way into OneLake ([SOURCES.md](SOURCES.md)). `OrderJsonBlocks.JsonPayload`
is `nvarchar(max)`. So: did the JSON survive?

Measured **inside Fabric**, against the Delta table:

| Check | Result |
| --- | --- |
| Block rows in Fabric | **17,079** |
| Max `LEN(JsonPayload)` | **759,857** chars |
| Max source `PayloadBytes` | **759,857** — *identical* |
| Rows ≥ 1 MiB | **0** |
| `ISJSON() = 1` | **17,079** |
| `ISJSON() = 0` | **0** |
| Rows where `LEN(JsonPayload) <> PayloadBytes` | **0** |

And the largest blocks parse as JSON *in Fabric*:

```
TITLE/PRODUCTS   759,857 chars   OPENJSON topLevelKeys = 1
TITLE/PRODUCTS   753,381 chars   OPENJSON topLevelKeys = 1
TITLE/PRODUCTS   753,306 chars   OPENJSON topLevelKeys = 1
```

**Nothing was truncated. Nothing was corrupted.**

This is not luck. Splitting on *business* boundaries caps the largest block at
~760 KB even for a 5 MB order, which clears the 1 MB ceiling with 24% headroom.
The same decomposition that satisfies the Cosmos 2 MB item limit also satisfies
the Fabric mirroring LOB limit — two unrelated platform constraints solved by
one modelling decision.

**The corollary matters just as much:** a design that stored each order as one
multi-megabyte JSON column *would* have been truncated, silently, and the loss
would only have surfaced as parse failures in analytics much later.

## 5. The analytics model

[`fabric/warehouse/analytics_model.sql`](../fabric/warehouse/analytics_model.sql)
builds a star schema in the Warehouse using **cross-database queries** into both
mirrors — no data copy, no pipeline.

```
FactOrder         <- mir_sql_orders.dbo.Orders        (SourceBackend = 'sql')
FactOrder         <- mir_cosmos_orders.dbo.CosmosOrderItems (SourceBackend = 'cosmos')
FactLoan          <- mir_sql_orders.dbo.Loans
FactOrderCharge   <- OPENJSON over mir_sql_orders.dbo.OrderJsonBlocks (CDF/*)
DimCustomer, DimProperty, DimParty  <- mir_sql_orders
DimDate, DimOrderStatus             <- generated
```

`FactOrder` is loaded **from both backends into the same table**, tagged with
`SourceBackend`. That is what makes the "the operational choice does not
determine the analytics model" claim testable rather than rhetorical — the two
loads are reconciled row-for-row.

`FactOrderCharge` is the load-bearing one: it shreds CDF line items out of the
mirrored `JsonPayload` with `OPENJSON`. **It only works because the JSON survived
mirroring intact**, which is the proof in §4 expressed as a working query.

### Build timings (MEASURED, F2 capacity)

| Step | ms |
| --- | ---: |
| DDL (16 statements) | 2–230 each |
| `DimCustomer` / `DimProperty` / `DimParty` | 363 / 333 / 629 |
| `FactOrder` from SQL mirror | 635 |
| `FactOrder` from Cosmos mirror | 404 |
| `FactLoan` | 417 |
| `FactOrderCharge` (OPENJSON shred) | **7,464** |

### Two Fabric Warehouse limitations found by running it

Both were discovered by a failing statement, not assumed:

1. **`tinyint` is not supported.** `The data type 'tinyint' ... is not supported
   in this edition of SQL Server.` `smallint` is the narrowest integer type.
2. **No supported set-based row generator.** `sys.all_objects` is rejected with
   `The query references an object that is not supported in distributed
   processing mode`, and there is no recursive-CTE alternative. `DimDate` is
   therefore generated client-side and inserted as explicit rows.

## 6. Representative analytics (MEASURED, F2)

| Query | ms | Rows |
| --- | ---: | ---: |
| `orders_by_state` | 750 | 40 |
| `orders_by_status` | 924 | 14 |
| `average_loan_amount` | 728 | 7 |
| `cdf_charges_by_category` | 641 | 16 |
| `title_fees_by_description` | 593 | 7 |
| `closing_cost_totals_by_order` | 637 | 25 |
| `order_version_and_size_distribution` | 69 | 2 |
| `property_geography` | 547 | 237 |
| `json_payload_integrity` | 19,571 | 1 |

The integrity query is slow because it computes `LEN()` and `ISJSON()` over
every one of 17,079 multi-hundred-KB strings — a deliberate full scan of ~690 MB
of JSON, not a representative analytics query.

Query definitions: [`fabric/warehouse/analytics_queries.sql`](../fabric/warehouse/analytics_queries.sql).

## 7. Reconciliation

[`tools/reconciliation.py`](../tools/reconciliation.py) checks three things
independently: SQL vs Cosmos, SQL vs Fabric, Cosmos vs Fabric.

**An important caveat about the first reconciliation run.** It reported 145
status disagreements and 5 Cosmos-only orders. Investigated rather than
explained away, every difference traced to *deliberate single-backend test
writes*:

- the SQL **write benchmark** mutated `Status`, `Balance` and `Project` on 200
  orders in SQL only;
- the `version` write shape re-ingested orders with fresh content into SQL only,
  moving `MaxLoanAmount`;
- the Cosmos **negative test** and **index-impact measurement** wrote 5 extra
  orders (`POCNEG*`, `POCIDX`) to Cosmos only.

That is test-harness divergence, not pipeline error — but it means a
reconciliation claim is only meaningful against a freshly re-ingested baseline.
See [BENCHMARK_SUMMARY.md](../results/BENCHMARK_SUMMARY.md) for the state of the
clean run.

## 8. Data freshness

Measured by [`tools/run_analytics.py --freshness`](../tools/run_analytics.py),
which writes to the operational store, runs the incremental extractor, then
polls Fabric until the change is queryable. It reports three intervals
separately so the result is diagnostic rather than one opaque number:

| Interval | What it covers |
| --- | --- |
| `writeMs` | the operational write itself |
| `pushSec` | extractor: read changes over the private endpoint, write Parquet to OneLake |
| `pushToVisibleSec` | Fabric replicator merge + SQL endpoint metadata sync |
| `endToEndSec` | operational write → queryable in Fabric |

**The pipeline is pull-based on a manual trigger in this POC**, so `endToEnd` is
dominated by how often the extractor runs, not by Fabric. The number that
characterises *Fabric* is `pushToVisibleSec`. A production deployment would run
the extractor on a schedule (or from the Cosmos change feed / SQL change
tracking) and the cadence becomes a tuning decision.

## 9. Cost

Fabric capacity is a **flat cost that does not vary with the operational
database choice**, so it is neutral in the SQL-vs-Cosmos decision. F2 was
sufficient for every step here, including the 7.5-second OPENJSON shred over
~690 MB of JSON. Current rates and the derivation are in
[COST_ANALYSIS.md](COST_ANALYSIS.md).

## 10. Conclusions

1. **Choosing the operational database does not constrain Fabric analytics.**
   Both paths land in OneLake as Delta and populate the same star schema.
2. **Business-boundary decomposition is what makes the SQL path safe for
   mirroring.** The largest block (760 KB) clears the 1 MB LOB ceiling; a
   whole-order JSON column would have been truncated silently.
3. **Cosmos is the cheaper path to move**, because only the projected fact
   columns need to travel (534 KB vs 199 MB of Parquet for the same 500 orders).
   The trade is that the nested payload arrives as a JSON string and any
   payload-level analytics must shred it at query time.
4. **F2 is adequate** for this data volume and these queries.
5. **Open Mirroring is the right integration under private-only networking**,
   and Fabric managed private endpoints (available on F2) make native mirroring
   a viable alternative once a connection credential is provisioned.
