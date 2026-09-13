"""Write / ingestion benchmark (§17).

The workload is read-dominated, so writes are benchmarked separately rather than
mixed into the read tests. Four update shapes are measured at configurable rates:

    header   - scalar order-header change (Status, Balance, ...)
    title    - one TITLE business block replaced
    cdf      - one CDF business block replaced
    version  - a complete new order version (full re-ingest)

Runs directly against the repositories (not through HTTP) because the question
is what the *storage engine* costs per write, not what FastAPI adds.

    python loadtests/operational/run_write_bench.py --backend cosmos --rate 5 --duration 30
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from generator.synthetic_order_generator import build_order, calibrated_profiles
from ingestion.parser.block_splitter import parse_envelope, split_object_data

SHAPES = ["header", "title", "cdf", "version"]


def make_repo(backend: str):
    if backend == "sql":
        from app.repositories.sql_repository import SqlOrderRepository

        return SqlOrderRepository()
    if backend == "cosmos":
        from app.repositories.cosmos_repository import CosmosOrderRepository

        return CosmosOrderRepository()
    raise ValueError(backend)


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * q
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return round(s[f] + (s[c] - s[f]) * (k - f), 2)


def summarise(samples: list[dict[str, Any]], elapsed: float) -> dict[str, Any]:
    ok = [s for s in samples if s["ok"]]
    lat = [s["ms"] for s in ok]
    ru = [s["ru"] for s in ok]
    rows = [s["rows"] for s in ok]
    out = {
        "attempts": len(samples),
        "succeeded": len(ok),
        "failed": len(samples) - len(ok),
        "elapsedSec": round(elapsed, 2),
        "achievedWritesPerSec": round(len(samples) / elapsed, 2) if elapsed else 0,
    }
    if ok:
        out["latencyMs"] = {
            "p50": pct(lat, 0.5), "p95": pct(lat, 0.95), "p99": pct(lat, 0.99),
            "max": round(max(lat), 2), "mean": round(statistics.fmean(lat), 2),
        }
        out["bytesWritten"] = sum(s["bytes"] for s in ok)
        out["rowsOrItemsAffected"] = {
            "total": sum(rows), "mean": round(statistics.fmean(rows), 2),
        }
        if any(ru):
            out["requestChargeRu"] = {
                "p50": pct(ru, 0.5), "p95": pct(ru, 0.95), "p99": pct(ru, 0.99),
                "mean": round(statistics.fmean(ru), 3), "max": round(max(ru), 3),
                "total": round(sum(ru), 2),
                "ruPerSecond": round(sum(ru) / elapsed, 2) if elapsed else 0,
            }
        out["retries"] = sum(s.get("retries", 0) for s in ok)
        out["throttled429"] = sum(s.get("throttled429", 0) for s in ok)
    errs: dict[str, int] = {}
    for s in samples:
        if not s["ok"]:
            k = (s.get("error") or "?").split(":")[0]
            errs[k] = errs.get(k, 0) + 1
    out["errorKinds"] = errs
    return out


def do_write(repo, shape: str, order: dict[str, Any], rng: random.Random,
             profiles: dict, seed: int) -> dict[str, Any]:
    oid = order["orderId"]
    t0 = time.perf_counter()
    try:
        if shape == "header":
            res = repo.update_order_header(oid, {
                "Status": rng.choice(["In Process", "Title Review", "Clear to Close", "Closing"]),
                "Balance": round(rng.uniform(0, 250_000), 2),
            })
        elif shape in ("title", "cdf"):
            # Build a realistic replacement block from a freshly generated order,
            # so the payload is the right shape and size rather than a stub.
            doc = build_order(seed, rng.randint(0, 999), profiles["p1m"], order["customerId"])
            blocks = split_object_data(parse_envelope(doc)["objectData"])
            want = ("TITLE", "COMMITMENTS") if shape == "title" else ("CDF", "DISBURSEMENTS")
            block = next(b for b in blocks if (b.block_type, b.block_sub_type) == want)
            res = repo.update_block(oid, block.block_type, block.block_sub_type, 0, block.payload)
        else:  # version
            doc = build_order(seed, rng.randint(0, 999), profiles["p1m"], order["customerId"])
            # Force it onto the existing order id / next version.
            det = doc["ExtractData"]["ExtractObjects"][0]["ObjectDetails"]
            det["OrderID"] = oid
            det["OrderVersion"] = int(order.get("orderVersion", 1)) + 1
            doc["ExtractDetails"]["CustomerSerialNumber"] = order["customerId"]
            res = repo.ingest_order(doc)
        ms = (time.perf_counter() - t0) * 1000
        return {
            "shape": shape, "ok": True, "ms": ms, "ru": res.request_charge,
            "bytes": res.bytes_written,
            "rows": res.extra.get("rowsAffected", res.items_written),
            "retries": res.extra.get("retries", 0),
            "throttled429": res.extra.get("throttled429", 0),
        }
    except Exception as exc:
        return {
            "shape": shape, "ok": False, "ms": (time.perf_counter() - t0) * 1000,
            "ru": 0.0, "bytes": 0, "rows": 0,
            "error": f"{type(exc).__name__}: {exc}"[:200],
        }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backend", choices=["sql", "cosmos"], required=True)
    ap.add_argument("--rates", default="1,5,10", help="writes/sec to test")
    ap.add_argument("--duration", type=float, default=30)
    ap.add_argument("--shapes", default=",".join(SHAPES))
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--order-pool", type=int, default=200)
    ap.add_argument("--out", default="results")
    args = ap.parse_args()

    repo = make_repo(args.backend)
    profiles = {p.name: p for p in calibrated_profiles(seed=42)}
    pool = repo.list_order_ids(args.order_pool)
    if not pool:
        raise SystemExit("no orders in the backend - run ingestion first")
    print(f"{args.backend}: {len(pool)} orders available")

    shapes = [s for s in args.shapes.split(",") if s in SHAPES]
    rates = [float(r) for r in args.rates.split(",")]

    runs: list[dict[str, Any]] = []
    for shape in shapes:
        for rate in rates:
            rng = random.Random(args.seed)
            samples: list[dict[str, Any]] = []
            interval = 1.0 / rate
            start = time.perf_counter()
            n = 0
            while time.perf_counter() - start < args.duration:
                target = start + n * interval
                now = time.perf_counter()
                if target > now:
                    time.sleep(target - now)
                order = rng.choice(pool)
                samples.append(do_write(repo, shape, order, rng, profiles, args.seed))
                n += 1
            elapsed = time.perf_counter() - start
            s = summarise(samples, elapsed)
            s["shape"] = shape
            s["targetWritesPerSec"] = rate
            runs.append(s)
            line = (f"  {shape:<8} {rate:>4g}/s  n={s['attempts']:<4} "
                    f"p50={s.get('latencyMs', {}).get('p50', 0):>8.1f}ms "
                    f"p95={s.get('latencyMs', {}).get('p95', 0):>8.1f}ms "
                    f"fail={s['failed']}")
            if "requestChargeRu" in s:
                r = s["requestChargeRu"]
                line += f"  RU mean={r['mean']:>8.1f} p95={r['p95']:>8.1f} total={r['total']:>10,.0f}"
            print(line)

    out = {
        "meta": {
            "runUtc": datetime.now(timezone.utc).isoformat(),
            "backend": args.backend,
            "durationSecPerRun": args.duration,
            "shapes": shapes,
            "rates": rates,
            "seed": args.seed,
            "orderPoolSize": len(pool),
        },
        "runs": runs,
    }
    d = Path(args.out) / args.backend
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"writes-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.json"
    p.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"-> {p}")
    repo.close()


if __name__ == "__main__":
    main()
