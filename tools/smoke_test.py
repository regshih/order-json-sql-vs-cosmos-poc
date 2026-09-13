"""HTTP smoke test: exercise every endpoint against a running API.

    python tools/smoke_test.py --base-url http://127.0.0.1:8000

Prints a table and exits non-zero if any required endpoint fails, so it can
gate a benchmark run.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import httpx

BLOCK_ENDPOINTS = ["summary", "", "title", "cdf", "notes", "checklist", "parties"]

SEARCHES = [
    {"customerId": "POC001", "limit": 20},
    {"state": "CA", "limit": 20},
    {"minLoanAmount": 500000, "limit": 20},
    {"customerId": "POC001", "status": "Closed", "limit": 20},
    {"customerId": "POC002", "state": "TX", "minLoanAmount": 100000, "limit": 20},
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--orders", type=int, default=3, help="how many order ids to exercise")
    ap.add_argument("--json-out")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")

    results: list[dict[str, Any]] = []
    failures = 0

    with httpx.Client(timeout=180.0) as c:
        health = c.get(f"{base}/health")
        print(f"health: {health.status_code} {health.text[:200]}")
        if health.status_code != 200:
            return 2
        backend = health.json()["backend"]

        pool = c.get(f"{base}/_bench/orders", params={"limit": 200}).json()["orders"]
        if not pool:
            print("FAIL: no orders in the backend - run ingestion first")
            return 3
        # Pick a spread of payload sizes so the big-payload path is covered.
        pool.sort(key=lambda o: o.get("payloadBytes") or 0)
        picks = [pool[0], pool[len(pool) // 2], pool[-1]][: args.orders]

        print(f"\nbackend={backend}  orders in store={len(pool)}")
        print(f"{'order':<10} {'endpoint':<10} {'http':>5} {'bytes':>10} {'ms':>8}  note")
        print("-" * 70)

        for o in picks:
            oid = o["orderId"]
            for ep in BLOCK_ENDPOINTS:
                url = f"{base}/orders/{oid}" + (f"/{ep}" if ep else "")
                t0 = time.perf_counter()
                try:
                    r = c.get(url)
                    ms = (time.perf_counter() - t0) * 1000
                    note = ""
                    if r.status_code == 200 and ep == "":
                        body = r.json()
                        od = body["ExtractData"]["ExtractObjects"][0]["ObjectData"]
                        note = f"{len(od)} sections"
                    ok = r.status_code == 200
                    results.append({"orderId": oid, "endpoint": ep or "full",
                                    "status": r.status_code, "bytes": len(r.content),
                                    "ms": round(ms, 1)})
                    if not ok:
                        failures += 1
                        note = r.text[:80]
                    print(f"{oid[:8]:<10} {ep or 'full':<10} {r.status_code:>5} "
                          f"{len(r.content):>10,} {ms:>8.1f}  {note}")
                except Exception as exc:
                    failures += 1
                    print(f"{oid[:8]:<10} {ep or 'full':<10} {'ERR':>5} {'-':>10} {'-':>8}  {exc}")

        print("\nsearch:")
        for q in SEARCHES:
            t0 = time.perf_counter()
            r = c.get(f"{base}/orders", params=q)
            ms = (time.perf_counter() - t0) * 1000
            count = r.json()["count"] if r.status_code == 200 else -1
            if r.status_code != 200:
                failures += 1
            print(f"  {json.dumps(q):<62} http={r.status_code} count={count:<4} {ms:7.1f} ms")
            results.append({"endpoint": "search", "query": q, "status": r.status_code,
                            "count": count, "ms": round(ms, 1)})

        print("\n404 handling:")
        r = c.get(f"{base}/orders/00000000-0000-0000-0000-000000000000/summary")
        print(f"  unknown order -> {r.status_code} (expected 404)")
        if r.status_code != 404:
            failures += 1

        metrics = c.get(f"{base}/_bench/metrics").json()

    print(f"\nserver-side timing breakdown ({backend}):")
    for op, m in metrics.get("byOperation", {}).items():
        line = (f"  {op:<10} n={m['requests']:<4} total p50={m['total_ms']['p50']:>7.1f} "
                f"p95={m['total_ms']['p95']:>7.1f}  db={m['db_ms']['p50']:>6.1f} "
                f"recon={m['reconstruct_ms']['p50']:>6.1f} ser={m['serialize_ms']['p50']:>6.1f} "
                f"bytes={m['response_bytes']['p50']:>9,}")
        if "request_charge_ru" in m:
            line += f"  RU p50={m['request_charge_ru']['p50']:.2f} p95={m['request_charge_ru']['p95']:.2f}"
        print(line)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump({"backend": backend, "results": results, "serverMetrics": metrics}, f, indent=2)
        print(f"\n-> {args.json_out}")

    print(f"\n{'SMOKE_OK' if failures == 0 else f'SMOKE_FAILED ({failures} failures)'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
