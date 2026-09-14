"""Cost model driven by MEASURED RU / latency and LIVE Azure retail prices.

No price is hard-coded. Prices are fetched from the Azure Retail Prices API
(the same meters the pricing pages render) and cached to
``artifacts/pricing-snapshot.json`` with the retrieval date, region, meter name
and SKU, so every number in docs/COST_ANALYSIS.md is traceable.

    python tools/cost_model.py --refresh-prices
    python tools/cost_model.py --md-out docs/COST_ANALYSIS.md

Cosmos RU comes from the measured benchmark results, never estimated. If no
measured RU is available the tool says so instead of inventing a figure.
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RETAIL_API = "https://prices.azure.com/api/retail/prices"
HOURS_PER_MONTH = 730

# Meters we need, expressed as Retail Prices API filters. Each entry is
# (key, OData filter, unit note). Keeping the *filters* here rather than the
# prices is what makes the model self-updating.
#
# Meter names were discovered by querying the API itself (not guessed); the
# retrieved meterName/skuName/productName is recorded in the snapshot so every
# figure is traceable back to a specific meter.
METERS: list[tuple[str, str, str]] = [
    (
        "cosmos_provisioned_ru",
        "serviceName eq 'Azure Cosmos DB' and meterName eq '100 RU/s' and skuName eq 'RUs' "
        "and armRegionName eq '{region}'",
        "per 100 RU/s per hour",
    ),
    (
        "cosmos_autoscale_ru",
        "serviceName eq 'Azure Cosmos DB' and meterName eq 'AP1 100 RUs' "
        "and armRegionName eq '{region}'",
        "per 100 RU/s per hour (autoscale)",
    ),
    (
        "cosmos_storage",
        "serviceName eq 'Azure Cosmos DB' and meterName eq 'Data Stored' and skuName eq 'RUs' "
        "and armRegionName eq '{region}'",
        "per GB per month",
    ),
    (
        "sql_gp_serverless_vcore",
        "serviceName eq 'SQL Database' and meterName eq 'vCore' and skuName eq '1 vCore' "
        "and productName eq 'SQL Database General Purpose - Serverless - Compute Gen5' "
        "and armRegionName eq '{region}'",
        "per vCore per hour (serverless, billed per second while active)",
    ),
    (
        "sql_gp_provisioned_vcore",
        "serviceName eq 'SQL Database' and meterName eq 'vCore' and skuName eq 'vCore' "
        "and productName eq 'SQL Database Single/Elastic Pool General Purpose - Compute Gen5' "
        "and armRegionName eq '{region}'",
        "per vCore per hour",
    ),
    (
        "sql_bc_provisioned_vcore",
        "serviceName eq 'SQL Database' and meterName eq 'vCore' and skuName eq '1 vCore' "
        "and productName eq 'SQL Database Single/Elastic Pool Business Critical - Compute Gen5' "
        "and armRegionName eq '{region}'",
        "per vCore per hour",
    ),
    (
        "sql_hs_vcore",
        "serviceName eq 'SQL Database' and meterName eq 'vCore' and skuName eq 'vCore' "
        "and productName eq 'SQL Database SingleDB/Elastic Pool Hyperscale - Compute Gen5' "
        "and armRegionName eq '{region}'",
        "per vCore per hour",
    ),
    (
        "sql_gp_storage",
        "serviceName eq 'SQL Database' and contains(meterName, 'Data Stored') "
        "and contains(productName, 'General Purpose') and armRegionName eq '{region}'",
        "per GB per month",
    ),
    (
        "adls_hot_lrs",
        "serviceName eq 'Storage' and meterName eq 'Hot LRS Data Stored' and skuName eq 'Hot LRS' "
        "and productName eq 'Azure Data Lake Storage Gen2 Hierarchical Namespace' "
        "and armRegionName eq '{region}'",
        "per GB per month (ADLS Gen2, hot, LRS)",
    ),
    (
        "adls_cool_lrs",
        "serviceName eq 'Storage' and meterName eq 'Cool LRS Data Stored' and skuName eq 'Cool LRS' "
        "and productName eq 'Azure Data Lake Storage Gen2 Hierarchical Namespace' "
        "and armRegionName eq '{region}'",
        "per GB per month (ADLS Gen2, cool, LRS)",
    ),
    (
        "blob_hot_lrs",
        "serviceName eq 'Storage' and meterName eq 'Hot LRS Data Stored' and skuName eq 'Hot LRS' "
        "and productName eq 'General Block Blob v2' and armRegionName eq '{region}'",
        "per GB per month",
    ),
    (
        "blob_cool_lrs",
        "serviceName eq 'Storage' and meterName eq 'Cool LRS Data Stored' and skuName eq 'Cool LRS' "
        "and productName eq 'General Block Blob v2' and armRegionName eq '{region}'",
        "per GB per month",
    ),
    (
        "fabric_capacity_cu",
        "serviceName eq 'Microsoft Fabric' and productName eq 'Fabric Capacity' "
        "and meterName eq 'Data Warehouse Capacity Usage CU' and armRegionName eq '{region}'",
        "per CU per hour",
    ),
    (
        "onelake_storage_hot",
        "serviceName eq 'Microsoft Fabric' and productName eq 'OneLake' "
        "and meterName eq 'OneLake Storage Data Stored' and armRegionName eq '{region}'",
        "per GB per month",
    ),
]


def fetch_meter(odata_filter: str) -> list[dict[str, Any]]:
    url = f"{RETAIL_API}?$filter={urllib.parse.quote(odata_filter)}&currencyCode='USD'"
    with urllib.request.urlopen(url, timeout=40) as r:
        return json.loads(r.read().decode())["Items"]


def refresh_prices(region: str, out: Path) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "retrievedUtc": datetime.now(timezone.utc).isoformat(),
        "source": RETAIL_API,
        "currency": "USD",
        "armRegionName": region,
        "note": "Fetched live from the Azure Retail Prices API. Consumption meters only; "
                "reservation and dev/test rows are filtered out.",
        "meters": {},
        "unavailable": [],
    }
    for key, flt, unit in METERS:
        try:
            items = fetch_meter(flt.format(region=region))
            # Consumption only: exclude reservations and DevTest offers.
            items = [
                i for i in items
                if i.get("type") == "Consumption"
                and "DevTest" not in (i.get("skuName") or "")
                and not i.get("reservationTerm")
                # Free/included tiers are $0 meters that would otherwise win the
                # "cheapest match" selection below and understate real cost.
                and "Free" not in (i.get("skuName") or "")
                and "Free" not in (i.get("meterName") or "")
                and i["retailPrice"] > 0
            ]
            if not items:
                snapshot["unavailable"].append({"key": key, "filter": flt.format(region=region),
                                                "reason": "no consumption meter matched"})
                continue
            # Cheapest matching consumption meter is the base rate.
            best = min(items, key=lambda i: i["retailPrice"])
            snapshot["meters"][key] = {
                "retailPrice": best["retailPrice"],
                "unitOfMeasure": best["unitOfMeasure"],
                "meterName": best["meterName"],
                "skuName": best.get("skuName"),
                "productName": best.get("productName"),
                "serviceName": best.get("serviceName"),
                "armRegionName": best.get("armRegionName"),
                "effectiveStartDate": best.get("effectiveStartDate"),
                "unitNote": unit,
                "matchCount": len(items),
            }
        except Exception as exc:
            snapshot["unavailable"].append({"key": key, "reason": f"{type(exc).__name__}: {exc}"[:200]})

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return snapshot


def load_prices(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"{path} not found - run with --refresh-prices first")
    return json.loads(path.read_text())


def price(snapshot: dict[str, Any], key: str) -> float | None:
    m = snapshot["meters"].get(key)
    return m["retailPrice"] if m else None


# --------------------------------------------------------------------------
# Measured inputs
# --------------------------------------------------------------------------


# Maps the index-impact measurement's operation names onto API endpoint names.
INDEX_IMPACT_TO_ENDPOINT = {
    "singlePartitionFullOrder": "full",
    "tenantScopedSearch": "search",
}
BLOCK_TO_ENDPOINT = {"TITLE": "title", "CDF": "cdf", "CHECKLIST": "checklist",
                     "NOTES": "notes", "PARTIES": "parties"}


def load_under_load_ru(results_dir: Path) -> dict[str, Any]:
    """BEST RU source: effective RU derived from a measured capacity ceiling.

    Isolated single-request RU is not a safe basis for sizing - measurement
    showed it wrong in both directions (4x too high on a single-partition
    container, then ~2x too low without concurrency). The figure that answers
    "how much throughput must I buy" is derived from what the workload actually
    achieved against a known ceiling:

        effectiveRuPerRequest = ceilingRuPerSecond / achievedRps

    Produced by driving the sweep at a known ceiling; see
    results/cosmos/effective-ru-under-load.json and
    docs/DECISION_MATRIX.md section 3.1.
    """
    p = results_dir / "cosmos" / "effective-ru-under-load.json"
    if not p.exists():
        return {}
    d = json.loads(p.read_text())
    ops = d.get("operations", {})
    by_op: dict[str, Any] = {}
    for name, v in ops.items():
        ru = v.get("effectiveRuPerRequest") or v.get("upperBoundRuPerRequest")
        if ru is None:
            continue
        # For operations that were not RU-bound, the isolated post-split figure
        # is the better estimate than a loose upper bound.
        if not v.get("capped") and v.get("isolatedPostSplitRu"):
            ru = v["isolatedPostSplitRu"]
        by_op[name] = {"mean": ru, "p50": ru, "p95": ru,
                       "samples": 0,
                       "basis": "capacity-ceiling derivation" if v.get("capped")
                                else "isolated post-split (not RU-bound under load)"}
    mix = d.get("derivedMix", {})
    if mix.get("effectiveRuPerRequest"):
        by_op["_derivedMix"] = {"mean": mix["effectiveRuPerRequest"]}
    return {
        "source": [str(p.relative_to(results_dir))],
        "label": "under-load (capacity-ceiling derivation)",
        "measuredUtc": d.get("generatedUtc"),
        "method": d.get("method"),
        "byOperation": by_op,
    }


def load_authoritative_ru(results_dir: Path) -> dict[str, Any]:
    """Fallback RU source: the single-process, isolated measurement.

    The benchmark API runs 8 uvicorn workers, each with its own in-process
    metrics ring, so its reported RU is a one-worker sample that can carry
    residue from a previous run. cosmos/indexing/measure_index_impact.py runs
    single-process and accumulates the charge page by page, so its numbers are
    the ones a cost model may rely on.
    """
    p = results_dir / "cosmos" / "index-impact.json"
    if not p.exists():
        return {}
    runs = json.loads(p.read_text()).get("runs", [])
    if not runs:
        return {}
    r = runs[-1]
    ops = r.get("operations", {})
    lookup = (ops.get("crossPartitionLookup", {}).get("ru", {}) or {}).get("mean", 0.0)
    point = (ops.get("pointReadWithFullPk", {}).get("ru", {}) or {}).get("mean", 0.0)

    by_op: dict[str, Any] = {}
    # A summary read is the tenant lookup plus a point read - the API contract
    # has no tenant in the route. See docs/COSMOS_DESIGN.md.
    by_op["summary"] = {"mean": round(lookup + point, 3), "p50": round(lookup + point, 3),
                        "p95": round(lookup + point, 3), "samples": 0,
                        "composition": "crossPartitionLookup + pointReadWithFullPk"}
    for key, endpoint in INDEX_IMPACT_TO_ENDPOINT.items():
        ru = (ops.get(key, {}).get("ru", {}) or {})
        if ru:
            extra = lookup if endpoint == "full" else 0.0
            by_op[endpoint] = {"mean": round(ru.get("mean", 0) + extra, 3),
                               "p50": round(ru.get("p50", 0) + extra, 3),
                               "p95": round(ru.get("p95", 0) + extra, 3),
                               "samples": ru.get("n", 0)}
    for block, endpoint in BLOCK_TO_ENDPOINT.items():
        ru = ((ops.get("blockReads", {}) or {}).get(block, {}).get("ru", {}) or {})
        if ru:
            by_op[endpoint] = {"mean": round(ru.get("mean", 0) + lookup, 3),
                               "p50": round(ru.get("p50", 0) + lookup, 3),
                               "p95": round(ru.get("p95", 0) + lookup, 3),
                               "samples": ru.get("n", 0)}
    return {
        "source": [str(p.relative_to(results_dir))],
        "label": r.get("label"),
        "measuredUtc": r.get("measuredUtc"),
        "method": ("single-process, per-page RU accumulation, tenant-lookup RU "
                   "added to every endpoint because the API contract carries no "
                   "tenant in the route"),
        "byOperation": by_op,
    }


def load_measured_ru(results_dir: Path) -> dict[str, Any]:
    """Fallback RU source: the benchmark API's own telemetry.

    INDICATIVE ONLY - see load_authoritative_ru for why.
    """
    out: dict[str, Any] = {"source": [], "byOperation": {}}
    files = sorted((results_dir / "cosmos").glob("run-*.json")) if (results_dir / "cosmos").exists() else []
    for f in files:
        d = json.loads(f.read_text())
        srv = d.get("serverMetrics") or {}
        for op, m in (srv.get("byOperation") or {}).items():
            ru = m.get("request_charge_ru")
            if not ru:
                continue
            slot = out["byOperation"].setdefault(op, {"samples": 0, "p50": [], "p95": [], "mean": []})
            slot["samples"] += m["requests"]
            slot["p50"].append(ru["p50"])
            slot["p95"].append(ru["p95"])
            slot["mean"].append(ru["mean"])
        out["source"].append(f.name)
    for op, slot in out["byOperation"].items():
        n = len(slot["mean"]) or 1
        slot["p50"] = round(sum(slot["p50"]) / n, 3)
        slot["p95"] = round(sum(slot["p95"]) / n, 3)
        slot["mean"] = round(sum(slot["mean"]) / n, 3)
    return out


def load_measured_write_ru(results_dir: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    d = results_dir / "ingestion"
    if not d.exists():
        return out
    for f in sorted(d.glob("ingest-cosmos-*.json")):
        s = json.loads(f.read_text())["summary"]
        if s.get("ruPerOrder"):
            out = {
                "ruPerOrderIngest": s["ruPerOrder"],
                "itemsPerOrder": round(s["itemsWritten"] / max(s["ordersSucceeded"], 1), 1),
                "source": f.name,
            }
    return out


# Default workload mix, matching loadtests WORKLOADS["mix"].
DEFAULT_MIX = {"summary": 0.50, "title": 0.20, "cdf": 0.15, "checklist": 0.10, "full": 0.05}


def cosmos_cost(snapshot: dict[str, Any], ru: dict[str, Any], rps: float,
                mix: dict[str, float], autoscale: bool, headroom: float = 1.5) -> dict[str, Any]:
    """RU/s required for a read rate, and its monthly cost.

    ``headroom`` provisions above the steady-state average because Cosmos bills
    provisioned throughput, not consumption: sizing exactly to the mean would
    throttle on any burst.
    """
    by_op = ru.get("byOperation", {})
    # If the mix was derived directly from a capacity measurement, use it: it is
    # a measurement, not a re-weighting of per-operation figures.
    if len(mix) > 1 and "_derivedMix" in by_op:
        ru_per_request = by_op["_derivedMix"]["mean"]
    else:
        missing = [op for op in mix if op not in by_op]
        if missing:
            return {"error": f"no measured RU for {missing}; run the Cosmos benchmark first"}
        ru_per_request = sum(mix[op] * by_op[op]["mean"] for op in mix)
    ru_per_sec = ru_per_request * rps
    provisioned = max(400, int(round(ru_per_sec * headroom / 100.0)) * 100)

    key = "cosmos_autoscale_ru" if autoscale else "cosmos_provisioned_ru"
    unit = price(snapshot, key)
    if unit is None:
        return {"error": f"price for {key} unavailable"}

    monthly = provisioned / 100 * unit * HOURS_PER_MONTH
    return {
        "rps": rps,
        "mode": "autoscale" if autoscale else "provisioned",
        "measuredRuPerRequest": round(ru_per_request, 3),
        "ruPerSecondRequired": round(ru_per_sec, 1),
        "headroomFactor": headroom,
        "provisionedRuPerSec": provisioned,
        "unitPricePer100RuPerHour": unit,
        "monthlyThroughputUsd": round(monthly, 2),
        "perOperationRu": {op: by_op[op]["mean"] for op in mix if op in by_op},
        "ruBasis": ("capacity-ceiling derivation of the whole mix"
                    if (len(mix) > 1 and "_derivedMix" in by_op)
                    else "weighted per-operation measurements"),
    }


def sql_cost(snapshot: dict[str, Any], vcores: float, tier: str, storage_gb: float,
             active_fraction: float = 1.0) -> dict[str, Any]:
    key = {
        "gp_serverless": "sql_gp_serverless_vcore",
        "gp_provisioned": "sql_gp_provisioned_vcore",
        "business_critical": "sql_bc_provisioned_vcore",
        "hyperscale": "sql_hs_vcore",
    }[tier]
    unit = price(snapshot, key)
    st = price(snapshot, "sql_gp_storage")
    if unit is None:
        return {"error": f"price for {key} unavailable"}
    compute = vcores * unit * HOURS_PER_MONTH * active_fraction
    storage = (storage_gb * st) if st is not None else None
    return {
        "tier": tier,
        "vCores": vcores,
        "activeFraction": active_fraction,
        "unitPricePerVCoreHour": unit,
        "monthlyComputeUsd": round(compute, 2),
        "storageGb": storage_gb,
        "storagePricePerGbMonth": st,
        "monthlyStorageUsd": round(storage, 2) if storage is not None else None,
        "monthlyTotalUsd": round(compute + (storage or 0), 2),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--region", default="westus3")
    ap.add_argument("--refresh-prices", action="store_true")
    ap.add_argument("--prices", default="artifacts/pricing-snapshot.json")
    ap.add_argument("--results", default="results")
    ap.add_argument("--hot-storage-gb", type=float, default=441.5,
                    help="hot operational GB; default is the 'medium' retention scenario")
    ap.add_argument("--archive-storage-gb", type=float, default=1935.0)
    ap.add_argument("--json-out", default="artifacts/cost-model.json")
    ap.add_argument("--md-out", default="docs/COST_ANALYSIS.md")
    args = ap.parse_args()

    pp = Path(args.prices)
    snapshot = refresh_prices(args.region, pp) if args.refresh_prices else load_prices(pp)
    print(f"prices: {len(snapshot['meters'])} meters, {len(snapshot['unavailable'])} unavailable "
          f"(retrieved {snapshot['retrievedUtc'][:10]}, region {snapshot['armRegionName']})")

    results = Path(args.results)
    ru = load_under_load_ru(results)
    if ru:
        print(f"RU source: {ru['source'][0]} (under-load capacity derivation - best)")
    else:
        ru = load_authoritative_ru(results)
    if ru and "effective-ru" not in (ru.get("source") or [""])[0]:
        print(f"RU source: {(ru.get('source') or ['?'])[0]} (isolated single-process)")
    if not ru:
        ru = load_measured_ru(results)
        if ru.get("byOperation"):
            print("RU source: benchmark API telemetry (INDICATIVE - one-worker sample)")
            ru["caveat"] = ("sampled from one of several uvicorn workers; run "
                            "cosmos/indexing/measure_index_impact.py for authoritative RU")
    write_ru = load_measured_write_ru(results)

    cosmos_rows = []
    for rps in (10, 50, 100):
        for autoscale in (False, True):
            cosmos_rows.append(cosmos_cost(snapshot, ru, rps, DEFAULT_MIX, autoscale))

    # Per-operation cost at 50 RPS if the whole workload were that operation.
    per_op = {}
    for op in ("summary", "title", "cdf", "checklist", "full", "search"):
        if op in ru.get("byOperation", {}):
            per_op[op] = cosmos_cost(snapshot, ru, 50, {op: 1.0}, autoscale=False)

    sql_rows = [
        sql_cost(snapshot, 2, "gp_serverless", args.hot_storage_gb, active_fraction=1.0),
        sql_cost(snapshot, 4, "gp_serverless", args.hot_storage_gb, active_fraction=1.0),
        sql_cost(snapshot, 2, "gp_provisioned", args.hot_storage_gb),
        sql_cost(snapshot, 4, "gp_provisioned", args.hot_storage_gb),
        sql_cost(snapshot, 8, "gp_provisioned", args.hot_storage_gb),
        sql_cost(snapshot, 4, "business_critical", args.hot_storage_gb),
        sql_cost(snapshot, 4, "hyperscale", args.hot_storage_gb),
    ]

    cosmos_storage_unit = price(snapshot, "cosmos_storage")
    # The raw archive is ADLS Gen2 (hierarchical namespace), so prefer those
    # meters and fall back to plain block blob if unavailable.
    blob_hot = price(snapshot, "adls_hot_lrs") or price(snapshot, "blob_hot_lrs")
    blob_cool = price(snapshot, "adls_cool_lrs") or price(snapshot, "blob_cool_lrs")
    fabric_cu = price(snapshot, "fabric_capacity_cu")
    onelake = price(snapshot, "onelake_storage_hot")

    out = {
        "generatedUtc": datetime.now(timezone.utc).isoformat(),
        "pricingSnapshot": {
            "retrievedUtc": snapshot["retrievedUtc"],
            "region": snapshot["armRegionName"],
            "source": snapshot["source"],
            "meters": snapshot["meters"],
            "unavailable": snapshot["unavailable"],
        },
        "measuredRu": ru,
        "measuredWriteRu": write_ru,
        "workloadMix": DEFAULT_MIX,
        "cosmosThroughput": cosmos_rows,
        "cosmosPerOperationAt50Rps": per_op,
        "cosmosStorage": {
            "pricePerGbMonth": cosmos_storage_unit,
            "hotGb": args.hot_storage_gb,
            "monthlyUsd": round(args.hot_storage_gb * cosmos_storage_unit, 2) if cosmos_storage_unit else None,
        },
        "sqlOptions": sql_rows,
        "archive": {
            "hotPricePerGbMonth": blob_hot,
            "coolPricePerGbMonth": blob_cool,
            "archiveGb": args.archive_storage_gb,
            "monthlyHotUsd": round(args.archive_storage_gb * blob_hot, 2) if blob_hot else None,
            "monthlyCoolUsd": round(args.archive_storage_gb * blob_cool, 2) if blob_cool else None,
        },
        "fabric": {
            "pricePerCuHour": fabric_cu,
            "f2MonthlyUsd": round(2 * fabric_cu * HOURS_PER_MONTH, 2) if fabric_cu else None,
            "f4MonthlyUsd": round(4 * fabric_cu * HOURS_PER_MONTH, 2) if fabric_cu else None,
            "f64MonthlyUsd": round(64 * fabric_cu * HOURS_PER_MONTH, 2) if fabric_cu else None,
            "oneLakePricePerGbMonth": onelake,
            "note": "Fabric bills a flat capacity, not per query. Derived from the "
                    "Capacity Usage CU-hour meter; no per-SKU meter exists.",
        },
    }

    jp = Path(args.json_out)
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"-> {jp}")

    Path(args.md_out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.md_out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(build_md(out))
    print(f"-> {args.md_out}")

    for r in cosmos_rows:
        if "error" in r:
            print("cosmos:", r["error"])
        else:
            print(f"cosmos {r['rps']:>3} rps {r['mode']:<11} ru/req={r['measuredRuPerRequest']:>7.2f} "
                  f"-> {r['provisionedRuPerSec']:>6,} RU/s = ${r['monthlyThroughputUsd']:>9,.2f}/mo")
    for r in sql_rows:
        if "error" not in r:
            print(f"sql {r['tier']:<18} {r['vCores']:>4g} vCore -> ${r['monthlyTotalUsd']:>9,.2f}/mo")


def build_md(d: dict[str, Any]) -> str:
    ps = d["pricingSnapshot"]
    L: list[str] = []
    a = L.append
    a("# Cost Analysis")
    a("")
    a(f"Generated {d['generatedUtc']} by [tools/cost_model.py](../tools/cost_model.py).")
    a("")
    a("## Pricing basis")
    a("")
    a(f"- **Source:** [Azure Retail Prices API]({ps['source']}) — the same meters the")
    a("  public pricing pages render. No price in this document is hard-coded.")
    a(f"- **Retrieved:** {ps['retrievedUtc']}")
    a(f"- **Region:** `{ps['region']}`")
    a("- **Currency:** USD, pay-as-you-go, Consumption meters only (reservations and")
    a("  DevTest offers filtered out).")
    a(f"- **Month:** {HOURS_PER_MONTH} hours.")
    a("")
    a("| Meter key | Retail price | Unit | Meter name | SKU |")
    a("| --- | ---: | --- | --- | --- |")
    for k, m in sorted(ps["meters"].items()):
        a(f"| `{k}` | ${m['retailPrice']} | {m['unitOfMeasure']} | {m['meterName']} | "
          f"{m.get('skuName') or '—'} |")
    a("")
    if ps["unavailable"]:
        a("**Meters that could not be retrieved** (no figure is invented for these):")
        a("")
        for u in ps["unavailable"]:
            a(f"- `{u['key']}` — {u['reason']}")
        a("")

    ru = d["measuredRu"]
    a("## Measured Cosmos RU (not estimated)")
    a("")
    if not ru.get("byOperation"):
        a("> **No measured RU available.** Run the Cosmos benchmark")
        a("> (`loadtests/operational/run_load.py --backend cosmos`) before relying on")
        a("> any Cosmos cost figure. This document deliberately shows no estimate.")
        a("")
    else:
        a(f"Source: `{', '.join(x.replace(chr(92), '/') for x in ru['source'])}`")
        if ru.get("method"):
            a("")
            a(f"Method: {ru['method'].rstrip('.')}.")
        if ru.get("caveat"):
            a("")
            a(f"> **Caveat:** {ru['caveat']}.")
        a("")
        a("| Operation | RU/request | Basis |")
        a("| --- | ---: | --- |")
        for op, m in sorted(ru["byOperation"].items()):
            if op == "_derivedMix":
                label, basis = "**derived mix (used for costing)**",                     "capacity-ceiling derivation of the whole mix"
            else:
                label, basis = f"`{op}`", m.get("basis", "measured")
            a(f"| {label} | {m['mean']:,.2f} | {basis} |")
        a("")
    if d["measuredWriteRu"]:
        w = d["measuredWriteRu"]
        a(f"**Write cost (MEASURED):** {w['ruPerOrderIngest']} RU to ingest one complete order "
          f"version as {w['itemsPerOrder']} items (`{w['source']}`).")
        a("")

    a("## Cosmos DB - throughput cost by read rate")
    a("")
    a(f"Workload mix: {json.dumps(d['workloadMix'])} (matches the benchmark's `mix` shape).")
    a("")
    a("| RPS | Mode | Measured RU/request | RU/s required | Provisioned RU/s | $/month |")
    a("| ---: | --- | ---: | ---: | ---: | ---: |")
    for r in d["cosmosThroughput"]:
        if "error" in r:
            continue
        a(f"| {r['rps']} | {r['mode']} | {r['measuredRuPerRequest']} | {r['ruPerSecondRequired']:,} | "
          f"{r['provisionedRuPerSec']:,} | ${r['monthlyThroughputUsd']:,.2f} |")
    a("")
    if d["cosmosThroughput"] and "error" in d["cosmosThroughput"][0]:
        a(f"> {d['cosmosThroughput'][0]['error']}")
        a("")
    else:
        a(f"Provisioning includes a {d['cosmosThroughput'][0].get('headroomFactor')}x headroom factor over")
        a("the steady-state mean, because Cosmos bills provisioned throughput rather than")
        a("consumption — sizing to the mean throttles on burst.")
        a("")

    if d["cosmosPerOperationAt50Rps"]:
        a("### Cost if the entire 50 RPS workload were a single operation")
        a("")
        a("| Operation | Measured RU/request | RU/s at 50 RPS | Provisioned RU/s | $/month |")
        a("| --- | ---: | ---: | ---: | ---: |")
        for op, r in d["cosmosPerOperationAt50Rps"].items():
            a(f"| `{op}` | {r['measuredRuPerRequest']} | {r['ruPerSecondRequired']:,} | "
              f"{r['provisionedRuPerSec']:,} | ${r['monthlyThroughputUsd']:,.2f} |")
        a("")
        a("This is the single most decision-relevant table in the cost analysis: it prices")
        a("the difference between an API that serves whole orders and one that serves")
        a("business blocks.")
        a("")

    cs = d["cosmosStorage"]
    if cs.get("monthlyUsd") is not None:
        a(f"**Cosmos storage:** {cs['hotGb']:,.0f} GB at ${cs['pricePerGbMonth']}/GB/month = "
          f"**${cs['monthlyUsd']:,.2f}/month**.")
        a("")

    a("## Azure SQL Database - deployment options")
    a("")
    a("| Tier | vCores | $/vCore/hr | Compute $/mo | Storage GB | Storage $/mo | Total $/mo |")
    a("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for r in d["sqlOptions"]:
        if "error" in r:
            continue
        a(f"| {r['tier']} | {r['vCores']:g} | ${r['unitPricePerVCoreHour']} | "
          f"${r['monthlyComputeUsd']:,.2f} | {r['storageGb']:,.0f} | "
          f"${r['monthlyStorageUsd']:,.2f} | **${r['monthlyTotalUsd']:,.2f}** |")
    a("")
    a("Serverless is billed per second while the database is active; the figures above")
    a("assume it is active continuously (`activeFraction = 1.0`), which is the correct")
    a("assumption for a 50 RPS always-on API. Serverless only saves money when the")
    a("database can actually auto-pause.")
    a("")

    ar = d["archive"]
    a("## Raw archive")
    a("")
    a("| Tier | $/GB/month | Archive GB | $/month |")
    a("| --- | ---: | ---: | ---: |")
    if ar.get("monthlyHotUsd") is not None:
        a(f"| Blob Hot LRS | ${ar['hotPricePerGbMonth']} | {ar['archiveGb']:,.0f} | ${ar['monthlyHotUsd']:,.2f} |")
    if ar.get("monthlyCoolUsd") is not None:
        a(f"| Blob Cool LRS | ${ar['coolPricePerGbMonth']} | {ar['archiveGb']:,.0f} | ${ar['monthlyCoolUsd']:,.2f} |")
    a("")
    a("The archive holds every version, gzipped. Moving it to Cool (or Archive) tier is")
    a("the cheapest lever in the whole design, and it is available regardless of which")
    a("operational database is chosen.")
    a("")

    fb = d["fabric"]
    a("## Microsoft Fabric")
    a("")
    if fb.get("pricePerCuHour"):
        a(f"| SKU | CUs | $/month (24×7) |")
        a("| --- | ---: | ---: |")
        a(f"| F2 | 2 | ${fb['f2MonthlyUsd']:,.2f} |")
        a(f"| F4 | 4 | ${fb['f4MonthlyUsd']:,.2f} |")
        a(f"| F64 | 64 | ${fb['f64MonthlyUsd']:,.2f} |")
        a("")
        a(f"Derived from the Capacity Usage meter at ${fb['pricePerCuHour']}/CU/hour. "
          f"{fb['note']}")
        if fb.get("oneLakePricePerGbMonth"):
            a(f" OneLake storage: ${fb['oneLakePricePerGbMonth']}/GB/month.")
        a("")
        a("Fabric capacity is a **flat cost that does not vary with the operational")
        a("database choice**, so it is neutral in the SQL-vs-Cosmos comparison. It can be")
        a("paused when not in use, which is how this POC controlled its cost.")
        a("")
    else:
        a("> Fabric capacity pricing could not be retrieved; see the unavailable-meters list.")
        a("")

    a("## Assumptions and their labels")
    a("")
    a("| Item | Basis |")
    a("| --- | --- |")
    a("| Cosmos RU per operation | **MEASURED** from benchmark runs |")
    a("| Cosmos write RU per order | **MEASURED** from ingestion runs |")
    a("| Unit prices | **VERIFIED** live from the Azure Retail Prices API on the date above |")
    a("| Storage volumes | **MODELLED** — see [RETENTION_ANALYSIS.md](RETENTION_ANALYSIS.md), "
      "driven by ASSUMED order and version rates |")
    a("| 1.5x RU headroom | **ASSUMPTION** — engineering judgement for burst tolerance |")
    a("| SQL vCore sizing | **MEASURED** requirement from the benchmark, see "
      "[BENCHMARK_SUMMARY.md](../results/BENCHMARK_SUMMARY.md) |")
    a("| Serverless active fraction 1.0 | **ASSUMPTION** — an always-on API never auto-pauses |")
    a("")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
