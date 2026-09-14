# OPTIONAL CONTROL — direct Fabric/Delta API serving

> **This is a CONTROL EXPERIMENT, not a third candidate architecture.**
>
> It exists to answer one question with measurement instead of assertion:
> *"Why not just have the API query Fabric/Delta directly?"*
>
> A benchmark that technically completes is **not** the same as architectural
> fitness. Read §5 before drawing any conclusion from the numbers.

Diagram: [fabric-direct-serving-control.mmd](../diagrams/fabric-direct-serving-control.mmd) ·
Implementation: [`app/repositories/fabric_repository.py`](../app/repositories/fabric_repository.py)

---

## 1. What was tested

The **same API, same endpoints, same reconstruction logic** — only the repository
changed:

```bash
STORAGE_BACKEND=fabric uvicorn app.api.main:app --workers 8
```

[`FabricOrderRepository`](../app/repositories/fabric_repository.py) issues T-SQL
against the **Fabric SQL analytics endpoint** over the `mir_sql_orders` mirrored
Delta tables. The Delta schema is a mirror of `ord.*` from PATH A, so the
reconstruction code is byte-for-byte the same as the SQL repository's — any
difference measured is the *engine*, not the data model.

It does **not** launch a Spark job per request. That would be an unfair strawman;
the SQL analytics endpoint is the current supported low-latency query surface
over OneLake.

| Setting | Value |
| --- | --- |
| Endpoint | `…datawarehouse.fabric.microsoft.com` (Fabric SQL analytics endpoint) |
| Database | `mir_sql_orders` (Open Mirroring Delta) |
| Capacity | **F2** |
| Auth | Entra via `DefaultAzureCredential`, Power BI scope |
| Pool | 16 connections |
| Endpoints tested | `summary`, `title`, `full` at 10 / 25 / 50 RPS |
| Data | the same 500 orders, 17,079 blocks |

## 2. Measured results

| Workload | Target RPS | Achieved | p50 ms | p95 ms | p99 ms | Errors | Error rate | MB/s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `summary` | 10 | 9.97 | 217.7 | 227.5 | 233.1 | 2 | 0.44% | 0.01 |
| `summary` | 25 | 24.90 | 226.7 | 254.8 | 269.9 | 7 | 0.62% | 0.02 |
| `summary` | 50 | 48.25 | **2,965.7** | 3,364.0 | 3,583.9 | **704** | **31.29%** | 0.02 |
| `title` | 10 | 10.00 | 70.2 | 90.0 | 108.0 | 1 | 0.22% | 3.90 |
| `title` | 25 | 24.95 | 71.4 | 136.3 | 173.2 | 1 | 0.09% | 9.87 |
| `title` | 50 | 49.93 | 70.5 | 125.5 | 200.2 | 3 | 0.13% | 19.33 |
| `full` | 10 | 9.99 | 131.1 | 197.4 | 253.1 | 1 | 0.22% | 12.86 |
| `full` | 25 | 24.93 | 130.8 | 201.3 | 265.5 | 4 | 0.36% | 32.45 |
| `full` | 50 | 49.98 | 144.3 | 224.8 | 292.8 | **1,074** | **47.73%** | 33.98 |

All errors were HTTP 500 — the API surfacing a failed query against the Fabric
endpoint.

Health-probe latency is itself telling: **822 ms**, against 1.19 ms for Azure SQL
and 63 ms for Cosmos.

## 3. The three findings

### 3.1 Point lookups are ~26x slower than the operational database

| `GET /orders/{id}/summary`, p50 | |
| --- | ---: |
| Azure SQL | **8.2 ms** |
| Cosmos DB | 33.9 ms |
| **Fabric SQL endpoint** | **217.7 ms** |

This is the single most important number here, and it is not a tuning problem.
A summary read is one indexed row — precisely the shape an analytics engine is
*not* built for. Fabric's SQL endpoint is optimised for scan throughput over
columnar Delta files, so there is a fixed per-query cost (distributed query
planning, file/statistics access) that dwarfs the work being asked for.

Note what this means: the endpoint's cost is dominated by **per-query overhead,
not payload size** — which is why a 430 KB `title` read (70 ms) is *three times
faster* than a 753-byte `summary` read (218 ms). That inversion is the signature
of an analytics engine.

### 3.2 It breaks down at the customer's stated operating point

Both `summary` and `full` held together at 10 and 25 RPS with <1% errors, then
failed at **50 RPS** — the rate the customer actually needs:

- `summary`: 31.3% errors and p50 rising 13x to 2,966 ms
- `full`: 47.7% errors

`title` survived 50 RPS at 0.13% errors, so the ceiling is not a simple
request-rate limit; it is sensitive to query shape and concurrency. Either way,
**a third to a half of requests failing at the target rate disqualifies it** as an
operational serving path.

This is consistent with the documented position ([SOURCES.md](SOURCES.md)):
Warehouse operations are classified as *background* work with a hard-rejection
throttle stage, and **no concurrency limit or latency SLO is published for
operational serving**. There is nothing to size against.

### 3.3 Block and full-order reads are respectable — and that is the trap

`title` at 70 ms p50 / 0.13% errors and `full` at 131 ms p50 (10–25 RPS) are
genuinely decent numbers. Reading ~430 KB or ~1.4 MB of columnar data is exactly
what the engine is good at.

**This is why the experiment needed running.** Someone looking only at the
`title` row would conclude Fabric can serve the API. The summary row, the 50 RPS
error rates, and §5 are what prevent that mistake.

## 4. Data freshness makes it unusable regardless of latency

Even if every latency number were acceptable, the data is **stale by design**:

| | Measured |
| --- | ---: |
| Operational write → visible in Fabric | **107 s** |

An operational API cannot serve reads that lag writes by a minute or more. There
is no read-your-writes guarantee, and the lag is the sum of the extractor cadence,
the Fabric replicator merge, and SQL-endpoint metadata sync — none of which the
API controls. See [FABRIC_ANALYTICS.md](FABRIC_ANALYTICS.md) §8.

## 5. Why this is not a recommendation, independent of the benchmark

Even had it passed 50 RPS cleanly, these remain true:

1. **It is read-only.** The mirrored Delta tables cannot be written. Every write
   still goes to the operational store, so Fabric can only ever be a *second*
   copy — added complexity, not reduced. `FabricOrderRepository`'s write methods
   raise `NotImplementedError` for exactly this reason, and that is the honest
   implementation.
2. **Capacity is shared.** The same F-SKU CUs serve every notebook, pipeline,
   semantic model and report in the tenant. An operational API would compete with
   analytics for the same budget, and a heavy Spark job would become an API
   incident.
3. **No operational SLO exists.** Nothing to design an availability target
   against.
4. **Throttling is by design.** Capacity smoothing and rejection are correct
   behaviour for analytics and unacceptable for a synchronous API.
5. **It inverts the dependency.** The operational API would depend on the
   analytics platform being healthy — the opposite of what a layered architecture
   wants.

## 6. Conclusion

**Direct Fabric/Delta serving offers no compelling advantage over Azure SQL or
Cosmos DB for this operational API, and several disqualifying disadvantages.**

It is:

- **26x slower** than Azure SQL on the most common operation
- **failing 31–48% of requests** at the customer's stated 50 RPS
- **~107 s stale**
- **read-only**, so it cannot replace the operational store
- **without a published concurrency limit or latency SLO**

The recommendation is unchanged: **serve operationally from Azure SQL (or Cosmos
DB), and use Fabric for analytics** — which is precisely what
[FABRIC_ANALYTICS.md](FABRIC_ANALYTICS.md) demonstrates works well for both
paths.

The one legitimate use of this repository class is **bulk export / bulk analytical
retrieval** where a minute of staleness is fine and scan throughput is the goal.
`title` at 19.3 MB/s and `full` at 34 MB/s show the engine is capable there. That
is a reporting path, not a serving path.

---

### Reproducing

```bash
# on the API VM
STORAGE_BACKEND=fabric FABRIC_SQL_ENDPOINT=<...>.datawarehouse.fabric.microsoft.com \
  FABRIC_SQL_DATABASE=mir_sql_orders bash scripts/vm_api.sh start fabric

# from the load VM
for W in summary title full; do
  for R in 10 25 50; do
    python loadtests/operational/run_load.py --base-url http://<api-host>:8000 \
      --backend fabric --workload $W --rps $R --duration 45 --warmup 10 \
      --note 'CONTROL: direct Fabric/Delta serving'
  done
done
```

Machine-readable results: [`results/fabric/`](../results/fabric/).
