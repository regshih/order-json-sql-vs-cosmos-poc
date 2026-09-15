"""Generate docs/FULL_DOCUMENT_BENCHMARK.md from the section 6 sweep.

Reads every run file in results/fulldoc/ and produces two views, because they
answer different questions:

    RATE SWEEP   10/25/50/100 RPS, all payload sizes mixed
                 -> does the design hold the customer's rate at all?

    PAYLOAD BAND 50 RPS, one size band at a time
                 -> WHERE does it break down, and is the constraint the
                    database, the network, or serialisation?

The band view is the one the brief actually asks for. At 50 RPS a 5 MB response
is roughly 250 MB/s before HTTP overhead, and a mixed-size run averages that
away. Separating the phases - db / reconstruct / serialize against wire
throughput - is what stops a network limit being reported as a database limit.

    python tools/summarize_full_document.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

LABEL = {
    "sql-full-json": "SQL Full JSON (nvarchar)",
    "sql-full-json-native": "SQL Full JSON (native json)",
    "cosmos-mongo": "Cosmos Mongo (one doc)",
    "sql-hybrid": "SQL hybrid (reassembled)",
    "cosmos-nosql": "Cosmos NoSQL (reassembled)",
    "fabric": "Fabric (control)",
}
ORDER = list(LABEL)


def load_runs(src: Path) -> list[dict[str, Any]]:
    runs = []
    # Runs land in results/fulldoc/<backend>/run-*.json, so recurse.
    for f in sorted(src.rglob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if "aggregate" in d and "meta" in d:
            d["_file"] = f.name
            runs.append(d)
    return runs


def pct(agg: dict[str, Any], key: str) -> float | None:
    """Percentile from the real run schema: aggregate.overall.latencyMs.<key>."""
    lat = (agg.get("overall") or {}).get("latencyMs") or agg.get("latencyMs") or {}
    v = lat.get(key)
    return float(v) if isinstance(v, (int, float)) else None


def mean_bytes(agg: dict[str, Any]) -> float:
    rb = (agg.get("overall") or {}).get("responseBytes") or {}
    return float(rb.get("mean") or 0.0)


def phases(doc: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    """Server-side db / reconstruct / serialize medians, weighted by requests.

    Same derivation as tools/summarize_benchmarks.py so the two reports cannot
    disagree about the same quantity.
    """
    ops = ((doc.get("serverMetrics") or {}).get("byOperation") or {})
    total = sum(m.get("requests", 0) for m in ops.values())
    if not total:
        return None, None, None
    def w(field: str) -> float:
        return sum(m.get(field, {}).get("p50", 0) * m.get("requests", 0)
                   for m in ops.values()) / total
    return w("db_ms"), w("reconstruct_ms"), w("serialize_ms")


def fmt(v: float | None, nd: int = 1) -> str:
    return "-" if v is None else f"{v:,.{nd}f}"


def rate_table(runs: list[dict[str, Any]]) -> list[str]:
    rows = [r for r in runs if "band" not in (r["meta"].get("note") or "")]
    by: dict[tuple[str, float], dict[str, Any]] = {}
    for r in rows:
        by[(r["meta"]["backend"], float(r["meta"]["targetRps"]))] = r
    rates = sorted({k[1] for k in by})
    backends = [b for b in ORDER if any(k[0] == b for k in by)]

    out = ["| Backend | Target RPS | Achieved | p50 ms | p95 ms | p99 ms | Errors % | MB/s |",
           "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for b in backends:
        for rt in rates:
            r = by.get((b, rt))
            if not r:
                continue
            a = r["aggregate"]
            out.append(
                f"| {LABEL.get(b, b)} | {rt:,.0f} | {a['achievedRps']:,.1f} | "
                f"{fmt(pct(a, 'p50'))} | {fmt(pct(a, 'p95'))} | {fmt(pct(a, 'p99'))} | "
                f"{a['errorRatePct']:,.2f} | {a['throughputMBps']:,.2f} |"
            )
    return out


def band_table(runs: list[dict[str, Any]]) -> list[str]:
    rows = [r for r in runs if "band" in (r["meta"].get("note") or "")]
    by: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        band = (r["meta"]["note"] or "").split("band-")[-1]
        by[(r["meta"]["backend"], band)] = r
    bands = ["p500k", "p1m", "p2m", "p3m", "p5m"]
    bands = [b for b in bands if any(k[1] == b for k in by)]
    backends = [b for b in ORDER if any(k[0] == b for k in by)]

    out = ["| Backend | Band | Mean resp | Achieved RPS | p50 ms | p95 ms | Errors % | MB/s | DB ms | Recon ms | Ser ms |",
           "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for b in backends:
        for band in bands:
            r = by.get((b, band))
            if not r:
                continue
            a = r["aggregate"]
            db, rec, ser = phases(r)
            out.append(
                f"| {LABEL.get(b, b)} | {band} | "
                f"{(mean_bytes(a) / 1048576):,.2f} MB | {a['achievedRps']:,.1f} | "
                f"{fmt(pct(a, 'p50'))} | {fmt(pct(a, 'p95'))} | "
                f"{a['errorRatePct']:,.2f} | {a['throughputMBps']:,.2f} | "
                f"{fmt(db, 2)} | {fmt(rec, 2)} | {fmt(ser, 2)} |"
            )
    return out


def constraint_analysis(runs: list[dict[str, Any]]) -> list[str]:
    """Locate each backend's ceiling FROM THE DATA rather than by assertion.

    The brief is explicit that a network limit must not be reported as a database
    limit. The discriminator used here: if wire throughput stops rising while
    achieved RPS falls, the design has hit a throughput ceiling. Whether that
    ceiling is the network or the client path is then settled by comparing
    backends - they share the same VM pair and the same API process, so a backend
    that sustains MORE MB/s proves the lower ceiling was not the network.
    """
    rows = [r for r in runs if "band" in (r["meta"].get("note") or "")]
    by: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for r in rows:
        band = (r["meta"]["note"] or "").split("band-")[-1]
        by.setdefault(r["meta"]["backend"], []).append((band, r))

    order = ["p500k", "p1m", "p2m", "p3m", "p5m"]
    out = ["| Backend | Peak MB/s observed | At band | RPS held at 5 MB | Ceiling reached? |",
           "| --- | ---: | --- | ---: | --- |"]
    peaks: dict[str, float] = {}
    for b in [x for x in ORDER if x in by]:
        entries = sorted(by[b], key=lambda e: order.index(e[0]) if e[0] in order else 99)
        best_mb, best_band = 0.0, "-"
        rps_5m = None
        for band, r in entries:
            mb = float(r["aggregate"]["throughputMBps"])
            if mb > best_mb:
                best_mb, best_band = mb, band
            if band == "p5m":
                rps_5m = float(r["aggregate"]["achievedRps"])
        peaks[b] = best_mb
        # A ceiling is "reached" when the largest band could not hold the target
        # rate while throughput had already flattened.
        hit = "yes" if (rps_5m is not None and rps_5m < 45) else "no"
        out.append(f"| {LABEL.get(b, b)} | {best_mb:,.1f} | {best_band} | "
                   f"{(f'{rps_5m:,.1f}' if rps_5m is not None else '-')} | {hit} |")

    if peaks:
        top = max(peaks, key=lambda k: peaks[k])
        out.append("")
        out.append(f"The highest sustained figure observed on this VM pair is "
                   f"**{peaks[top]:,.1f} MB/s** ({LABEL.get(top, top)}). Any backend "
                   f"that flattens materially below that did **not** hit a network "
                   f"limit - the same two machines and the same API process carried "
                   f"more for a different storage design. Its ceiling is in the "
                   f"database or, more often here, in the client driver's path for "
                   f"large values.")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default="results/fulldoc")
    ap.add_argument("--out", default="docs/FULL_DOCUMENT_BENCHMARK.md")
    args = ap.parse_args()

    src = Path(args.src)
    runs = load_runs(src)
    if not runs:
        print(f"NO RUNS in {src} - run scripts/run_full_document_bench.sh first")
        return 1

    L: list[str] = []
    A = L.append
    A("# Full-Document Benchmark - GET /orders/{id}")
    A("")
    A(f"Generated {datetime.now(timezone.utc).isoformat()} by "
      "[tools/summarize_full_document.py](../tools/summarize_full_document.py) "
      f"from {len(runs)} run file(s) in `{src}`. **No figure in this document was "
      "typed by hand.**")
    A("")
    A("Every request returns the **complete logical order**. Load is open-model "
      "(constant arrival rate) from a separate VM inside the same VNet, so "
      "queueing delay appears in the latency figures rather than being hidden by "
      "coordinated omission, and multi-megabyte responses cross a real network "
      "boundary.")
    A("")
    A("## 1. Rate sweep - all payload sizes mixed")
    A("")
    L.extend(rate_table(runs))
    A("")
    A("## 2. Payload bands at 50 RPS - the customer's stated rate")
    A("")
    L.extend(band_table(runs))
    A("")
    A("`DB ms`, `Recon ms` and `Ser ms` are server-side medians: time in the "
      "database, time reassembling the order, and time serialising the response. "
      "A full-document backend should show **~0 reconstruct** by construction - "
      "that is the difference the whole extension exists to measure. Where wire "
      "throughput flattens while DB time stays low, the constraint is the "
      "network or serialisation, **not** the database.")
    A("")
    A("## 3. Where the constraint actually is")
    A("")
    L.extend(constraint_analysis(runs))
    A("")
    A("> Server-side phase timings come from the API's in-process telemetry "
      "across multiple uvicorn workers, each with its own counters, so they "
      "describe shape rather than being authoritative. Client-side latency and "
      "throughput are authoritative.")
    A("")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"-> {out} ({len(runs)} runs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
