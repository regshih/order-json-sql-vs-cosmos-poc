"""SECTION 8 - what indexing a large nested document actually costs.

The 16 MB documentation says to "avoid using wildcard indexes" with large
documents (SOURCES.md M.1). That is a vendor warning without a number attached.
This puts a number on it.

Four stages, measured on a DEDICATED collection so the benchmark data is never
disturbed:

    1. BASELINE      only the _id index the platform creates
    2. MINIMUM       the four fields the customer workflow actually filters on
    3. WILDCARD      an index over the entire nested payload  <-- the warned-against case
    4. CLEANUP       wildcard dropped, back to MINIMUM

At each stage the same operations are replayed and their RU recorded: insert,
point read by _id, a filtered query, a one-field update, and a whole-document
replace. RU capture pins the pool to one connection, because
`getLastRequestStatistics` is connection-scoped state (SOURCES.md M.7).

The question is not "does a wildcard index work" - it is what it does to WRITE
cost, since on this engine every write rewrites the whole document and must
maintain every index term derived from it.

    python tools/mongo_index_experiment.py
    python tools/mongo_index_experiment.py --orders 5 --profile p1m
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import orjson  # noqa: E402

from generator.synthetic_order_generator import (  # noqa: E402
    build_order,
    calibrated_profiles,
)

# The minimum set, justified per field rather than copied from a template:
#   customerId          tenant scoping - every query is bounded by it
#   _proj.status        the one status filter the order workflow needs
#   _proj.primaryState  title work is state-specific
#   orderVersion        version pinning on read
#
# Deliberately NOT indexed: anything inside the payload. Nothing in the stated
# workflow filters on a nested CDF or Title field, and indexing them is what
# stage 3 exists to price.
MINIMUM_INDEXES = [
    ("customerId", [("customerId", 1)]),
    ("proj_status", [("_proj.status", 1)]),
    ("proj_state", [("_proj.primaryState", 1)]),
    ("order_version", [("orderVersion", 1)]),
]


def measure(repo, envs: list[dict[str, Any]], label: str) -> dict[str, Any]:
    """Replay the same five operations and collect RU + latency."""
    out: dict[str, Any] = {"stage": label, "ops": {}}

    def one(name: str, fn) -> None:
        t0 = time.perf_counter()
        try:
            res = fn()
            ms = (time.perf_counter() - t0) * 1000
            ru = getattr(res, "request_charge", None)
            if ru is None:
                ru = float((repo._last_ru() or {}).get("requestCharge", 0.0))
            out["ops"].setdefault(name, []).append(
                {"ms": round(ms, 2), "ru": round(float(ru or 0.0), 2)})
        except Exception as exc:  # noqa: BLE001
            out["ops"].setdefault(name, []).append(
                {"error": f"{type(exc).__name__}: {exc}"[:200]})

    for env in envs:
        oid = str(env["ExtractData"]["ExtractObjects"][0]["ObjectDetails"]["OrderID"]).lower()
        one("insert", lambda e=env: repo.ingest_order(e))
        one("pointRead", lambda o=oid: repo.col.find_one({"_id": o}))
        one("filteredQuery", lambda: list(
            repo.col.find({"customerId": "CUST-IDXTEST", "_proj.status": "Open"},
                          {"_proj.summary": 1}).limit(20)))
        one("updateOneField", lambda o=oid: repo.update_order_header(o, {"status": "IdxTested"}))
        one("replaceWhole", lambda e=env: repo.ingest_order(e))

    # Collapse to means so the report is readable; raw samples stay in the file.
    summary = {}
    for name, samples in out["ops"].items():
        good = [s for s in samples if "error" not in s]
        if good:
            summary[name] = {
                "ru": round(sum(s["ru"] for s in good) / len(good), 2),
                "ms": round(sum(s["ms"] for s in good) / len(good), 2),
                "n": len(good),
            }
        else:
            summary[name] = {"error": samples[0].get("error"), "n": 0}
    out["mean"] = summary
    return out


def index_names(repo) -> list[str]:
    try:
        return sorted(i["name"] for i in repo.col.list_indexes())
    except Exception as exc:  # noqa: BLE001
        return [f"<listIndexes failed: {type(exc).__name__}>"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--orders", type=int, default=4)
    ap.add_argument("--profile", default="p1m")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--collection", default="orders_indextest")
    ap.add_argument("--keep", action="store_true",
                    help="leave the test collection and wildcard index in place")
    ap.add_argument("--out", default="results/mongo-index-experiment.json")
    args = ap.parse_args()

    from app.repositories.mongo_repository import MongoOrderRepository

    repo = MongoOrderRepository(collection=args.collection, capture_ru=True)
    profiles = {p.name: p for p in calibrated_profiles(seed=args.seed)}
    prof = profiles[args.profile]
    envs = [build_order(args.seed, 970_000 + i, prof, "CUST-IDXTEST")
            for i in range(args.orders)]
    mean_bytes = sum(len(orjson.dumps(e)) for e in envs) / len(envs)

    print(f"collection : {args.collection}")
    print(f"documents  : {args.orders} x {args.profile} (~{mean_bytes / 1048576:.2f} MB each)\n")

    stages: list[dict[str, Any]] = []

    # ---- 1. baseline --------------------------------------------------
    print("1. BASELINE (platform _id index only)")
    print(f"   indexes: {index_names(repo)}")
    stages.append({**measure(repo, envs, "baseline"), "indexes": index_names(repo)})

    # ---- 2. minimum ---------------------------------------------------
    print("\n2. MINIMUM (four workflow fields)")
    for name, spec in MINIMUM_INDEXES:
        try:
            repo.col.create_index(spec, name=name)
        except Exception as exc:  # noqa: BLE001
            print(f"   create {name} failed: {type(exc).__name__}: {exc}"[:160])
    print(f"   indexes: {index_names(repo)}")
    stages.append({**measure(repo, envs, "minimum"), "indexes": index_names(repo)})

    # ---- 3. wildcard - the case the docs warn against ------------------
    print("\n3. WILDCARD over the entire nested payload (documented as discouraged)")
    wildcard_ok = True
    t0 = time.perf_counter()
    try:
        repo.col.create_index([("$**", 1)], name="wildcard_all")
        print(f"   created in {(time.perf_counter() - t0):.1f}s")
    except Exception as exc:  # noqa: BLE001
        wildcard_ok = False
        print(f"   REFUSED after {(time.perf_counter() - t0):.1f}s: "
              f"{type(exc).__name__}: {exc}"[:220])
    print(f"   indexes: {index_names(repo)}")
    if wildcard_ok:
        stages.append({**measure(repo, envs, "wildcard"), "indexes": index_names(repo)})
    else:
        stages.append({"stage": "wildcard", "refused": True,
                       "indexes": index_names(repo)})

    # ---- 4. cleanup ---------------------------------------------------
    if wildcard_ok and not args.keep:
        print("\n4. CLEANUP (drop the wildcard)")
        try:
            repo.col.drop_index("wildcard_all")
        except Exception as exc:  # noqa: BLE001
            print(f"   drop failed: {type(exc).__name__}: {exc}"[:160])
        print(f"   indexes: {index_names(repo)}")
        stages.append({**measure(repo, envs, "after_drop"), "indexes": index_names(repo)})

    # ---- report -------------------------------------------------------
    print(f"\n{'stage':12} {'insert RU':>11} {'read RU':>9} {'query RU':>9} "
          f"{'update RU':>10} {'replace RU':>11}")
    print("-" * 68)
    base = None
    for s in stages:
        if s.get("refused"):
            print(f"{s['stage']:12}  index creation REFUSED by the service")
            continue
        m = s["mean"]
        g = lambda k: m.get(k, {}).get("ru", 0.0)  # noqa: E731
        if base is None:
            base = g("insert")
        print(f"{s['stage']:12} {g('insert'):11,.1f} {g('pointRead'):9,.1f} "
              f"{g('filteredQuery'):9,.1f} {g('updateOneField'):10,.1f} "
              f"{g('replaceWhole'):11,.1f}"
              + (f"   insert x{g('insert') / base:.2f} vs baseline" if base else ""))

    report = {
        "generatedUtc": datetime.now(timezone.utc).isoformat(),
        "collection": args.collection,
        "profile": args.profile,
        "documents": args.orders,
        "meanDocumentBytes": int(mean_bytes),
        "minimumIndexRationale": {
            "customerId": "tenant scoping - every query is bounded by it",
            "_proj.status": "the status filter the order workflow needs",
            "_proj.primaryState": "title work is state-specific",
            "orderVersion": "version pinning on read",
            "_excluded": ("nothing inside the payload: no stated workflow filters "
                          "on a nested CDF or Title field, and stage 3 prices what "
                          "indexing them would cost"),
        },
        "stages": stages,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\n-> {out}")

    if not args.keep:
        try:
            repo.col.drop()
            print(f"dropped test collection {args.collection}")
        except Exception as exc:  # noqa: BLE001
            print(f"could not drop {args.collection}: {type(exc).__name__}")
    repo.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
