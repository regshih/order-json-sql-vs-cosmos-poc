# Benchmark Summary

Generated 2026-09-14T05:33:59.235309+00:00 by [tools/summarize_benchmarks.py](../tools/summarize_benchmarks.py) from the
machine-readable run files in `results/`. **No figure in this document was
typed by hand.**

## Method

- Load generated with an **open-model (constant arrival rate)** harness: requests
  are issued on a fixed schedule regardless of whether earlier ones completed, so
  queueing delay appears in the latency figures instead of being hidden by
  coordinated omission. Latency is measured **scheduled-to-complete**.
- The load generator runs on a **separate VM** from the API, inside the same VNet,
  so multi-megabyte responses cross a real network boundary.
- Each run has a warmup phase that is discarded, then a measured steady state.
- Server-side `db` / `reconstruct` / `serialize` splits and Cosmos RU come from
  the API's own telemetry, not from the client.

## Read throughput sweep — realistic mix

Mix: 50% summary, 20% title, 15% CDF, 10% checklist, 5% full order.
**50 RPS is the customer's stated operating point.**

| Backend | Target RPS | Achieved RPS | p50 ms | p95 ms | p99 ms | max ms | Errors % | MB/s | 429s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| cosmos | 10 | 10.0 | 39.9 | 92.7 | 155.6 | 246.8 | 0.00 | 2.24 | 0 |
| cosmos | 25 | 25.0 | 37.4 | 85.2 | 109.2 | 261.1 | 0.00 | 5.37 | 0 |
| cosmos ⬅ | 50 | 49.9 | 39.5 | 88.8 | 123.0 | 241.1 | 0.00 | 11.23 | 0 |
| cosmos | 100 | 99.9 | 45.2 | 98.7 | 136.2 | 282.5 | 0.00 | 22.85 | 0 |
| cosmos | 200 | 199.3 | 76.1 | 199.1 | 295.5 | 999.5 | 0.00 | 43.86 | 0 |
| sql | 10 | 10.0 | 9.4 | 48.8 | 152.4 | 478.5 | 0.00 | 2.28 | 0 |
| sql | 25 | 25.0 | 9.0 | 41.6 | 118.0 | 480.7 | 0.00 | 5.84 | 0 |
| sql ⬅ | 50 | 50.0 | 9.0 | 38.7 | 113.5 | 1,014.6 | 0.00 | 11.68 | 0 |
| sql | 100 | 100.0 | 8.2 | 28.6 | 99.8 | 1,206.0 | 0.00 | 22.72 | 0 |
| sql | 200 | 199.8 | 8.4 | 30.1 | 59.3 | 286.5 | 0.00 | 44.66 | 0 |

## Workload shapes at 50 RPS

| Workload | Backend | Achieved RPS | p50 ms | p95 ms | p99 ms | Mean resp bytes | MB/s | RU/req | Errors % |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| cdf | cosmos | 49.9 | 68.0 | 107.8 | 167.3 | 471,526 | 22.45 | 99.61 | 0.00 |
| cdf | sql | 50.0 | 11.3 | 26.9 | 42.2 | 455,884 | 21.73 | — | 0.00 |
| full | cosmos | 49.9 | 110.4 | 194.3 | 251.3 | 1,464,274 | 69.62 | 236.17 | 0.00 |
| full | sql | 50.0 | 37.5 | 110.7 | 169.3 | 1,413,635 | 67.36 | — | 0.00 |
| search | cosmos | 50.0 | 30.1 | 76.7 | 116.0 | 8,574 | 0.41 | 166.05 | 0.00 |
| search | sql | 50.0 | 6.9 | 8.7 | 9.9 | 8,878 | 0.42 | — | 0.00 |
| summary | cosmos | 50.0 | 33.9 | 37.1 | 40.1 | 753 | 0.04 | 47.71 | 0.00 |
| summary | sql | 50.0 | 8.2 | 9.8 | 11.4 | 753 | 0.04 | — | 0.00 |
| title | cosmos | 50.0 | 56.4 | 77.5 | 105.5 | 440,943 | 21.00 | 56.05 | 0.00 |
| title | sql | 50.0 | 10.9 | 19.2 | 27.2 | 428,796 | 20.44 | — | 0.00 |

## Workload C — full multi-megabyte orders

This is the payload-size stress test. At 50 RPS a mean ~1.3 MB response is
~65 MB/s of JSON leaving the API; the p99 order is ~5 MB.

| Backend | Target RPS | Achieved RPS | p50 ms | p95 ms | p99 ms | Mean bytes | MB/s | Errors % | queue p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| cosmos | 10 | 9.2 | 99.4 | 3,259.4 | 7,245.1 | 1,426,285 | 12.53 | 0.22 | 1.8 |
| cosmos | 25 | 25.0 | 101.1 | 164.3 | 221.9 | 1,429,557 | 34.03 | 0.00 | 1.8 |
| cosmos | 50 | 49.9 | 110.4 | 194.3 | 251.3 | 1,464,274 | 69.62 | 0.00 | 1.6 |
| sql | 10 | 10.0 | 29.2 | 105.9 | 140.7 | 1,442,446 | 13.75 | 0.00 | 1.8 |
| sql | 25 | 25.0 | 26.3 | 83.9 | 114.4 | 1,444,732 | 34.40 | 0.00 | 1.9 |
| sql | 50 | 50.0 | 37.5 | 110.7 | 169.3 | 1,413,635 | 67.36 | 0.00 | 1.7 |

## Latency vs payload size (full-order reads)

Only the `full` workload is shown: the `mix` workload also returns whole
orders (5% of its requests) but with too few samples per bucket to be
meaningful, and mixing the two made identical-looking duplicate rows.

| Backend | Workload | RPS | Payload bucket | n | p50 ms | p95 ms | p99 ms | Mean bytes |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| cosmos | full | 10 | <0.75MB | 97 | 82.1 | 3,307.3 | 7,249.6 | 514,918 |
| cosmos | full | 10 | 0.75-1.25MB | 150 | 90.4 | 1,233.5 | 3,277.2 | 986,931 |
| cosmos | full | 10 | 1.25-2MB | 95 | 103.1 | 1,866.3 | 7,237.4 | 1,573,979 |
| cosmos | full | 10 | 2-4MB | 95 | 124.5 | 3,350.9 | 7,267.0 | 2,440,202 |
| cosmos | full | 10 | >4MB | 12 | 191.3 | 7,267.4 | 7,317.4 | 5,088,991 |
| cosmos | full | 25 | <0.75MB | 223 | 86.5 | 134.4 | 151.8 | 515,232 |
| cosmos | full | 25 | 0.75-1.25MB | 396 | 96.1 | 134.7 | 174.0 | 987,123 |
| cosmos | full | 25 | 1.25-2MB | 246 | 106.2 | 139.9 | 187.5 | 1,573,805 |
| cosmos | full | 25 | 2-4MB | 228 | 122.1 | 165.1 | 190.1 | 2,422,984 |
| cosmos | full | 25 | >4MB | 32 | 198.2 | 255.4 | 262.7 | 5,089,303 |
| cosmos | full | 50 | <0.75MB | 437 | 92.3 | 149.2 | 218.8 | 514,978 |
| cosmos | full | 50 | 0.75-1.25MB | 817 | 103.6 | 158.8 | 204.2 | 987,128 |
| cosmos | full | 50 | 1.25-2MB | 458 | 113.1 | 185.7 | 258.0 | 1,573,810 |
| cosmos | full | 50 | 2-4MB | 457 | 131.8 | 191.7 | 241.3 | 2,472,669 |
| cosmos | full | 50 | >4MB | 81 | 207.2 | 266.1 | 349.7 | 5,089,789 |
| sql | full | 10 | <0.75MB | 136 | 16.2 | 49.5 | 66.4 | 515,455 |
| sql | full | 10 | 0.75-1.25MB | 213 | 22.7 | 93.4 | 112.3 | 986,375 |
| sql | full | 10 | 1.25-2MB | 104 | 31.4 | 111.6 | 135.4 | 1,573,761 |
| sql | full | 10 | 2-4MB | 123 | 41.7 | 140.6 | 180.5 | 2,434,811 |
| sql | full | 10 | >4MB | 24 | 84.2 | 109.8 | 114.0 | 5,088,129 |
| sql | full | 25 | <0.75MB | 319 | 16.6 | 27.4 | 66.2 | 515,381 |
| sql | full | 25 | 0.75-1.25MB | 511 | 23.1 | 42.4 | 93.2 | 986,858 |
| sql | full | 25 | 1.25-2MB | 321 | 31.4 | 64.3 | 132.1 | 1,573,736 |
| sql | full | 25 | 2-4MB | 294 | 40.6 | 76.6 | 104.0 | 2,426,349 |
| sql | full | 25 | >4MB | 55 | 87.9 | 127.4 | 184.2 | 5,088,919 |
| sql | full | 50 | <0.75MB | 632 | 23.3 | 63.9 | 91.3 | 515,041 |
| sql | full | 50 | 0.75-1.25MB | 1,051 | 30.5 | 78.7 | 111.6 | 986,905 |
| sql | full | 50 | 1.25-2MB | 651 | 41.5 | 100.1 | 137.2 | 1,573,700 |
| sql | full | 50 | 2-4MB | 578 | 62.3 | 127.9 | 153.7 | 2,432,210 |
| sql | full | 50 | >4MB | 88 | 127.8 | 203.2 | 223.1 | 5,089,389 |

The full set, including the `mix` contributions, is in
[`summary-by-size.csv`](summary-by-size.csv).

## Where the time goes (server-side, p50)

| Backend | Workload | DB ms | Reconstruct ms | Serialize ms | Client p50 ms | App CPU % | SQL CPU % | SQL IO % |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| cosmos | full | 109.32 | 0.13 | 1.31 | 110.4 | 0.0 | — | — |
| sql | full | 0.00 | 0.00 | 0.00 | 37.5 | 1.2 | 28.15 | 8.69 |
| cosmos | mix | 38.19 | 0.03 | 0.20 | 39.5 | 0.0 | — | — |
| sql | mix | 4.45 | 2.26 | 0.17 | 9.0 | 0.0 | 7.61 | 13.22 |

**Reconstruction overhead** is the `Reconstruct ms` column: the cost of turning
stored blocks/items back into one order document. It is the price both designs
pay for decomposing the order, and it is directly comparable between them.

## Cosmos RU per operation (INDICATIVE - sampled from one worker)

> These come from the API's in-process telemetry under an 8-worker
> uvicorn process and are **not** authoritative: a single-workload run
> can show operations from the previous run because the reset and the
> read hit different workers. Shown for shape, not for costing.
>
> **The cost model uses the single-process measurements in**
> `results/cosmos/index-impact.json` **instead.**

| Operation | Requests | RU/request (mean) | RU p95 (max seen) | DB p50 ms | 429s |
| --- | ---: | ---: | ---: | ---: | ---: |
| `full` | 3,678 | 583.52 | 1,175.05 | 10,485.0 | 0 |
| `cdf` | 4,506 | 149.50 | 368.92 | 1,272.0 | 0 |
| `title` | 7,511 | 127.98 | 356.20 | 850.2 | 0 |
| `checklist` | 1,763 | 22.00 | 23.12 | 47.5 | 0 |
| `summary` | 12,811 | 7.28 | 7.76 | 34.9 | 0 |
| `search` | 628 | 4.34 | 5.30 | 26.8 | 0 |

## Measured Cosmos RU

| Workload | Target RPS | RU per request | RU/sec at this rate | 429s |
| --- | ---: | ---: | ---: | ---: |
| cdf | 50 | 99.61 | 4,974 | 0 |
| full | 10 | 47.25 | 436 | 0 |
| full | 25 | 269.15 | 6,718 | 0 |
| full | 50 | 236.17 | 11,776 | 0 |
| mix | 10 | 58.94 | 589 | 0 |
| mix | 25 | 488.08 | 12,197 | 0 |
| mix | 50 | 58.88 | 2,939 | 0 |
| mix | 100 | 241.15 | 24,088 | 0 |
| mix | 200 | 54.98 | 10,959 | 0 |
| search | 50 | 166.05 | 8,299 | 0 |
| summary | 50 | 47.71 | 2,384 | 0 |
| title | 50 | 56.05 | 2,800 | 0 |

Per-operation RU appears in [COST_ANALYSIS.md](../docs/COST_ANALYSIS.md), which
prices these measured values against live Azure retail rates.

## Source files

- `results/sql\run-001-mix-10rps.json` — sql mix @ 10 RPS
- `results/sql\run-002-mix-25rps.json` — sql mix @ 25 RPS
- `results/sql\run-003-mix-50rps.json` — sql mix @ 50 RPS
- `results/sql\run-004-mix-100rps.json` — sql mix @ 100 RPS
- `results/sql\run-005-mix-200rps.json` — sql mix @ 200 RPS
- `results/sql\run-006-summary-50rps.json` — sql summary @ 50 RPS
- `results/sql\run-007-title-50rps.json` — sql title @ 50 RPS
- `results/sql\run-008-cdf-50rps.json` — sql cdf @ 50 RPS
- `results/sql\run-009-search-50rps.json` — sql search @ 50 RPS
- `results/sql\run-010-full-10rps.json` — sql full @ 10 RPS
- `results/sql\run-011-full-25rps.json` — sql full @ 25 RPS
- `results/sql\run-012-full-50rps.json` — sql full @ 50 RPS
- `results/cosmos\run-012-mix-10rps.json` — cosmos mix @ 10 RPS
- `results/cosmos\run-013-mix-25rps.json` — cosmos mix @ 25 RPS
- `results/cosmos\run-014-mix-50rps.json` — cosmos mix @ 50 RPS
- `results/cosmos\run-015-mix-100rps.json` — cosmos mix @ 100 RPS
- `results/cosmos\run-016-mix-200rps.json` — cosmos mix @ 200 RPS
- `results/cosmos\run-017-summary-50rps.json` — cosmos summary @ 50 RPS
- `results/cosmos\run-018-title-50rps.json` — cosmos title @ 50 RPS
- `results/cosmos\run-019-cdf-50rps.json` — cosmos cdf @ 50 RPS
- `results/cosmos\run-020-search-50rps.json` — cosmos search @ 50 RPS
- `results/cosmos\run-021-full-10rps.json` — cosmos full @ 10 RPS
- `results/cosmos\run-022-full-25rps.json` — cosmos full @ 25 RPS
- `results/cosmos\run-023-full-50rps.json` — cosmos full @ 50 RPS

