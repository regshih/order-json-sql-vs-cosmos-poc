"""Operational load harness.

Open-model (constant arrival rate) generator: requests are issued on a fixed
schedule regardless of whether earlier ones have finished. This is deliberate -
a closed-model generator (N workers looping) silently reduces offered load when
the system slows down and hides latency behind coordinated omission, which is
exactly the failure mode that matters at 50 RPS with multi-megabyte payloads.

Latency is recorded as *scheduled-to-complete* so queueing delay is visible.

    python loadtests/operational/run_load.py \
        --base-url http://10.60.1.4:8000 --backend sql \
        --workload mix --rps 50 --duration 60 --warmup 15

Writes machine-readable JSON to results/{backend}/ and never prints numbers
that are not in that file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import random
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# Workload shapes (§16). Values are relative weights.
WORKLOADS: dict[str, dict[str, float]] = {
    # A - summary only
    "summary": {"summary": 1.0},
    # B - single business block
    "title": {"title": 1.0},
    "cdf": {"cdf": 1.0},
    "block": {"title": 0.5, "cdf": 0.5},
    # C - full order (the multi-megabyte case)
    "full": {"full": 1.0},
    # D - realistic mix
    "mix": {"summary": 0.50, "title": 0.20, "cdf": 0.15, "checklist": 0.10, "full": 0.05},
    # E - search
    "search": {"search": 1.0},
}


def build_url(base: str, op: str, order: dict[str, Any], rng: random.Random,
              customers: list[str], states: list[str]) -> str:
    oid = order["orderId"]
    if op == "summary":
        return f"{base}/orders/{oid}/summary"
    if op == "full":
        return f"{base}/orders/{oid}"
    if op == "search":
        # Rotate through the three documented search shapes.
        pick = rng.random()
        if pick < 0.4:
            c = rng.choice(customers)
            return f"{base}/orders?customerId={c}&status=Closed&limit=50"
        if pick < 0.7:
            return f"{base}/orders?state={rng.choice(states)}&limit=50"
        return f"{base}/orders?minLoanAmount={rng.choice([250000, 500000, 1000000])}&limit=50"
    return f"{base}/orders/{oid}/{op}"


class Recorder:
    def __init__(self) -> None:
        self.samples: list[dict[str, Any]] = []

    def add(self, op: str, latency_ms: float, status: int, nbytes: int,
            payload_bytes: int | None, queue_ms: float, error: str | None) -> None:
        self.samples.append({
            "op": op, "latencyMs": latency_ms, "status": status, "bytes": nbytes,
            "payloadBytes": payload_bytes, "queueMs": queue_ms, "error": error,
        })


async def worker(client: httpx.AsyncClient, url: str, op: str, scheduled: float,
                 rec: Recorder, payload_bytes: int | None, sem: asyncio.Semaphore) -> None:
    async with sem:
        queue_ms = (time.perf_counter() - scheduled) * 1000
        t0 = time.perf_counter()
        status, nbytes, error = 0, 0, None
        try:
            resp = await client.get(url)
            status = resp.status_code
            nbytes = len(resp.content)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:180]
        latency = (time.perf_counter() - t0) * 1000
        # Latency from the moment the request was *scheduled*, not dispatched.
        rec.add(op, latency + queue_ms, status, nbytes, payload_bytes, queue_ms, error)


async def run_phase(base: str, orders: list[dict[str, Any]], weights: dict[str, float],
                    rps: float, duration: float, rec: Recorder, seed: int,
                    max_inflight: int, customers: list[str], states: list[str],
                    size_filter: tuple[int, int] | None) -> None:
    rng = random.Random(seed)
    ops = list(weights)
    ws = [weights[o] for o in ops]

    pool = orders
    if size_filter:
        lo, hi = size_filter
        pool = [o for o in orders if lo <= (o.get("payloadBytes") or 0) <= hi] or orders

    limits = httpx.Limits(max_connections=max_inflight + 32, max_keepalive_connections=max_inflight + 32)
    sem = asyncio.Semaphore(max_inflight)
    tasks: list[asyncio.Task] = []

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0), limits=limits,
                                 follow_redirects=False) as client:
        start = time.perf_counter()
        interval = 1.0 / rps
        n = 0
        while True:
            target = start + n * interval
            now = time.perf_counter()
            if target - now > 0:
                await asyncio.sleep(target - now)
            if time.perf_counter() - start >= duration:
                break
            op = rng.choices(ops, weights=ws, k=1)[0]
            order = rng.choice(pool)
            url = build_url(base, op, order, rng, customers, states)
            tasks.append(asyncio.create_task(
                worker(client, url, op, target, rec, order.get("payloadBytes"), sem)
            ))
            n += 1
            if len(tasks) > 20000:
                tasks = [t for t in tasks if not t.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * q
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return round(s[f] + (s[c] - s[f]) * (k - f), 2)


def summarise(rec: Recorder, duration: float, target_rps: float) -> dict[str, Any]:
    ok = [s for s in rec.samples if s["status"] == 200 and not s["error"]]
    errs = [s for s in rec.samples if s["status"] != 200 or s["error"]]

    def block(rows: list[dict[str, Any]]) -> dict[str, Any]:
        lat = [r["latencyMs"] for r in rows]
        byt = [r["bytes"] for r in rows]
        q = [r["queueMs"] for r in rows]
        if not rows:
            return {"count": 0}
        return {
            "count": len(rows),
            "latencyMs": {"p50": pct(lat, 0.5), "p90": pct(lat, 0.9), "p95": pct(lat, 0.95),
                          "p99": pct(lat, 0.99), "max": round(max(lat), 2),
                          "mean": round(statistics.fmean(lat), 2)},
            "queueMs": {"p50": pct(q, 0.5), "p95": pct(q, 0.95), "max": round(max(q), 2)},
            "responseBytes": {"p50": int(pct([float(b) for b in byt], 0.5)),
                              "p95": int(pct([float(b) for b in byt], 0.95)),
                              "mean": int(statistics.fmean(byt)), "max": max(byt)},
            "throughputMBps": round(sum(byt) / duration / 1024**2, 2),
        }

    by_op: dict[str, list[dict[str, Any]]] = {}
    for s in ok:
        by_op.setdefault(s["op"], []).append(s)

    # Latency vs payload size - the question the brief actually asks.
    buckets = [(0, 750_000, "<0.75MB"), (750_000, 1_250_000, "0.75-1.25MB"),
               (1_250_000, 2_000_000, "1.25-2MB"), (2_000_000, 4_000_000, "2-4MB"),
               (4_000_000, 10**9, ">4MB")]
    by_size = {}
    full_ok = [s for s in ok if s["op"] == "full" and s["payloadBytes"]]
    for lo, hi, label in buckets:
        rows = [s for s in full_ok if lo <= s["payloadBytes"] < hi]
        if rows:
            by_size[label] = block(rows)

    total_bytes = sum(s["bytes"] for s in ok)
    error_kinds: dict[str, int] = {}
    for e in errs:
        key = e["error"] or f"HTTP {e['status']}"
        error_kinds[key.split(":")[0]] = error_kinds.get(key.split(":")[0], 0) + 1

    return {
        "targetRps": target_rps,
        "durationSec": round(duration, 2),
        "requestsIssued": len(rec.samples),
        "requestsOk": len(ok),
        "requestsFailed": len(errs),
        "errorRatePct": round(len(errs) / max(len(rec.samples), 1) * 100, 3),
        "achievedRps": round(len(rec.samples) / duration, 2),
        "achievedOkRps": round(len(ok) / duration, 2),
        "totalResponseBytes": total_bytes,
        "throughputMBps": round(total_bytes / duration / 1024**2, 2),
        "overall": block(ok),
        "byOperation": {k: block(v) for k, v in sorted(by_op.items())},
        "byPayloadSize": by_size,
        "errorKinds": error_kinds,
    }


async def fetch_orders(base: str, limit: int) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=120.0) as c:
        r = await c.get(f"{base}/_bench/orders", params={"limit": limit})
        r.raise_for_status()
        return r.json()["orders"]


async def server_metrics(base: str, reset: bool = False) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=120.0) as c:
        if reset:
            await c.post(f"{base}/_bench/metrics/reset")
            return {}
        r = await c.get(f"{base}/_bench/metrics")
        return r.json() if r.status_code == 200 else {"error": r.status_code}


async def main_async(args: argparse.Namespace) -> None:
    base = args.base_url.rstrip("/")
    orders = await fetch_orders(base, args.order_pool)
    if not orders:
        raise SystemExit("no orders available from /_bench/orders - ingest data first")
    print(f"loaded {len(orders)} order ids from {base}")

    customers = sorted({o["customerId"] for o in orders})
    states = ["CA", "TX", "FL", "NY", "WA", "AZ", "CO", "GA", "OH", "NC"]
    weights = WORKLOADS[args.workload]
    size_filter = None
    if args.size_min or args.size_max:
        size_filter = (args.size_min or 0, args.size_max or 10**9)

    # Warmup: fills connection pools, JIT-warms the app, wakes a serverless DB.
    if args.warmup > 0:
        print(f"warmup {args.warmup}s @ {args.rps} rps ...")
        await run_phase(base, orders, weights, args.rps, args.warmup, Recorder(),
                        args.seed, args.max_inflight, customers, states, size_filter)

    await server_metrics(base, reset=True)

    runs: list[dict[str, Any]] = []
    for rep in range(args.repeats):
        rec = Recorder()
        print(f"steady-state run {rep + 1}/{args.repeats}: {args.workload} @ {args.rps} rps "
              f"for {args.duration}s ...")
        t0 = time.perf_counter()
        await run_phase(base, orders, weights, args.rps, args.duration, rec,
                        args.seed + rep, args.max_inflight, customers, states, size_filter)
        elapsed = time.perf_counter() - t0
        s = summarise(rec, elapsed, args.rps)
        runs.append(s)
        o = s["overall"]
        print(f"  achieved {s['achievedRps']} rps  p50={o['latencyMs']['p50']}ms "
              f"p95={o['latencyMs']['p95']}ms p99={o['latencyMs']['p99']}ms "
              f"errors={s['requestsFailed']} {s['throughputMBps']} MB/s")

    srv = await server_metrics(base)

    out = {
        "meta": {
            "runUtc": datetime.now(timezone.utc).isoformat(),
            "backend": args.backend,
            "workload": args.workload,
            "workloadWeights": weights,
            "targetRps": args.rps,
            "durationSec": args.duration,
            "warmupSec": args.warmup,
            "repeats": args.repeats,
            "maxInflight": args.max_inflight,
            "baseUrl": base,
            "orderPoolSize": len(orders),
            "sizeFilter": size_filter,
            "loadClient": {"host": platform.node(), "python": platform.python_version()},
            "note": args.note,
        },
        "runs": runs,
        "aggregate": _aggregate(runs),
        "serverMetrics": srv,
    }

    outdir = Path(args.out) / args.backend
    outdir.mkdir(parents=True, exist_ok=True)
    n = len(list(outdir.glob("run-*.json"))) + 1
    path = outdir / f"run-{n:03d}-{args.workload}-{int(args.rps)}rps.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"-> {path}")


def _aggregate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    if len(runs) == 1:
        return runs[0]
    return {
        "repeats": len(runs),
        "achievedRps": round(statistics.fmean(r["achievedRps"] for r in runs), 2),
        "errorRatePct": round(statistics.fmean(r["errorRatePct"] for r in runs), 3),
        "throughputMBps": round(statistics.fmean(r["throughputMBps"] for r in runs), 2),
        "latencyMs": {
            k: round(statistics.fmean(r["overall"]["latencyMs"][k] for r in runs), 2)
            for k in ("p50", "p95", "p99", "max", "mean")
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--backend", required=True, help="sql | cosmos | fabric")
    ap.add_argument("--workload", choices=sorted(WORKLOADS), default="mix")
    ap.add_argument("--rps", type=float, default=50)
    ap.add_argument("--duration", type=float, default=60)
    ap.add_argument("--warmup", type=float, default=15)
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--max-inflight", type=int, default=256,
                    help="safety valve; if reached the system is past its practical limit")
    ap.add_argument("--order-pool", type=int, default=2000)
    ap.add_argument("--size-min", type=int, default=0)
    ap.add_argument("--size-max", type=int, default=0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="results")
    ap.add_argument("--note", default="")
    args = ap.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
