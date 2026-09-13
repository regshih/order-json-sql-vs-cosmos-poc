# Two-Year Retention Analysis

Generated 2026-09-13T19:14:20.096552+00:00 by [tools/retention_model.py](../tools/retention_model.py).

## What is known vs assumed

| Input | Value | Basis |
| --- | --- | --- |
| Retention horizon | 730 days (~2 years) | **STATED** by the customer |
| API read rate | ~50 requests/sec | **STATED** by the customer |
| Mean payload (compact) | 1,316,797 bytes | **MEASURED** — 200 generated orders |
| Median / p95 / max payload | 990,078 / 3,193,248 / 5,092,224 bytes | **MEASURED** |
| JSON blocks per order | 31.0 | **MEASURED** (fallback default) |
| Cosmos payload items per order | 31.0 (+1 header) | **MEASURED** |
| gzip ratio in the raw archive | ~9% of original | **MEASURED** (archive layer) |
| **Orders per day** | varies by scenario | **ASSUMPTION** |
| **Versions per order** | varies by scenario | **ASSUMPTION** |
| **Updates per order per day** | varies by scenario | **ASSUMPTION** |
| Hot (operational) retention | 180 days | **ASSUMPTION** — design proposal |
| Distinct customers/tenants | 8 | **ASSUMPTION** — POC scale |

> The version/update rate is the single biggest unknown in this model and the
> one input the customer must supply before any sizing is committed. Every row
> below marked ASSUMPTION moves proportionally with it.

## Scenarios

| Scenario | Orders/day | Versions/order | Total orders (2y) | Total versions (2y) |
| --- | ---: | ---: | ---: | ---: |
| low | 500 | 4.0 | 365,000 | 1,460,000 |
| medium | 2,000 | 12.0 | 1,460,000 | 17,520,000 |
| high | 5,000 | 25.0 | 3,650,000 | 91,250,000 |
| observed-sample | 2,000 | 59.0 | 1,460,000 | 86,140,000 |

### Operational (hot) store

Holds the **current version only**, for orders opened in the last 180 days.

| Scenario | Hot orders | Hot bytes | SQL `OrderJsonBlocks` rows | SQL total rows | Cosmos items |
| --- | ---: | ---: | ---: | ---: | ---: |
| low | 90,000 | 110.4 GiB | 2,790,000 | 5,193,000 | 2,880,000 |
| medium | 360,000 | 441.5 GiB | 11,160,000 | 23,652,000 | 11,520,000 |
| high | 900,000 | 1.08 TiB | 27,900,000 | 70,830,000 | 28,800,000 |
| observed-sample | 360,000 | 441.5 GiB | 11,160,000 | 40,572,000 | 11,520,000 |

### Raw archive (every version, immutable)

| Scenario | Archived versions | Uncompressed | Compressed (gzip) |
| --- | ---: | ---: | ---: |
| low | 1,460,000 | 1.75 TiB | 161.1 GiB |
| medium | 17,520,000 | 20.98 TiB | 1.89 TiB |
| high | 91,250,000 | 109.28 TiB | 9.84 TiB |
| observed-sample | 86,140,000 | 103.16 TiB | 9.28 TiB |

### Growth rate

| Scenario | Hot GiB/month | Archive GiB/month (compressed) |
| --- | ---: | ---: |
| low | 18.4 | 6.6 |
| medium | 73.6 | 79.5 |
| high | 184.0 | 413.9 |
| observed-sample | 73.6 | 390.7 |

## Cosmos logical partition growth — the customer's stated problem

The customer reported logical-partition growth trouble with their current
design. The hierarchical key chosen here (`/customerId`, `/orderId`) makes the
logical partition **one order**, so it is bounded by the size of a single order
and cannot grow with tenant size or with elapsed time.

| Scenario | Bytes per logical partition | % of 20 GiB limit | Order versions before limit |
| --- | ---: | ---: | ---: |
| low | 1,316,797 | 0.0061% | 16,308 |
| medium | 1,316,797 | 0.0061% | 16,308 |
| high | 1,316,797 | 0.0061% | 16,308 |
| observed-sample | 1,316,797 | 0.0061% | 16,308 |

Contrast with a **single-level `/customerId`** partition key, which is the shape
that produces unbounded partition growth:

| Scenario | Orders/customer over 2y | Bytes in one customer partition | % of 20 GiB limit | Exceeds? |
| --- | ---: | ---: | ---: | --- |
| low | 45,625 | 56.0 GiB | 279.8% | **YES — hard failure** |
| medium | 182,500 | 223.8 GiB | 1,119.1% | **YES — hard failure** |
| high | 456,250 | 559.5 GiB | 2,797.6% | **YES — hard failure** |
| observed-sample | 182,500 | 223.8 GiB | 1,119.1% | **YES — hard failure** |

## Conclusions

1. **Hot vs archive separation is the load-bearing decision**, not the choice of
   SQL vs Cosmos. Keeping only the current version of recent orders in the
   operational store keeps it one to two orders of magnitude smaller than the
   full two-year version history.
2. **Version history belongs in the raw archive.** It compresses to ~9% of its
   original size there, is billed at blob rates rather than database rates, and
   is never on the API read path.
3. **A hierarchical `/customerId` + `/orderId` key removes the partition-growth
   failure mode entirely** — the logical partition is one order, a fixed small
   fraction of the 20 GiB limit, regardless of tenant size or retention.
4. **The version rate must come from the customer.** Under the scenarios above
   the archive differs by more than 6x. That is a procurement-relevant range.

