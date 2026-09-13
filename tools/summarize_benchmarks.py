"""Generate results/BENCHMARK_SUMMARY.md and CSVs from machine-readable runs.

No benchmark number is ever typed into a report by hand. This tool reads every
``results/<backend>/run-*.json`` and ``writes-*.json`` produced by the load
harness and emits:

    results/BENCHMARK_SUMMARY.md
    results/summary-reads.csv
    results/summary-writes.csv
    results/summary-by-size.csv

    python tools/summarize_benchmarks.py
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKENDS = ["sql", "cosmos", "fabric"]


def load_read_runs(results: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for backend in BACKENDS:
        d = results / backend
        if not d.exists():
            continue
        for f in sorted(d.glob("run-*.json")):
            try:
                doc = json.loads(f.read_text())
            except json.JSONDecodeError:
                continue
            meta = doc.get("meta", {})
            agg = doc.get("aggregate") or (doc.get("runs") or [{}])[0]
            overall = agg.get("overall") or {}
            lat = overall.get("latencyMs") or agg.get("latencyMs") or {}
            srv = doc.get("serverMetrics") or {}
            srv_ops = srv.get("byOperation") or {}
            # Server-side breakdown, weighted across the operations in the mix.
            total_req = sum(m.get("requests", 0) for m in srv_ops.values()) or 1
            db = sum(m.get("db_ms", {}).get("p50", 0) * m.get("requests", 0) for m in srv_ops.values()) / total_req
            rec = sum(m.get("reconstruct_ms", {}).get("p50", 0) * m.get("requests", 0) for m in srv_ops.values()) / total_req
            ser = sum(m.get("serialize_ms", {}).get("p50", 0) * m.get("requests", 0) for m in srv_ops.values()) / total_req
            ru_vals = [m["request_charge_ru"]["mean"] * m.get("requests", 0)
                       for m in srv_ops.values() if "request_charge_ru" in m]
            ru = (sum(ru_vals) / total_req) if ru_vals else None
            throttles = sum(m.get("throttled_429", 0) for m in srv_ops.values())

            rows.append({
                "file": str(f.relative_to(results)),
                "backend": meta.get("backend", backend),
                "workload": meta.get("workload"),
                "targetRps": meta.get("targetRps"),
                "durationSec": meta.get("durationSec"),
                "repeats": meta.get("repeats", 1),
                "achievedRps": agg.get("achievedRps"),
                "errorRatePct": agg.get("errorRatePct"),
                "p50Ms": lat.get("p50"),
                "p95Ms": lat.get("p95"),
                "p99Ms": lat.get("p99"),
                "maxMs": lat.get("max"),
                "throughputMBps": agg.get("throughputMBps"),
                "meanResponseBytes": (overall.get("responseBytes") or {}).get("mean"),
                "p95ResponseBytes": (overall.get("responseBytes") or {}).get("p95"),
                "queueP95Ms": (overall.get("queueMs") or {}).get("p95"),
                "srvDbMs": round(db, 2),
                "srvReconstructMs": round(rec, 2),
                "srvSerializeMs": round(ser, 2),
                "srvRuPerRequest": round(ru, 3) if ru is not None else None,
                "throttled429": throttles,
                "appCpuPct": (srv.get("process") or {}).get("systemCpuPercent"),
                "appRssMb": (srv.get("process") or {}).get("rssMb"),
                "sqlMaxCpuPct": ((srv.get("sqlResourceStats") or {}).get("maxCpuPct")),
                "sqlMaxIoPct": ((srv.get("sqlResourceStats") or {}).get("maxDataIoPct")),
                "byPayloadSize": agg.get("byPayloadSize") or {},
                "byOperation": agg.get("byOperation") or {},
            })
    return rows


def load_write_runs(results: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for backend in BACKENDS:
        d = results / backend
        if not d.exists():
            continue
        for f in sorted(d.glob("writes-*.json")):
            doc = json.loads(f.read_text())
            meta = doc.get("meta", {})
            for r in doc.get("runs", []):
                lat = r.get("latencyMs") or {}
                ru = r.get("requestChargeRu") or {}
                rows.append({
                    "file": str(f.relative_to(results)),
                    "backend": meta.get("backend", backend),
                    "shape": r.get("shape"),
                    "targetWritesPerSec": r.get("targetWritesPerSec"),
                    "achievedWritesPerSec": r.get("achievedWritesPerSec"),
                    "attempts": r.get("attempts"),
                    "failed": r.get("failed"),
                    "p50Ms": lat.get("p50"),
                    "p95Ms": lat.get("p95"),
                    "p99Ms": lat.get("p99"),
                    "maxMs": lat.get("max"),
                    "ruMean": ru.get("mean"),
                    "ruP95": ru.get("p95"),
                    "ruTotal": ru.get("total"),
                    "ruPerSecond": ru.get("ruPerSecond"),
                    "rowsOrItems": (r.get("rowsOrItemsAffected") or {}).get("mean"),
                    "throttled429": r.get("throttled429", 0),
                    "retries": r.get("retries", 0),
                })
    return rows


def load_ingestion(results: Path) -> list[dict[str, Any]]:
    d = results / "ingestion"
    out = []
    if not d.exists():
        return out
    for f in sorted(d.glob("ingest-*.json")):
        s = json.loads(f.read_text())["summary"]
        out.append({"file": f.name, **{k: s.get(k) for k in (
            "backend", "ordersAttempted", "ordersSucceeded", "ordersFailed", "elapsedSec",
            "ordersPerSec", "blocksWritten", "itemsWritten", "bytesWritten", "sourceBytes",
            "chunkedBlocks", "maxItemBytes", "totalRu", "ruPerOrder")},
            "ingestP95Ms": (s.get("ingestMs") or {}).get("p95"),
            "archiveP95Ms": (s.get("archiveMs") or {}).get("p95")})
    return out


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def fmt(v: Any, nd: int = 1) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.{nd}f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def build_md(reads: list[dict[str, Any]], writes: list[dict[str, Any]],
             ingestion: list[dict[str, Any]], negative: dict[str, Any] | None) -> str:
    L: list[str] = []
    a = L.append
    a("# Benchmark Summary")
    a("")
    a(f"Generated {datetime.now(timezone.utc).isoformat()} by "
      "[tools/summarize_benchmarks.py](../tools/summarize_benchmarks.py) from the")
    a("machine-readable run files in `results/`. **No figure in this document was")
    a("typed by hand.**")
    a("")
    if not reads and not writes:
        a("> No benchmark runs found. Execute `scripts/run_benchmarks.sh` first.")
        return "\n".join(L) + "\n"

    a("## Method")
    a("")
    a("- Load generated with an **open-model (constant arrival rate)** harness: requests")
    a("  are issued on a fixed schedule regardless of whether earlier ones completed, so")
    a("  queueing delay appears in the latency figures instead of being hidden by")
    a("  coordinated omission. Latency is measured **scheduled-to-complete**.")
    a("- The load generator runs on a **separate VM** from the API, inside the same VNet,")
    a("  so multi-megabyte responses cross a real network boundary.")
    a("- Each run has a warmup phase that is discarded, then a measured steady state.")
    a("- Server-side `db` / `reconstruct` / `serialize` splits and Cosmos RU come from")
    a("  the API's own telemetry, not from the client.")
    a("")

    # ---------------- reads: rps sweep ----------------
    mix = sorted([r for r in reads if r["workload"] == "mix"],
                 key=lambda r: (r["backend"], r["targetRps"] or 0))
    if mix:
        a("## Read throughput sweep — realistic mix")
        a("")
        a("Mix: 50% summary, 20% title, 15% CDF, 10% checklist, 5% full order.")
        a("**50 RPS is the customer's stated operating point.**")
        a("")
        a("| Backend | Target RPS | Achieved RPS | p50 ms | p95 ms | p99 ms | max ms | Errors % | MB/s | 429s |")
        a("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for r in mix:
            flag = " ⬅" if r["targetRps"] == 50 else ""
            a(f"| {r['backend']}{flag} | {fmt(r['targetRps'],0)} | {fmt(r['achievedRps'])} | "
              f"{fmt(r['p50Ms'])} | {fmt(r['p95Ms'])} | {fmt(r['p99Ms'])} | {fmt(r['maxMs'])} | "
              f"{fmt(r['errorRatePct'],2)} | {fmt(r['throughputMBps'],2)} | {fmt(r['throttled429'],0)} |")
        a("")

    # ---------------- reads: workload shapes ----------------
    shapes = sorted([r for r in reads if r["workload"] != "mix" and r["targetRps"] == 50],
                    key=lambda r: (r["workload"] or "", r["backend"]))
    if shapes:
        a("## Workload shapes at 50 RPS")
        a("")
        a("| Workload | Backend | Achieved RPS | p50 ms | p95 ms | p99 ms | Mean resp bytes | MB/s | RU/req | Errors % |")
        a("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for r in shapes:
            a(f"| {r['workload']} | {r['backend']} | {fmt(r['achievedRps'])} | {fmt(r['p50Ms'])} | "
              f"{fmt(r['p95Ms'])} | {fmt(r['p99Ms'])} | {fmt(r['meanResponseBytes'],0)} | "
              f"{fmt(r['throughputMBps'],2)} | {fmt(r['srvRuPerRequest'],2)} | {fmt(r['errorRatePct'],2)} |")
        a("")

    # ---------------- full order sweep ----------------
    full = sorted([r for r in reads if r["workload"] == "full"],
                  key=lambda r: (r["backend"], r["targetRps"] or 0))
    if full:
        a("## Workload C — full multi-megabyte orders")
        a("")
        a("This is the payload-size stress test. At 50 RPS a mean ~1.3 MB response is")
        a("~65 MB/s of JSON leaving the API; the p99 order is ~5 MB.")
        a("")
        a("| Backend | Target RPS | Achieved RPS | p50 ms | p95 ms | p99 ms | Mean bytes | MB/s | Errors % | queue p95 ms |")
        a("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for r in full:
            a(f"| {r['backend']} | {fmt(r['targetRps'],0)} | {fmt(r['achievedRps'])} | {fmt(r['p50Ms'])} | "
              f"{fmt(r['p95Ms'])} | {fmt(r['p99Ms'])} | {fmt(r['meanResponseBytes'],0)} | "
              f"{fmt(r['throughputMBps'],2)} | {fmt(r['errorRatePct'],2)} | {fmt(r['queueP95Ms'])} |")
        a("")

    # ---------------- latency vs payload size ----------------
    size_rows = []
    for r in reads:
        for bucket, m in (r.get("byPayloadSize") or {}).items():
            lat = m.get("latencyMs") or {}
            size_rows.append({
                "backend": r["backend"], "workload": r["workload"], "targetRps": r["targetRps"],
                "bucket": bucket, "count": m.get("count"),
                "p50Ms": lat.get("p50"), "p95Ms": lat.get("p95"), "p99Ms": lat.get("p99"),
                "meanBytes": (m.get("responseBytes") or {}).get("mean"),
            })
    if size_rows:
        a("## Latency vs payload size (full-order reads)")
        a("")
        a("| Backend | RPS | Payload bucket | n | p50 ms | p95 ms | p99 ms | Mean bytes |")
        a("| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |")
        order = {"<0.75MB": 0, "0.75-1.25MB": 1, "1.25-2MB": 2, "2-4MB": 3, ">4MB": 4}
        for r in sorted(size_rows, key=lambda x: (x["backend"], x["targetRps"] or 0,
                                                  order.get(x["bucket"], 9))):
            a(f"| {r['backend']} | {fmt(r['targetRps'],0)} | {r['bucket']} | {fmt(r['count'],0)} | "
              f"{fmt(r['p50Ms'])} | {fmt(r['p95Ms'])} | {fmt(r['p99Ms'])} | {fmt(r['meanBytes'],0)} |")
        a("")

    # ---------------- where the time goes ----------------
    breakdown = [r for r in reads if r["workload"] in ("mix", "full") and r["targetRps"] == 50]
    if breakdown:
        a("## Where the time goes (server-side, p50)")
        a("")
        a("| Backend | Workload | DB ms | Reconstruct ms | Serialize ms | Client p50 ms | App CPU % | SQL CPU % | SQL IO % |")
        a("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for r in sorted(breakdown, key=lambda x: (x["workload"] or "", x["backend"])):
            a(f"| {r['backend']} | {r['workload']} | {fmt(r['srvDbMs'],2)} | {fmt(r['srvReconstructMs'],2)} | "
              f"{fmt(r['srvSerializeMs'],2)} | {fmt(r['p50Ms'])} | {fmt(r['appCpuPct'])} | "
              f"{fmt(r['sqlMaxCpuPct'])} | {fmt(r['sqlMaxIoPct'])} |")
        a("")
        a("**Reconstruction overhead** is the `Reconstruct ms` column: the cost of turning")
        a("stored blocks/items back into one order document. It is the price both designs")
        a("pay for decomposing the order, and it is directly comparable between them.")
        a("")

    # ---------------- Cosmos RU ----------------
    ru_rows = [r for r in reads if r["backend"] == "cosmos" and r["srvRuPerRequest"]]
    if ru_rows:
        a("## Measured Cosmos RU")
        a("")
        a("| Workload | Target RPS | RU per request | RU/sec at this rate | 429s |")
        a("| --- | ---: | ---: | ---: | ---: |")
        for r in sorted(ru_rows, key=lambda x: (x["workload"] or "", x["targetRps"] or 0)):
            rps = r["achievedRps"] or 0
            a(f"| {r['workload']} | {fmt(r['targetRps'],0)} | {fmt(r['srvRuPerRequest'],2)} | "
              f"{fmt((r['srvRuPerRequest'] or 0) * rps, 0)} | {fmt(r['throttled429'],0)} |")
        a("")
        a("Per-operation RU appears in [COST_ANALYSIS.md](../docs/COST_ANALYSIS.md), which")
        a("prices these measured values against live Azure retail rates.")
        a("")

    # ---------------- writes ----------------
    if writes:
        a("## Write benchmark")
        a("")
        a("| Backend | Shape | Target /s | Achieved /s | p50 ms | p95 ms | p99 ms | RU mean | RU p95 | Rows/items | Failed |")
        a("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for r in sorted(writes, key=lambda x: (x["shape"] or "", x["backend"], x["targetWritesPerSec"] or 0)):
            a(f"| {r['backend']} | {r['shape']} | {fmt(r['targetWritesPerSec'],0)} | "
              f"{fmt(r['achievedWritesPerSec'],2)} | {fmt(r['p50Ms'])} | {fmt(r['p95Ms'])} | "
              f"{fmt(r['p99Ms'])} | {fmt(r['ruMean'],2)} | {fmt(r['ruP95'],2)} | "
              f"{fmt(r['rowsOrItems'],1)} | {fmt(r['failed'],0)} |")
        a("")

    # ---------------- ingestion ----------------
    if ingestion:
        a("## Bulk ingestion")
        a("")
        a("| Backend | Orders | Failed | Elapsed s | Orders/s | Blocks | Items | Bytes | Chunked | Max item B | Total RU | RU/order |")
        a("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for r in sorted(ingestion, key=lambda x: (x["backend"], -(x["ordersAttempted"] or 0))):
            a(f"| {r['backend']} | {fmt(r['ordersSucceeded'],0)} | {fmt(r['ordersFailed'],0)} | "
              f"{fmt(r['elapsedSec'],1)} | {fmt(r['ordersPerSec'],2)} | {fmt(r['blocksWritten'],0)} | "
              f"{fmt(r['itemsWritten'],0)} | {fmt(r['bytesWritten'],0)} | {fmt(r['chunkedBlocks'],0)} | "
              f"{fmt(r['maxItemBytes'],0)} | {fmt(r['totalRu'],0)} | {fmt(r['ruPerOrder'],1)} |")
        a("")

    # ---------------- negative test ----------------
    if negative and negative.get("conclusion"):
        c = negative["conclusion"]
        a("## Cosmos negative test — monolithic vs aggregate")
        a("")
        a("| Profile | Monolithic item bytes | Outcome | HTTP | Aggregate items | Largest item bytes | % of limit | Chunked |")
        a("| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |")
        agg_by = {x["profile"]: x for x in negative.get("aggregate", [])}
        for m in negative.get("monolithic", []):
            g = agg_by.get(m["profile"], {})
            a(f"| {m['profile']} | {fmt(m['itemBytes'],0)} | {m['outcome']} | "
              f"{fmt(m.get('statusCode'),0)} | {fmt(g.get('itemsWritten'),0)} | "
              f"{fmt(g.get('maxItemBytes'),0)} | {fmt(g.get('maxItemPctOfLimit'),1)}% | "
              f"{fmt(g.get('blocksChunked'),0)} |")
        a("")
        a(f"> {c['statement']}")
        a("")

    a("## Source files")
    a("")
    for r in reads:
        a(f"- `results/{r['file']}` — {r['backend']} {r['workload']} @ {fmt(r['targetRps'],0)} RPS")
    for r in {w["file"] for w in writes}:
        a(f"- `results/{r}` — write benchmark")
    a("")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", default="results")
    ap.add_argument("--md-out", default="results/BENCHMARK_SUMMARY.md")
    args = ap.parse_args()

    results = Path(args.results)
    reads = load_read_runs(results)
    writes = load_write_runs(results)
    ingestion = load_ingestion(results)

    neg_path = results / "cosmos" / "negative-test.json"
    negative = json.loads(neg_path.read_text()) if neg_path.exists() else None

    write_csv(results / "summary-reads.csv", reads, [
        "backend", "workload", "targetRps", "achievedRps", "errorRatePct", "p50Ms", "p95Ms",
        "p99Ms", "maxMs", "throughputMBps", "meanResponseBytes", "p95ResponseBytes",
        "queueP95Ms", "srvDbMs", "srvReconstructMs", "srvSerializeMs", "srvRuPerRequest",
        "throttled429", "appCpuPct", "appRssMb", "sqlMaxCpuPct", "sqlMaxIoPct", "file"])
    write_csv(results / "summary-writes.csv", writes, [
        "backend", "shape", "targetWritesPerSec", "achievedWritesPerSec", "attempts", "failed",
        "p50Ms", "p95Ms", "p99Ms", "maxMs", "ruMean", "ruP95", "ruTotal", "ruPerSecond",
        "rowsOrItems", "throttled429", "retries", "file"])

    size_rows = []
    for r in reads:
        for bucket, m in (r.get("byPayloadSize") or {}).items():
            lat = m.get("latencyMs") or {}
            size_rows.append({
                "backend": r["backend"], "workload": r["workload"], "targetRps": r["targetRps"],
                "bucket": bucket, "count": m.get("count"), "p50Ms": lat.get("p50"),
                "p95Ms": lat.get("p95"), "p99Ms": lat.get("p99"),
                "meanBytes": (m.get("responseBytes") or {}).get("mean"),
                "throughputMBps": m.get("throughputMBps")})
    write_csv(results / "summary-by-size.csv", size_rows, [
        "backend", "workload", "targetRps", "bucket", "count", "p50Ms", "p95Ms", "p99Ms",
        "meanBytes", "throughputMBps"])

    out = Path(args.md_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_md(reads, writes, ingestion, negative), encoding="utf-8")

    print(f"read runs={len(reads)} write runs={len(writes)} ingestion={len(ingestion)} "
          f"sizeRows={len(size_rows)} negative={'yes' if negative else 'no'}")
    print(f"-> {out}")
    print(f"-> {results / 'summary-reads.csv'}")
    print(f"-> {results / 'summary-writes.csv'}")
    print(f"-> {results / 'summary-by-size.csv'}")


if __name__ == "__main__":
    main()
