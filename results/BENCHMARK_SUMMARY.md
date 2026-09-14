# Benchmark Summary

Generated 2026-09-14T05:14:18.359579+00:00 by [tools/summarize_benchmarks.py](../tools/summarize_benchmarks.py) from the
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
| sql | 10 | 10.0 | 9.4 | 48.8 | 152.4 | 478.5 | 0.00 | 2.28 | 0 |
| sql | 25 | 25.0 | 9.0 | 41.6 | 118.0 | 480.7 | 0.00 | 5.84 | 0 |
| sql ⬅ | 50 | 50.0 | 9.0 | 38.7 | 113.5 | 1,014.6 | 0.00 | 11.68 | 0 |
| sql | 100 | 100.0 | 8.2 | 28.6 | 99.8 | 1,206.0 | 0.00 | 22.72 | 0 |
| sql | 200 | 199.8 | 8.4 | 30.1 | 59.3 | 286.5 | 0.00 | 44.66 | 0 |

## Workload shapes at 50 RPS

| Workload | Backend | Achieved RPS | p50 ms | p95 ms | p99 ms | Mean resp bytes | MB/s | RU/req | Errors % |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| cdf | sql | 50.0 | 11.3 | 26.9 | 42.2 | 455,884 | 21.73 | — | 0.00 |
| full | sql | 50.0 | 37.5 | 110.7 | 169.3 | 1,413,635 | 67.36 | — | 0.00 |
| search | sql | 50.0 | 6.9 | 8.7 | 9.9 | 8,878 | 0.42 | — | 0.00 |
| summary | sql | 50.0 | 8.2 | 9.8 | 11.4 | 753 | 0.04 | — | 0.00 |
| title | sql | 50.0 | 10.9 | 19.2 | 27.2 | 428,796 | 20.44 | — | 0.00 |

## Workload C — full multi-megabyte orders

This is the payload-size stress test. At 50 RPS a mean ~1.3 MB response is
~65 MB/s of JSON leaving the API; the p99 order is ~5 MB.

| Backend | Target RPS | Achieved RPS | p50 ms | p95 ms | p99 ms | Mean bytes | MB/s | Errors % | queue p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| sql | 10 | 10.0 | 29.2 | 105.9 | 140.7 | 1,442,446 | 13.75 | 0.00 | 1.8 |
| sql | 25 | 25.0 | 26.3 | 83.9 | 114.4 | 1,444,732 | 34.40 | 0.00 | 1.9 |
| sql | 50 | 50.0 | 37.5 | 110.7 | 169.3 | 1,413,635 | 67.36 | 0.00 | 1.7 |

## Latency vs payload size (full-order reads)

| Backend | RPS | Payload bucket | n | p50 ms | p95 ms | p99 ms | Mean bytes |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| sql | 10 | <0.75MB | 5 | 16.4 | 72.7 | 83.3 | 515,342 |
| sql | 10 | <0.75MB | 136 | 16.2 | 49.5 | 66.4 | 515,455 |
| sql | 10 | 0.75-1.25MB | 11 | 22.2 | 108.0 | 142.9 | 986,399 |
| sql | 10 | 0.75-1.25MB | 213 | 22.7 | 93.4 | 112.3 | 986,375 |
| sql | 10 | 1.25-2MB | 8 | 37.8 | 306.6 | 342.6 | 1,574,499 |
| sql | 10 | 1.25-2MB | 104 | 31.4 | 111.6 | 135.4 | 1,573,761 |
| sql | 10 | 2-4MB | 10 | 47.7 | 398.1 | 416.0 | 2,325,706 |
| sql | 10 | 2-4MB | 123 | 41.7 | 140.6 | 180.5 | 2,434,811 |
| sql | 10 | >4MB | 24 | 84.2 | 109.8 | 114.0 | 5,088,129 |
| sql | 25 | <0.75MB | 15 | 16.0 | 65.4 | 107.0 | 516,222 |
| sql | 25 | <0.75MB | 319 | 16.6 | 27.4 | 66.2 | 515,381 |
| sql | 25 | 0.75-1.25MB | 22 | 22.2 | 120.1 | 201.3 | 986,977 |
| sql | 25 | 0.75-1.25MB | 511 | 23.1 | 42.4 | 93.2 | 986,858 |
| sql | 25 | 1.25-2MB | 16 | 31.8 | 238.8 | 239.8 | 1,574,685 |
| sql | 25 | 1.25-2MB | 321 | 31.4 | 64.3 | 132.1 | 1,573,736 |
| sql | 25 | 2-4MB | 23 | 38.1 | 54.2 | 55.0 | 2,392,716 |
| sql | 25 | 2-4MB | 294 | 40.6 | 76.6 | 104.0 | 2,426,349 |
| sql | 25 | >4MB | 3 | 87.6 | 441.4 | 472.8 | 5,090,045 |
| sql | 25 | >4MB | 55 | 87.9 | 127.4 | 184.2 | 5,088,919 |
| sql | 50 | <0.75MB | 32 | 16.5 | 112.8 | 227.1 | 515,703 |
| sql | 50 | <0.75MB | 632 | 23.3 | 63.9 | 91.3 | 515,041 |
| sql | 50 | 0.75-1.25MB | 48 | 24.0 | 158.8 | 572.9 | 986,715 |
| sql | 50 | 0.75-1.25MB | 1,051 | 30.5 | 78.7 | 111.6 | 986,905 |
| sql | 50 | 1.25-2MB | 32 | 31.7 | 256.2 | 785.0 | 1,574,883 |
| sql | 50 | 1.25-2MB | 651 | 41.5 | 100.1 | 137.2 | 1,573,700 |
| sql | 50 | 2-4MB | 42 | 48.6 | 71.5 | 499.6 | 2,472,461 |
| sql | 50 | 2-4MB | 578 | 62.3 | 127.9 | 153.7 | 2,432,210 |
| sql | 50 | >4MB | 5 | 92.2 | 118.5 | 118.5 | 5,092,098 |
| sql | 50 | >4MB | 88 | 127.8 | 203.2 | 223.1 | 5,089,389 |
| sql | 100 | <0.75MB | 52 | 14.5 | 69.8 | 122.0 | 515,525 |
| sql | 100 | 0.75-1.25MB | 101 | 22.8 | 93.0 | 920.5 | 987,135 |
| sql | 100 | 1.25-2MB | 59 | 31.6 | 156.2 | 753.8 | 1,574,100 |
| sql | 100 | 2-4MB | 74 | 48.3 | 238.3 | 647.0 | 2,461,682 |
| sql | 100 | >4MB | 8 | 92.6 | 201.1 | 232.5 | 5,090,534 |
| sql | 200 | <0.75MB | 98 | 16.5 | 50.4 | 63.1 | 515,515 |
| sql | 200 | 0.75-1.25MB | 206 | 23.7 | 72.2 | 102.7 | 987,016 |
| sql | 200 | 1.25-2MB | 111 | 33.7 | 91.1 | 101.4 | 1,573,991 |
| sql | 200 | 2-4MB | 137 | 53.3 | 123.7 | 210.2 | 2,474,356 |
| sql | 200 | >4MB | 10 | 95.8 | 137.4 | 141.1 | 5,091,546 |

## Where the time goes (server-side, p50)

| Backend | Workload | DB ms | Reconstruct ms | Serialize ms | Client p50 ms | App CPU % | SQL CPU % | SQL IO % |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| sql | full | 0.00 | 0.00 | 0.00 | 37.5 | 1.2 | 28.15 | 8.69 |
| sql | mix | 4.45 | 2.26 | 0.17 | 9.0 | 0.0 | 7.61 | 13.22 |

**Reconstruction overhead** is the `Reconstruct ms` column: the cost of turning
stored blocks/items back into one order document. It is the price both designs
pay for decomposing the order, and it is directly comparable between them.

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

