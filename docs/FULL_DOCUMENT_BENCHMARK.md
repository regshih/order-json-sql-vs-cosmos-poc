# Full-Document Benchmark - GET /orders/{id}

Generated 2026-09-15T20:09:07.670774+00:00 by [tools/summarize_full_document.py](../tools/summarize_full_document.py) from 46 run file(s) in `results\fulldoc`. **No figure in this document was typed by hand.**

Every request returns the **complete logical order**. Load is open-model (constant arrival rate) from a separate VM inside the same VNet, so queueing delay appears in the latency figures rather than being hidden by coordinated omission, and multi-megabyte responses cross a real network boundary.

## 1. Rate sweep - all payload sizes mixed

| Backend | Target RPS | Achieved | p50 ms | p95 ms | p99 ms | Errors % | MB/s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| SQL Full JSON (nvarchar) | 10 | 10.0 | 32.7 | 217.1 | 425.8 | 0.00 | 15.63 |
| SQL Full JSON (nvarchar) | 25 | 25.0 | 19.5 | 106.2 | 172.0 | 0.00 | 37.57 |
| SQL Full JSON (nvarchar) | 50 | 50.0 | 17.2 | 63.1 | 155.9 | 0.00 | 73.87 |
| SQL Full JSON (nvarchar) | 100 | 95.7 | 1,532.5 | 4,821.9 | 6,481.7 | 0.00 | 140.12 |
| SQL Full JSON (native json) | 10 | 10.0 | 96.9 | 470.2 | 900.4 | 0.00 | 15.63 |
| SQL Full JSON (native json) | 25 | 13.8 | 20,106.9 | 36,230.7 | 45,687.4 | 2.58 | 20.20 |
| SQL Full JSON (native json) | 50 | 13.9 | 55,591.5 | 111,533.2 | 117,355.6 | 3.82 | 19.44 |
| SQL Full JSON (native json) | 100 | 13.9 | 141,624.1 | 261,699.9 | 273,393.2 | 0.13 | 20.12 |
| Cosmos Mongo (one doc) | 10 | 10.0 | 57.8 | 177.2 | 408.7 | 0.00 | 14.60 |
| Cosmos Mongo (one doc) | 25 | 24.9 | 60.5 | 184.1 | 283.0 | 0.00 | 34.29 |
| Cosmos Mongo (one doc) | 50 | 49.9 | 66.9 | 181.1 | 282.8 | 0.00 | 68.76 |
| Cosmos Mongo (one doc) | 100 | 99.8 | 73.1 | 189.8 | 293.0 | 0.00 | 138.21 |
| SQL hybrid (reassembled) | 10 | 10.0 | 53.9 | 672.5 | 1,822.1 | 0.00 | 13.11 |
| SQL hybrid (reassembled) | 25 | 24.3 | 31.1 | 377.0 | 2,885.4 | 0.00 | 31.76 |
| SQL hybrid (reassembled) | 50 | 49.0 | 29.5 | 92.1 | 257.6 | 0.00 | 63.26 |
| SQL hybrid (reassembled) | 100 | 99.7 | 54.3 | 143.4 | 206.7 | 0.00 | 127.36 |
| Cosmos NoSQL (reassembled) | 10 | 9.8 | 404.4 | 1,645.5 | 2,380.6 | 0.00 | 12.16 |
| Cosmos NoSQL (reassembled) | 25 | 12.1 | 18,170.2 | 47,542.6 | 55,804.4 | 0.00 | 15.01 |
| Cosmos NoSQL (reassembled) | 50 | 12.4 | 65,169.1 | 126,316.3 | 136,105.1 | 0.00 | 16.19 |
| Cosmos NoSQL (reassembled) | 100 | 12.0 | 155,152.7 | 308,161.7 | 324,727.1 | 0.02 | 15.93 |

## 2. Payload bands at 50 RPS - the customer's stated rate

| Backend | Band | Mean resp | Achieved RPS | p50 ms | p95 ms | Errors % | MB/s | DB ms | Recon ms | Ser ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| SQL Full JSON (nvarchar) | p500k | 0.49 MB | 50.0 | 11.3 | 13.0 | 0.00 | 24.54 | 21.20 | 0.00 | 0.14 |
| SQL Full JSON (nvarchar) | p1m | 0.94 MB | 50.0 | 14.6 | 16.6 | 0.00 | 47.04 | 18.28 | 0.00 | 0.16 |
| SQL Full JSON (nvarchar) | p2m | 2.01 MB | 50.0 | 22.2 | 25.9 | 0.00 | 100.52 | 14.87 | 0.00 | 0.28 |
| SQL Full JSON (nvarchar) | p3m | 3.05 MB | 48.9 | 928.8 | 1,551.1 | 0.00 | 149.41 | 23.65 | 0.00 | 0.47 |
| SQL Full JSON (nvarchar) | p5m | 4.86 MB | 30.7 | 16,651.8 | 30,387.0 | 0.00 | 149.00 | 1,008.43 | 0.00 | 0.33 |
| SQL Full JSON (native json) | p500k | 0.49 MB | 38.1 | 7,999.7 | 16,694.3 | 0.00 | 18.71 | 6,639.63 | 0.00 | 0.15 |
| SQL Full JSON (native json) | p1m | 0.94 MB | 21.2 | 34,011.2 | 61,312.7 | 0.84 | 19.81 | 5,827.51 | 0.00 | 0.14 |
| SQL Full JSON (native json) | p2m | 2.01 MB | 10.2 | 94,981.4 | 171,836.3 | 0.89 | 20.40 | 8,076.47 | 0.00 | 0.16 |
| SQL Full JSON (native json) | p3m | 3.05 MB | 7.0 | 150,772.5 | 270,498.9 | 2.31 | 20.85 | 7,269.18 | 0.00 | 0.17 |
| SQL Full JSON (native json) | p5m | 4.86 MB | 4.5 | 243,697.0 | 450,787.9 | 3.11 | 20.97 | 8,280.64 | 0.00 | 0.23 |
| Cosmos Mongo (one doc) | p500k | 0.49 MB | 50.0 | 32.4 | 43.0 | 0.00 | 24.53 | - | - | - |
| Cosmos Mongo (one doc) | p1m | 0.94 MB | 49.9 | 54.6 | 65.4 | 0.00 | 47.01 | 47.66 | 0.00 | 0.93 |
| Cosmos Mongo (one doc) | p2m | 2.01 MB | 49.9 | 118.7 | 146.9 | 0.00 | 100.31 | 48.98 | 0.00 | 0.98 |
| Cosmos Mongo (one doc) | p3m | 3.05 MB | 49.8 | 183.8 | 216.4 | 0.00 | 151.93 | 160.85 | 0.00 | 3.29 |
| Cosmos Mongo (one doc) | p5m | 4.86 MB | 49.6 | 317.1 | 423.1 | 0.00 | 240.79 | 48.53 | 0.00 | 0.98 |
| SQL hybrid (reassembled) | p500k | 0.50 MB | 50.0 | 17.2 | 19.2 | 0.00 | 24.80 | 8.62 | 3.55 | 0.55 |
| SQL hybrid (reassembled) | p1m | 0.94 MB | 50.0 | 23.7 | 27.5 | 0.00 | 47.04 | 8.62 | 3.55 | 0.55 |
| SQL hybrid (reassembled) | p2m | 1.97 MB | 49.9 | 71.7 | 118.0 | 0.00 | 98.60 | 36.54 | 12.79 | 1.93 |
| SQL hybrid (reassembled) | p3m | 3.01 MB | 49.9 | 127.0 | 232.4 | 0.00 | 150.01 | 11.62 | 6.21 | 1.02 |
| SQL hybrid (reassembled) | p5m | 4.76 MB | 32.0 | 13,786.6 | 25,859.4 | 0.00 | 152.38 | 27.31 | 15.06 | 2.02 |
| Cosmos NoSQL (reassembled) | p500k | 0.50 MB | 12.3 | 65,492.3 | 127,696.9 | 0.00 | 6.17 | 9,367.73 | 0.12 | 0.79 |
| Cosmos NoSQL (reassembled) | p1m | 1.06 MB | 10.7 | 71,953.4 | 151,795.7 | 0.04 | 11.35 | 9,972.27 | 0.12 | 0.84 |
| Cosmos NoSQL (reassembled) | p2m | 1.98 MB | 13.8 | 55,873.5 | 108,670.3 | 0.00 | 27.20 | 8,217.66 | 0.13 | 1.59 |
| Cosmos NoSQL (reassembled) | p3m | 2.99 MB | 13.0 | 63,181.4 | 119,973.3 | 0.00 | 38.91 | 9,758.28 | 0.12 | 1.27 |
| Cosmos NoSQL (reassembled) | p5m | 4.78 MB | 15.0 | 50,363.8 | 94,847.2 | 0.00 | 71.46 | 6,535.65 | 0.14 | 5.38 |

`DB ms`, `Recon ms` and `Ser ms` are server-side medians: time in the database, time reassembling the order, and time serialising the response. A full-document backend should show **~0 reconstruct** by construction - that is the difference the whole extension exists to measure. Where wire throughput flattens while DB time stays low, the constraint is the network or serialisation, **not** the database.

## 3. Where the constraint actually is

| Backend | Peak MB/s observed | At band | RPS held at 5 MB | Ceiling reached? |
| --- | ---: | --- | ---: | --- |
| SQL Full JSON (nvarchar) | 149.4 | p3m | 30.7 | yes |
| SQL Full JSON (native json) | 21.0 | p5m | 4.5 | yes |
| Cosmos Mongo (one doc) | 240.8 | p5m | 49.6 | no |
| SQL hybrid (reassembled) | 152.4 | p5m | 32.0 | yes |
| Cosmos NoSQL (reassembled) | 71.5 | p5m | 15.0 | yes |

The highest sustained figure observed on this VM pair is **240.8 MB/s** (Cosmos Mongo (one doc)). Any backend that flattens materially below that did **not** hit a network limit - the same two machines and the same API process carried more for a different storage design. Its ceiling is in the database or, more often here, in the client driver's path for large values.

> Server-side phase timings come from the API's in-process telemetry across multiple uvicorn workers, each with its own counters, so they describe shape rather than being authoritative. Client-side latency and throughput are authoritative.

