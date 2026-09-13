# Cost Analysis

Generated 2026-09-13T19:19:22.649070+00:00 by [tools/cost_model.py](../tools/cost_model.py).

## Pricing basis

- **Source:** [Azure Retail Prices API](https://prices.azure.com/api/retail/prices) — the same meters the
  public pricing pages render. No price in this document is hard-coded.
- **Retrieved:** 2026-09-13T19:18:55.005049+00:00
- **Region:** `westus3`
- **Currency:** USD, pay-as-you-go, Consumption meters only (reservations and
  DevTest offers filtered out).
- **Month:** 730 hours.

| Meter key | Retail price | Unit | Meter name | SKU |
| --- | ---: | --- | --- | --- |
| `adls_cool_lrs` | $0.01 | 1 GB/Month | Cool LRS Data Stored | Cool LRS |
| `adls_hot_lrs` | $0.0169 | 1 GB/Month | Hot LRS Data Stored | Hot LRS |
| `blob_cool_lrs` | $0.01 | 1 GB/Month | Cool LRS Data Stored | Cool LRS |
| `blob_hot_lrs` | $0.016928 | 1 GB/Month | Hot LRS Data Stored | Hot LRS |
| `cosmos_autoscale_ru` | $0.012 | 1/Hour | AP1 100 RUs | AP1 |
| `cosmos_provisioned_ru` | $0.008 | 1/Hour | 100 RU/s | RUs |
| `cosmos_storage` | $0.25 | 1 GB/Month | Data Stored | RUs |
| `fabric_capacity_cu` | $0.18 | 1 Hour | Data Warehouse Capacity Usage CU | Data Warehouse Capacity Usage |
| `sql_bc_provisioned_vcore` | $0.304435 | 1 Hour | vCore | 1 vCore |
| `sql_gp_provisioned_vcore` | $0.152217 | 1 Hour | vCore | vCore |
| `sql_gp_serverless_vcore` | $0.521758 | 1 Hour | vCore | 1 vCore |
| `sql_gp_storage` | $0.115 | 1 GB/Month | General Purpose Data Stored | General Purpose |
| `sql_hs_vcore` | $0.18266 | 1 Hour | vCore | vCore |

**Meters that could not be retrieved** (no figure is invented for these):

- `onelake_storage_hot` — no consumption meter matched

## Measured Cosmos RU (not estimated)

> **No measured RU available.** Run the Cosmos benchmark
> (`loadtests/operational/run_load.py --backend cosmos`) before relying on
> any Cosmos cost figure. This document deliberately shows no estimate.

## Cosmos DB — throughput cost by read rate

Workload mix: {"summary": 0.5, "title": 0.2, "cdf": 0.15, "checklist": 0.1, "full": 0.05} (matches the benchmark's `mix` shape).

| RPS | Mode | Measured RU/request | RU/s required | Provisioned RU/s | $/month |
| ---: | --- | ---: | ---: | ---: | ---: |

> no measured RU for ['summary', 'title', 'cdf', 'checklist', 'full']; run the Cosmos benchmark first

**Cosmos storage:** 442 GB at $0.25/GB/month = **$110.38/month**.

## Azure SQL Database — deployment options

| Tier | vCores | $/vCore/hr | Compute $/mo | Storage GB | Storage $/mo | Total $/mo |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| gp_serverless | 2 | $0.521758 | $761.77 | 442 | $50.77 | **$812.54** |
| gp_serverless | 4 | $0.521758 | $1,523.53 | 442 | $50.77 | **$1,574.31** |
| gp_provisioned | 2 | $0.152217 | $222.24 | 442 | $50.77 | **$273.01** |
| gp_provisioned | 4 | $0.152217 | $444.47 | 442 | $50.77 | **$495.25** |
| gp_provisioned | 8 | $0.152217 | $888.95 | 442 | $50.77 | **$939.72** |
| business_critical | 4 | $0.304435 | $888.95 | 442 | $50.77 | **$939.72** |
| hyperscale | 4 | $0.18266 | $533.37 | 442 | $50.77 | **$584.14** |

Serverless is billed per second while the database is active; the figures above
assume it is active continuously (`activeFraction = 1.0`), which is the correct
assumption for a 50 RPS always-on API. Serverless only saves money when the
database can actually auto-pause.

## Raw archive

| Tier | $/GB/month | Archive GB | $/month |
| --- | ---: | ---: | ---: |
| Blob Hot LRS | $0.0169 | 1,935 | $32.70 |
| Blob Cool LRS | $0.01 | 1,935 | $19.35 |

The archive holds every version, gzipped. Moving it to Cool (or Archive) tier is
the cheapest lever in the whole design, and it is available regardless of which
operational database is chosen.

## Microsoft Fabric

| SKU | CUs | $/month (24×7) |
| --- | ---: | ---: |
| F2 | 2 | $262.80 |
| F4 | 4 | $525.60 |
| F64 | 64 | $8,409.60 |

Derived from the Capacity Usage meter at $0.18/CU/hour. Fabric bills a flat capacity, not per query. Derived from the Capacity Usage CU-hour meter; no per-SKU meter exists.

Fabric capacity is a **flat cost that does not vary with the operational
database choice**, so it is neutral in the SQL-vs-Cosmos comparison. It can be
paused when not in use, which is how this POC controlled its cost.

## Assumptions and their labels

| Item | Basis |
| --- | --- |
| Cosmos RU per operation | **MEASURED** from benchmark runs |
| Cosmos write RU per order | **MEASURED** from ingestion runs |
| Unit prices | **VERIFIED** live from the Azure Retail Prices API on the date above |
| Storage volumes | **MODELLED** — see [RETENTION_ANALYSIS.md](RETENTION_ANALYSIS.md), driven by ASSUMED order and version rates |
| 1.5x RU headroom | **ASSUMPTION** — engineering judgement for burst tolerance |
| SQL vCore sizing | **MEASURED** requirement from the benchmark, see [BENCHMARK_SUMMARY.md](../results/BENCHMARK_SUMMARY.md) |
| Serverless active fraction 1.0 | **ASSUMPTION** — an always-on API never auto-pauses |

