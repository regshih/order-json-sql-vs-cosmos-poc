"""End-to-end ingestion: source -> raw archive -> operational backend.

    python -m ingestion.run_ingest --backend sql    --orders 200 --seed 42
    python -m ingestion.run_ingest --backend cosmos --orders 200 --seed 42
    python -m ingestion.run_ingest --backend both   --orders 200 --seed 42

Orders are generated on the fly from the deterministic generator (so the same
seed produces the identical logical dataset in both backends - which is what
makes the contract tests and the benchmark comparison valid), archived to the
immutable raw layer, then written to the operational store.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from generator.synthetic_order_generator import build_order, calibrated_profiles  # noqa: E402
from ingestion.archive.raw_archive import build_archive  # noqa: E402
from app.repositories.base import OrderRepository  # noqa: E402


def make_repo(backend: str) -> OrderRepository:
    if backend == "sql":
        from app.repositories.sql_repository import SqlOrderRepository

        return SqlOrderRepository()
    if backend == "cosmos":
        from app.repositories.cosmos_repository import CosmosOrderRepository

        return CosmosOrderRepository()
    raise ValueError(f"unknown backend {backend!r}")


def iter_orders(seed: int, count: int, customers: int, profile: str | None):
    profiles = calibrated_profiles(seed=seed)
    if profile:
        profiles = [p for p in profiles if p.name == profile]
        if not profiles:
            raise SystemExit(f"unknown profile {profile}")
    import random

    pick = random.Random(seed ^ 0x5EED)
    weights = [p.weight for p in profiles]
    for i in range(count):
        prof = pick.choices(profiles, weights=weights, k=1)[0]
        customer_id = f"POC{(i % customers) + 1:03d}"
        yield i, prof, customer_id, build_order(seed, i, prof, customer_id)


def ingest_one(
    repo: OrderRepository,
    archive,
    doc: dict[str, Any],
    profile_name: str,
    skip_archive: bool,
) -> dict[str, Any]:
    details = doc["ExtractData"]["ExtractObjects"][0]["ObjectDetails"]
    customer_id = doc["ExtractDetails"]["CustomerSerialNumber"]
    order_id, version = details["OrderID"], details["OrderVersion"]

    blob = json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    payload_hash = hashlib.sha256(blob).hexdigest()

    archive_uri = None
    archive_ms = 0.0
    if not skip_archive:
        t0 = time.perf_counter()
        try:
            res = archive.put(customer_id, order_id, version, blob)
            archive_uri = res.uri
        except FileExistsError:
            from ingestion.archive.raw_archive import archive_path

            archive_uri = archive_path(customer_id, order_id, version, archive.compress)
        archive_ms = (time.perf_counter() - t0) * 1000

    result = repo.ingest_order(doc, archive_uri=archive_uri, payload_hash=payload_hash)
    return {
        "orderId": order_id,
        "customerId": customer_id,
        "version": version,
        "profile": profile_name,
        "sourceBytes": len(blob),
        "blocks": result.blocks_written,
        "items": result.items_written,
        "bytes": result.bytes_written,
        "chunkedBlocks": result.chunked_blocks,
        "maxItemBytes": result.max_item_bytes,
        "ru": result.request_charge,
        "ingestMs": round(result.duration_ms, 2),
        "archiveMs": round(archive_ms, 2),
        "extra": result.extra,
    }


def run(backend: str, args: argparse.Namespace) -> dict[str, Any]:
    repo = make_repo(backend)
    archive = build_archive()
    run_id = str(uuid.uuid4())
    started = datetime.now(timezone.utc)

    print(f"[{backend}] ingesting {args.orders} orders (seed={args.seed}, workers={args.workers})")
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    t0 = time.perf_counter()

    def work(payload):
        i, prof, _cust, doc = payload
        return ingest_one(repo, archive, doc, prof.name, args.skip_archive)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for payload in iter_orders(args.seed, args.orders, args.customers, args.profile):
            futures[pool.submit(work, payload)] = payload[0]
            # Bound in-flight work so we never hold thousands of multi-MB docs.
            if len(futures) >= args.workers * 4:
                done = next(as_completed(futures))
                idx = futures.pop(done)
                _collect(done, idx, rows, failures)
        for fut in as_completed(futures):
            _collect(fut, futures[fut], rows, failures)

    elapsed = time.perf_counter() - t0
    ok = len(rows)
    summary = {
        "runId": run_id,
        "backend": backend,
        "startedUtc": started.isoformat(),
        "elapsedSec": round(elapsed, 2),
        "seed": args.seed,
        "ordersAttempted": args.orders,
        "ordersSucceeded": ok,
        "ordersFailed": len(failures),
        "ordersPerSec": round(ok / elapsed, 2) if elapsed else 0,
        "blocksWritten": sum(r["blocks"] for r in rows),
        "itemsWritten": sum(r["items"] for r in rows),
        "bytesWritten": sum(r["bytes"] for r in rows),
        "sourceBytes": sum(r["sourceBytes"] for r in rows),
        "chunkedBlocks": sum(r["chunkedBlocks"] for r in rows),
        "maxItemBytes": max((r["maxItemBytes"] for r in rows), default=0),
        "totalRu": round(sum(r["ru"] for r in rows), 2),
        "ruPerOrder": round(statistics.fmean([r["ru"] for r in rows]), 2) if rows and any(r["ru"] for r in rows) else 0,
        "ingestMs": _stats([r["ingestMs"] for r in rows]),
        "archiveMs": _stats([r["archiveMs"] for r in rows]) if not args.skip_archive else None,
        "failures": failures[:20],
    }

    out = Path(args.out) / f"ingest-{backend}-{started:%Y%m%dT%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "orders": rows}, indent=2), encoding="utf-8")

    print(
        f"[{backend}] {ok}/{args.orders} ok in {elapsed:.1f}s "
        f"({summary['ordersPerSec']}/s) blocks={summary['blocksWritten']} "
        f"bytes={summary['bytesWritten']:,} "
        + (f"RU={summary['totalRu']:,} ({summary['ruPerOrder']}/order) " if summary["totalRu"] else "")
        + f"-> {out}"
    )
    if failures:
        print(f"[{backend}] FAILURES: {len(failures)}; first: {failures[0]['error'][:200]}")

    repo.close()
    return summary


def _collect(fut, idx, rows, failures) -> None:
    try:
        rows.append(fut.result())
    except Exception as exc:
        failures.append({"index": idx, "error": f"{type(exc).__name__}: {exc}"})


def _stats(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    s = sorted(values)

    def p(q: float) -> float:
        k = (len(s) - 1) * q
        f = int(k)
        c = min(f + 1, len(s) - 1)
        return round(s[f] + (s[c] - s[f]) * (k - f), 2)

    return {
        "min": round(s[0], 2), "p50": p(0.5), "p95": p(0.95), "p99": p(0.99),
        "max": round(s[-1], 2), "mean": round(statistics.fmean(s), 2),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backend", choices=["sql", "cosmos", "both"], required=True)
    ap.add_argument("--orders", type=int, default=200)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--customers", type=int, default=8)
    ap.add_argument("--profile", help="restrict to one size profile")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--skip-archive", action="store_true")
    ap.add_argument("--out", default="results/ingestion")
    args = ap.parse_args()

    backends = ["sql", "cosmos"] if args.backend == "both" else [args.backend]
    for b in backends:
        run(b, args)


if __name__ == "__main__":
    main()
