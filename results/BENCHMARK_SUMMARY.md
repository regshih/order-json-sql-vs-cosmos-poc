# Benchmark Summary

Generated 2026-09-14T04:30:47.146167+00:00 by [tools/summarize_benchmarks.py](../tools/summarize_benchmarks.py) from the
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
| cosmos | 10 | 10.0 | 29.5 | 180.0 | 327.1 | 800.2 | 0.00 | 2.27 | 0 |
| cosmos | 25 | 23.1 | 1,111.1 | 5,681.8 | 8,874.0 | 14,569.1 | 0.53 | 5.20 | 3 |
| cosmos ⬅ | 50 | 23.0 | 34,289.0 | 67,225.8 | 72,481.8 | 81,181.5 | 9.63 | 5.27 | 31 |
| cosmos | 100 | 23.8 | 98,771.9 | 182,020.8 | 190,327.2 | 196,844.4 | 7.83 | 5.27 | 126 |
| cosmos | 200 | 24.1 | 222,389.9 | 415,912.2 | 434,856.0 | 439,036.5 | 6.67 | 5.30 | 450 |
| sql | 10 | 10.0 | 9.4 | 48.8 | 152.4 | 478.5 | 0.00 | 2.28 | 0 |
| sql | 25 | 25.0 | 9.0 | 41.6 | 118.0 | 480.7 | 0.00 | 5.84 | 0 |
| sql ⬅ | 50 | 50.0 | 9.0 | 38.7 | 113.5 | 1,014.6 | 0.00 | 11.68 | 0 |
| sql | 100 | 100.0 | 8.2 | 28.6 | 99.8 | 1,206.0 | 0.00 | 22.72 | 0 |
| sql | 200 | 199.8 | 8.4 | 30.1 | 59.3 | 286.5 | 0.00 | 44.66 | 0 |

## Workload shapes at 50 RPS

| Workload | Backend | Achieved RPS | p50 ms | p95 ms | p99 ms | Mean resp bytes | MB/s | RU/req | Errors % |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| cdf | cosmos | 12.7 | 87,670.1 | 168,721.4 | 177,038.2 | 457,756 | 5.53 | 245.70 | 0.00 |
| cdf | sql | 50.0 | 11.3 | 26.9 | 42.2 | 455,884 | 21.73 | — | 0.00 |
| full | sql | 50.0 | 37.5 | 110.7 | 169.3 | 1,413,635 | 67.36 | — | 0.00 |
| search | cosmos | 50.0 | 13.8 | 17.0 | 55.8 | 8,864 | 0.42 | 218.35 | 0.00 |
| search | sql | 50.0 | 6.9 | 8.7 | 9.9 | 8,878 | 0.42 | — | 0.00 |
| summary | cosmos | 50.0 | 16.8 | 19.2 | 21.3 | 753 | 0.04 | 180.46 | 0.00 |
| summary | sql | 50.0 | 8.2 | 9.8 | 11.4 | 753 | 0.04 | — | 0.00 |
| title | cosmos | 13.1 | 84,212.8 | 160,942.1 | 169,271.5 | 430,763 | 5.38 | 77.90 | 0.10 |
| title | sql | 50.0 | 10.9 | 19.2 | 27.2 | 428,796 | 20.44 | — | 0.00 |

## Workload C — full multi-megabyte orders

This is the payload-size stress test. At 50 RPS a mean ~1.3 MB response is
~65 MB/s of JSON leaving the API; the p99 order is ~5 MB.

| Backend | Target RPS | Achieved RPS | p50 ms | p95 ms | p99 ms | Mean bytes | MB/s | Errors % | queue p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| cosmos | 10 | 3.9 | 40,770.6 | 97,214.1 | 109,188.7 | 1,410,817 | 5.20 | 1.33 | 21,349.8 |
| cosmos | 25 | 4.0 | 147,372.0 | 297,173.1 | 316,520.4 | 1,385,873 | 4.77 | 8.60 | 232,709.7 |
| sql | 10 | 10.0 | 29.2 | 105.9 | 140.7 | 1,442,446 | 13.75 | 0.00 | 1.8 |
| sql | 25 | 25.0 | 26.3 | 83.9 | 114.4 | 1,444,732 | 34.40 | 0.00 | 1.9 |
| sql | 50 | 50.0 | 37.5 | 110.7 | 169.3 | 1,413,635 | 67.36 | 0.00 | 1.7 |

## Latency vs payload size (full-order reads)

| Backend | RPS | Payload bucket | n | p50 ms | p95 ms | p99 ms | Mean bytes |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| cosmos | 10 | <0.75MB | 6 | 157.2 | 168.4 | 168.5 | 514,107 |
| cosmos | 10 | <0.75MB | 140 | 43,838.9 | 98,390.7 | 107,135.0 | 515,038 |
| cosmos | 10 | 0.75-1.25MB | 10 | 173.5 | 320.7 | 384.3 | 985,708 |
| cosmos | 10 | 0.75-1.25MB | 199 | 35,176.1 | 94,889.5 | 106,367.1 | 987,101 |
| cosmos | 10 | 1.25-2MB | 11 | 186.6 | 265.5 | 267.9 | 1,575,634 |
| cosmos | 10 | 1.25-2MB | 114 | 40,476.4 | 98,330.7 | 108,311.8 | 1,573,918 |
| cosmos | 10 | 2-4MB | 6 | 199.2 | 259.7 | 269.8 | 2,287,932 |
| cosmos | 10 | 2-4MB | 122 | 34,638.0 | 93,785.7 | 96,948.4 | 2,464,920 |
| cosmos | 10 | >4MB | 1 | 252.2 | 252.2 | 252.2 | 5,095,574 |
| cosmos | 10 | >4MB | 17 | 84,811.5 | 111,464.7 | 116,669.2 | 5,089,316 |
| cosmos | 25 | <0.75MB | 20 | 1,472.8 | 5,795.6 | 8,257.8 | 514,134 |
| cosmos | 25 | <0.75MB | 292 | 139,724.0 | 298,040.3 | 317,016.3 | 514,696 |
| cosmos | 25 | 0.75-1.25MB | 19 | 1,799.0 | 8,316.2 | 9,392.7 | 986,469 |
| cosmos | 25 | 0.75-1.25MB | 475 | 148,292.9 | 300,081.7 | 313,071.0 | 987,093 |
| cosmos | 25 | 1.25-2MB | 20 | 909.6 | 4,005.9 | 8,452.4 | 1,575,864 |
| cosmos | 25 | 1.25-2MB | 308 | 153,294.0 | 292,453.2 | 314,340.9 | 1,573,667 |
| cosmos | 25 | 2-4MB | 18 | 1,016.8 | 4,841.9 | 5,692.1 | 2,227,517 |
| cosmos | 25 | 2-4MB | 266 | 138,314.2 | 294,465.6 | 316,353.1 | 2,418,992 |
| cosmos | 25 | >4MB | 2 | 1,215.3 | 1,620.7 | 1,656.7 | 5,092,256 |
| cosmos | 25 | >4MB | 30 | 174,649.5 | 315,059.5 | 322,949.8 | 5,091,003 |
| cosmos | 50 | <0.75MB | 33 | 23,821.0 | 61,880.9 | 68,031.2 | 514,369 |
| cosmos | 50 | 0.75-1.25MB | 53 | 41,110.9 | 66,322.5 | 69,630.8 | 986,636 |
| cosmos | 50 | 1.25-2MB | 35 | 26,591.8 | 67,526.6 | 75,212.7 | 1,574,910 |
| cosmos | 50 | 2-4MB | 35 | 39,980.2 | 65,045.6 | 69,100.0 | 2,356,346 |
| cosmos | 50 | >4MB | 3 | 44,305.4 | 55,451.3 | 56,442.0 | 5,091,150 |
| cosmos | 100 | <0.75MB | 59 | 77,400.6 | 186,138.0 | 190,043.0 | 514,526 |
| cosmos | 100 | 0.75-1.25MB | 106 | 102,965.9 | 177,336.4 | 191,149.1 | 986,837 |
| cosmos | 100 | 1.25-2MB | 58 | 78,902.7 | 184,327.6 | 186,992.1 | 1,573,764 |
| cosmos | 100 | 2-4MB | 62 | 86,465.7 | 176,322.9 | 191,508.4 | 2,389,382 |
| cosmos | 100 | >4MB | 8 | 134,447.5 | 182,878.6 | 186,192.5 | 5,088,741 |
| cosmos | 200 | <0.75MB | 104 | 196,191.2 | 414,882.7 | 433,747.1 | 514,709 |
| cosmos | 200 | 0.75-1.25MB | 205 | 214,818.6 | 422,896.3 | 432,614.9 | 987,372 |
| cosmos | 200 | 1.25-2MB | 128 | 261,618.1 | 406,208.3 | 428,813.9 | 1,573,588 |
| cosmos | 200 | 2-4MB | 108 | 190,490.1 | 397,043.4 | 421,227.1 | 2,359,859 |
| cosmos | 200 | >4MB | 16 | 236,109.3 | 393,610.2 | 416,921.1 | 5,091,529 |
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
| cosmos | mix | 3,902.81 | 0.03 | 0.18 | 34,289.0 | 0.0 | — | — |
| sql | mix | 4.45 | 2.26 | 0.17 | 9.0 | 0.0 | 7.61 | 13.22 |

**Reconstruction overhead** is the `Reconstruct ms` column: the cost of turning
stored blocks/items back into one order document. It is the price both designs
pay for decomposing the order, and it is directly comparable between them.

## Measured Cosmos RU

| Workload | Target RPS | RU per request | RU/sec at this rate | 429s |
| --- | ---: | ---: | ---: | ---: |
| cdf | 50 | 245.70 | 3,113 | 225 |
| full | 10 | 119.54 | 469 | 81 |
| full | 25 | 1,121.72 | 4,431 | 0 |
| mix | 10 | 195.48 | 1,955 | 0 |
| mix | 25 | 188.26 | 4,356 | 3 |
| mix | 50 | 197.37 | 4,540 | 31 |
| mix | 100 | 197.39 | 4,696 | 126 |
| mix | 200 | 194.54 | 4,681 | 450 |
| search | 50 | 218.35 | 10,913 | 432 |
| summary | 50 | 180.46 | 9,019 | 26 |
| title | 50 | 77.90 | 1,021 | 0 |

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
- `results/cosmos\run-001-mix-10rps.json` — cosmos mix @ 10 RPS
- `results/cosmos\run-002-mix-25rps.json` — cosmos mix @ 25 RPS
- `results/cosmos\run-003-mix-50rps.json` — cosmos mix @ 50 RPS
- `results/cosmos\run-004-mix-100rps.json` — cosmos mix @ 100 RPS
- `results/cosmos\run-005-mix-200rps.json` — cosmos mix @ 200 RPS
- `results/cosmos\run-006-summary-50rps.json` — cosmos summary @ 50 RPS
- `results/cosmos\run-007-title-50rps.json` — cosmos title @ 50 RPS
- `results/cosmos\run-008-cdf-50rps.json` — cosmos cdf @ 50 RPS
- `results/cosmos\run-009-search-50rps.json` — cosmos search @ 50 RPS
- `results/cosmos\run-010-full-10rps.json` — cosmos full @ 10 RPS
- `results/cosmos\run-011-full-25rps.json` — cosmos full @ 25 RPS

