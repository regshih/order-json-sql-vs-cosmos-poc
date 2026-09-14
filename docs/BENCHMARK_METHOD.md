# Benchmark method

How every number in [BENCHMARK_SUMMARY.md](../results/BENCHMARK_SUMMARY.md) was
produced, and what it does and does not mean.

Harness: [`loadtests/operational/run_load.py`](../loadtests/operational/run_load.py) ·
Writes: [`loadtests/operational/run_write_bench.py`](../loadtests/operational/run_write_bench.py) ·
Sweep: [`scripts/run_benchmarks.sh`](../scripts/run_benchmarks.sh) ·
Report generator: [`tools/summarize_benchmarks.py`](../tools/summarize_benchmarks.py)

---

## 1. Topology

```
vm-orderjsonpoc-load  (D4s_v5, 4 vCPU)   ──HTTP──▶  vm-orderjsonpoc-api  (D8s_v5, 8 vCPU)
        load generator                                 FastAPI, 8 uvicorn workers
                                                              │
                                              private endpoints (10.60.2.0/24)
                                                              ▼
                                            Azure SQL  /  Cosmos DB  /  ADLS Gen2
```

Both VMs sit in `snet-compute` on the same VNet, in the same region as the data
stores, with accelerated networking enabled.

**Why a separate load VM.** Workload C pushes multi-megabyte responses at 50
RPS. Co-locating the generator would send that over loopback at memory speed
(hiding the network entirely) and would make the generator's CPU compete with
the API's. Two VMs means the payload crosses a real NIC and the two CPU budgets
are independent.

**Why in Azure rather than from a workstation.** 50 RPS × a mean 1.3 MB response
is ~65 MB/s sustained, and the p99 order is ~5 MB. That is not measurable over a
consumer internet link — the link would be the result. Tenant policy forced
private-only endpoints, which pushed the benchmark into the VNet; that made the
numbers better founded, not worse.

## 2. Open-model load generation

The generator issues requests on a **fixed schedule** (constant arrival rate),
not from a pool of looping workers.

This matters more than it sounds. A closed-model generator (N workers, each
looping "send → wait → send") silently *reduces offered load* when the system
slows down: if the server takes twice as long, the generator sends half as many
requests, and the measured latency looks flat while the system is actually
failing. That is **coordinated omission**, and it is the standard way a load test
produces reassuring nonsense.

Two consequences of the open model here:

1. Requests are dispatched at `start + n × (1/rps)` regardless of whether earlier
   ones have completed.
2. Latency is recorded **scheduled-to-complete**, not dispatched-to-complete, so
   queueing delay appears in the number. `queueMs` is reported separately, and a
   rising `queueMs` p95 is the signal that the system is past its capacity.

A `--max-inflight` semaphore (default 256) is a safety valve, not a throttle: if
it is reached, the system is past its practical limit and the run is reported as
such rather than silently degrading into a closed model.

## 3. Run structure

| Phase | Duration | Counted? |
| --- | --- | --- |
| Warmup | 15 s at the target rate | **No** — fills connection pools, warms uvicorn workers, wakes a serverless SQL database, and lets Cosmos establish its routing table |
| Metrics reset | — | server-side counters cleared after warmup |
| Steady state | 60 s at the target rate | **Yes** |

`--repeats` runs the steady state multiple times and reports the mean of each
percentile; the sweep uses a single repeat per point for time reasons, and
the full point-by-point data is in the per-run JSON.

## 4. Throughput points (§15)

`10, 25, 50, 100, 200` RPS. **50 RPS is the customer's stated rate** and is
flagged in the summary table. 100 and 200 exist to show headroom or find the
knee, not because the customer asked for them.

No latency SLA is asserted anywhere. The brief explicitly says not to invent
one, so the reports state what happened and leave the acceptability judgement to
the customer.

## 5. Workload shapes (§16)

| Shape | Mix | What it isolates |
| --- | --- | --- |
| `summary` | 100% `/summary` | the cheapest read; index/point-read latency with no payload |
| `title` | 100% `/title` | a single business block, ~300 KB |
| `cdf` | 100% `/cdf` | a single business block, ~240 KB |
| `full` | 100% `/{id}` | **the payload-size stress test**, 0.5–5 MB per response |
| `search` | 100% `/orders?…` | relational search vs Cosmos query |
| `mix` | 50/20/15/10/5 summary/title/cdf/checklist/full | the realistic default |

The mix weights are configurable in `WORKLOADS`; the default matches §16 of the
brief.

## 6. What is measured

### Client side (per request)

`latencyMs` (scheduled-to-complete), `queueMs`, HTTP status, response bytes,
and the payload-size bucket the target order falls into.

### Server side (per request, from the API's own telemetry)

| Metric | Meaning |
| --- | --- |
| `db_ms` | time inside the database driver |
| `reconstruct_ms` | turning stored blocks/items back into one document |
| `serialize_ms` | encoding the response with orjson |
| `response_bytes` | encoded size |
| `request_charge` | **Cosmos RU, read from the SDK response header** — never estimated |
| `throttled_429`, `retries` | Cosmos throttling |
| `sql_queries`, `sql_pool_checkout_ms` | SQL query count and pool contention |

### Resource utilisation

- **SQL**: `sys.dm_db_resource_stats` — CPU %, data IO %, log write %, worker %,
  session %, memory %, and the active connection count. Read from the service's
  own DMV rather than inferred from the client.
- **Application**: process and system CPU %, RSS, thread count via `psutil`.
- **Cosmos**: RU is the utilisation metric; the container's throughput and
  indexing policy are captured alongside each run.

## 7. Latency vs payload size

Full-order runs bucket results by the target order's actual payload size
(`<0.75MB`, `0.75–1.25MB`, `1.25–2MB`, `2–4MB`, `>4MB`) and report percentiles per
bucket. This is what answers "what happens to network/serialization performance
for multi-megabyte responses" with data rather than intuition, because the
dataset deliberately contains a realistic spread rather than uniform documents.

## 8. The dataset

- **500 orders**, seed 42, 8 synthetic customers — **byte-identical in both
  backends**, because both are loaded from the same deterministic generator and
  decomposed by the same splitter.
- Measured distribution: min 508 KB, median 990 KB, mean 1.32 MB, p90 2.11 MB,
  p95 3.19 MB, p99 5.08 MB, max 5.09 MB (compact UTF-8).
- 17,026 JSON blocks in SQL; 17,526 Cosmos items (blocks + one header per order).
- Order ids are stable across regenerations, so re-ingesting upserts rather than
  duplicating.

Load targets are drawn from `/_bench/orders`, so the generator hits **real stored
orders** with a realistic size spread, not a synthetic uniform id space.

## 9. Write benchmark (§17)

Run separately from reads, directly against the repositories rather than through
HTTP — the question is what the storage engine costs per write, not what FastAPI
adds.

| Shape | What changes |
| --- | --- |
| `header` | scalar order-header fields (Status, Balance) |
| `title` | one TITLE business block replaced with a realistic payload |
| `cdf` | one CDF business block replaced |
| `version` | a complete new order version (full re-ingest) |

Rates: 1, 5, 10 writes/sec, each for 30 s. Replacement payloads come from freshly
generated orders so they are the right shape and size, not stubs.

## 10. Reproducibility

- Every run writes a self-describing JSON file to `results/{backend}/` containing
  its own configuration, the client-side percentiles, the per-operation and
  per-size breakdowns, and a snapshot of the server-side metrics.
- `tools/summarize_benchmarks.py` generates `BENCHMARK_SUMMARY.md` and three CSVs
  **entirely from those files**. No figure is typed into a report by hand.
- The generator seed, order count and customer count are recorded in each file,
  so the exact dataset can be regenerated.

## 11. Known limitations

Stated plainly, because they bound what the numbers can support:

1. **Single region, single replica.** No multi-region read routing, no read
   scale-out (General Purpose does not offer it — see [SOURCES.md](SOURCES.md)).
2. **60-second steady state, one repeat per point.** Long enough for stable
   percentiles at these rates, too short to expose slow-burn effects such as
   index fragmentation, statistics drift, or serverless auto-pause behaviour.
3. **500 orders (~660 MB).** Enough to exercise the access paths and to make
   buffer-pool behaviour realistic at this scale, but far below the two-year
   volume modelled in [RETENTION_ANALYSIS.md](RETENTION_ANALYSIS.md). SQL results
   in particular would change once the working set exceeds memory.
4. **Cosmos autoscale ceiling fixed at 4,000 RU/s.** No search for the minimum
   viable throughput.
5. **The Cosmos lookup tax is included.** Every Cosmos read pays a
   cross-partition lookup because the API contract has no tenant in the route
   (see [COSMOS_DESIGN.md](COSMOS_DESIGN.md) §5). This is a faithful measurement
   of *the specified contract*, and it understates what Cosmos could do with a
   tenant-scoped route.
6. **No client-side caching, no CDN, no conditional requests.** Every request is
   a full round trip to the database.
7. **The synthetic corpus is denser than the real sample.** The real order is
   32.8% empty strings; the generator produces ~15%. The sizes and array
   cardinalities match, so the bytes moved are right, but each generated byte
   carries slightly more real content than a customer byte would. This makes the
   benchmark **conservative** � a real corpus of the same size would contain more
   cheap-to-serialise empty values � and it is left uncorrected because
   re-tuning it would invalidate the calibrated size profiles and require
   re-running every measurement.
