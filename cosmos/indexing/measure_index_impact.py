"""Measure the effect of the Cosmos indexing policy on RU, latency and storage (§10).

Runs the same set of operations and records the measured request charge, so the
indexing policy can be changed between runs and the two results compared
directly rather than argued about.

    python -m cosmos.indexing.measure_index_impact --label before-orderid-index
    # ... change the indexing policy ...
    python -m cosmos.indexing.measure_index_impact --label after-orderid-index
    python -m cosmos.indexing.measure_index_impact --compare

Results accumulate in results/cosmos/index-impact.json.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

RESULTS = Path("results/cosmos/index-impact.json")


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * q
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return round(s[f] + (s[c] - s[f]) * (k - f), 3)


def stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 3),
        "p50": pct(values, 0.5),
        "p95": pct(values, 0.95),
        "max": round(max(values), 3),
        "total": round(sum(values), 2),
    }


def measure(label: str, samples: int) -> dict[str, Any]:
    from app.repositories.cosmos_repository import DOC_TYPE_HEADER, CosmosOrderRepository
    from app.telemetry.metrics import measure as measure_req

    repo = CosmosOrderRepository()
    props = repo.container.read()
    pool = repo.list_order_ids(samples * 2)
    if not pool:
        raise SystemExit("no orders in Cosmos - ingest first")
    targets = pool[:samples]

    out: dict[str, Any] = {
        "label": label,
        "measuredUtc": datetime.now(timezone.utc).isoformat(),
        "samples": len(targets),
        "indexingPolicy": {
            "includedPaths": [p["path"] for p in props["indexingPolicy"].get("includedPaths", [])],
            "excludedPaths": [p["path"] for p in props["indexingPolicy"].get("excludedPaths", [])],
        },
        "operations": {},
    }

    # -- 1. the cross-partition lookup, in isolation --------------------
    # This is the query every read pays before it can do a point read, because
    # the API contract carries no tenant in the route.
    lookup_ru, lookup_ms = [], []
    for t in targets:
        t0 = time.perf_counter()
        list(repo.container.query_items(
            query=("SELECT TOP 1 c.customerId, c.orderVersion FROM c "
                   "WHERE c.orderId = @oid AND c.docType = @dt"),
            parameters=[{"name": "@oid", "value": t["orderId"]},
                        {"name": "@dt", "value": DOC_TYPE_HEADER}],
            enable_cross_partition_query=True,
        ))
        lookup_ms.append((time.perf_counter() - t0) * 1000)
        lookup_ru.append(float(
            repo.container.client_connection.last_response_headers.get("x-ms-request-charge", 0)))
    out["operations"]["crossPartitionLookup"] = {"ru": stats(lookup_ru), "latencyMs": stats(lookup_ms)}

    # -- 2. point read with the full partition key (the ideal case) ------
    from app.repositories.cosmos_repository import header_id

    pr_ru, pr_ms = [], []
    for t in targets:
        t0 = time.perf_counter()
        try:
            repo.container.read_item(
                item=header_id(t["orderId"], t["orderVersion"]),
                partition_key=[t["customerId"], t["orderId"]])
            pr_ms.append((time.perf_counter() - t0) * 1000)
            pr_ru.append(float(
                repo.container.client_connection.last_response_headers.get("x-ms-request-charge", 0)))
        except Exception:
            continue
    out["operations"]["pointReadWithFullPk"] = {"ru": stats(pr_ru), "latencyMs": stats(pr_ms)}

    # -- 3. single-partition full-order query ---------------------------
    fo_ru, fo_ms, fo_items = [], [], []
    for t in targets:
        t0 = time.perf_counter()
        items = list(repo.container.query_items(
            query=("SELECT c.docType, c.blockType, c.blockSubType, c.sequence, c.data "
                   "FROM c WHERE c.customerId = @cid AND c.orderId = @oid AND c.orderVersion = @v"),
            parameters=[{"name": "@cid", "value": t["customerId"]},
                        {"name": "@oid", "value": t["orderId"]},
                        {"name": "@v", "value": t["orderVersion"]}],
            partition_key=[t["customerId"], t["orderId"]],
        ))
        fo_ms.append((time.perf_counter() - t0) * 1000)
        fo_items.append(len(items))
        fo_ru.append(float(
            repo.container.client_connection.last_response_headers.get("x-ms-request-charge", 0)))
    out["operations"]["singlePartitionFullOrder"] = {
        "ru": stats(fo_ru), "latencyMs": stats(fo_ms),
        "itemsPerOrder": round(statistics.fmean(fo_items), 1) if fo_items else 0,
    }

    # -- 4. tenant-scoped search ----------------------------------------
    customers = sorted({t["customerId"] for t in targets})
    s_ru, s_ms = [], []
    for c in customers:
        t0 = time.perf_counter()
        list(repo.container.query_items(
            query=("SELECT TOP 50 c.orderId, c.search FROM c "
                   "WHERE c.docType = @dt AND c.customerId = @cid AND c.search.status = @st"),
            parameters=[{"name": "@dt", "value": DOC_TYPE_HEADER},
                        {"name": "@cid", "value": c},
                        {"name": "@st", "value": "Closed"}],
            partition_key=[c],
        ))
        s_ms.append((time.perf_counter() - t0) * 1000)
        s_ru.append(float(
            repo.container.client_connection.last_response_headers.get("x-ms-request-charge", 0)))
    out["operations"]["tenantScopedSearch"] = {"ru": stats(s_ru), "latencyMs": stats(s_ms)}

    # -- 5. write cost (indexing shows up here) --------------------------
    from generator.synthetic_order_generator import build_order, calibrated_profiles

    profiles = {p.name: p for p in calibrated_profiles(seed=42)}
    doc = build_order(42, 990_000 + (1 if "after" in label else 0), profiles["p1m"], "POCIDX")
    res = repo.ingest_order(doc)
    out["operations"]["writeOneOrder"] = {
        "ru": res.request_charge,
        "items": res.items_written,
        "ruPerItem": round(res.request_charge / max(res.items_written, 1), 2),
        "durationMs": round(res.duration_ms, 1),
    }

    # -- 6. storage ------------------------------------------------------
    try:
        usage = list(repo.container.query_items(
            query="SELECT VALUE COUNT(1) FROM c", enable_cross_partition_query=True))
        out["itemCount"] = usage[0] if usage else None
    except Exception:
        out["itemCount"] = None

    repo.close()
    return out


def load_all() -> list[dict[str, Any]]:
    if RESULTS.exists():
        return json.loads(RESULTS.read_text()).get("runs", [])
    return []


def save(runs: list[dict[str, Any]]) -> None:
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps({"runs": runs}, indent=2), encoding="utf-8")


def compare(runs: list[dict[str, Any]]) -> None:
    if len(runs) < 2:
        print("need at least two labelled runs to compare")
        return
    a, b = runs[-2], runs[-1]
    print(f"\ncomparing '{a['label']}' -> '{b['label']}'\n")
    print(f"  indexed paths before: {a['indexingPolicy']['includedPaths']}")
    print(f"  indexed paths after : {b['indexingPolicy']['includedPaths']}\n")
    print(f"{'operation':<28} {'RU before':>11} {'RU after':>11} {'change':>10}   "
          f"{'ms before':>10} {'ms after':>10}")
    print("-" * 88)
    for op in a["operations"]:
        if op not in b["operations"]:
            continue
        oa, ob = a["operations"][op], b["operations"][op]
        if isinstance(oa.get("ru"), dict):
            ra, rb = oa["ru"].get("mean", 0), ob["ru"].get("mean", 0)
            ma, mb = oa["latencyMs"].get("mean", 0), ob["latencyMs"].get("mean", 0)
        else:
            ra, rb = oa.get("ru", 0), ob.get("ru", 0)
            ma, mb = oa.get("durationMs", 0), ob.get("durationMs", 0)
        change = f"{(rb - ra) / ra * 100:+.1f}%" if ra else "n/a"
        print(f"{op:<28} {ra:>11.2f} {rb:>11.2f} {change:>10}   {ma:>10.1f} {mb:>10.1f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--label", help="name this measurement run")
    ap.add_argument("--samples", type=int, default=40)
    ap.add_argument("--compare", action="store_true")
    args = ap.parse_args()

    runs = load_all()
    if args.label:
        print(f"measuring '{args.label}' ({args.samples} samples per operation) ...")
        r = measure(args.label, args.samples)
        runs.append(r)
        save(runs)
        for op, v in r["operations"].items():
            if isinstance(v.get("ru"), dict):
                print(f"  {op:<28} RU mean={v['ru']['mean']:>8.2f} p95={v['ru']['p95']:>8.2f}  "
                      f"ms mean={v['latencyMs']['mean']:>7.1f}")
            else:
                print(f"  {op:<28} RU={v['ru']:>8.2f} items={v.get('items')} "
                      f"ms={v.get('durationMs')}")
        print(f"-> {RESULTS}")
    if args.compare or (args.label and len(runs) >= 2):
        compare(runs)


if __name__ == "__main__":
    main()
