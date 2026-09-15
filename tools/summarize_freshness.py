"""Summarize repeated freshness runs into a machine-readable artifact.

Freshness was the one measurement in this POC quoted from a single run's stdout
rather than generated from a committed file. This tool closes that gap: it reads
every `analytics-*.json` in `results/fabric-analytics/`, collects the
`freshness` blocks, and emits medians and ranges per backend so the documents
can cite a distribution instead of one observation.

Reporting a range matters more here than it does for the latency benchmarks. The
dominant term is `pushToVisibleSec` - the Fabric replicator's own merge and
metadata-sync cadence - which is a polling cycle, not a per-request cost, so a
single sample lands wherever in that cycle the write happened to fall. A
difference between two single runs is not evidence of a difference between the
two paths.

    python tools/summarize_freshness.py
    python tools/summarize_freshness.py --out artifacts/freshness.json
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FIELDS = ("writeMs", "pushSec", "pushToVisibleSec", "endToEndSec")


def collect(src: Path) -> tuple[dict[str, list[dict[str, Any]]], list[str], int]:
    by_backend: dict[str, list[dict[str, Any]]] = {}
    files: list[str] = []
    incomplete = 0
    for p in sorted(src.glob("analytics-*.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        runs = doc.get("freshness") or []
        if not runs:
            continue
        files.append(p.name)
        for r in runs:
            if not r.get("visible"):
                incomplete += 1
                continue
            by_backend.setdefault(r["backend"], []).append(r)
    return by_backend, files, incomplete


def stats(runs: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"samples": len(runs)}
    for f in FIELDS:
        vals = [r[f] for r in runs if isinstance(r.get(f), (int, float))]
        if not vals:
            continue
        out[f] = {
            "median": round(statistics.median(vals), 2),
            "min": round(min(vals), 2),
            "max": round(max(vals), 2),
            "n": len(vals),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default="results/fabric-analytics")
    ap.add_argument("--out", default="artifacts/freshness.json")
    args = ap.parse_args()

    src = Path(args.src)
    by_backend, files, incomplete = collect(src)
    if not by_backend:
        print(f"NO_FRESHNESS_RUNS in {src}")
        return 1

    report = {
        "generatedUtc": datetime.now(timezone.utc).isoformat(),
        "sourceFiles": files,
        "runsNotVisible": incomplete,
        "note": ("pushToVisibleSec is the Fabric replicator merge + SQL endpoint "
                 "metadata sync; it dominates endToEndSec and is a polling cadence, "
                 "so the range matters more than the median."),
        "byBackend": {b: stats(r) for b, r in sorted(by_backend.items())},
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"freshness over {len(files)} run file(s)"
          + (f", {incomplete} run(s) never became visible" if incomplete else ""))
    for b, s in report["byBackend"].items():
        print(f"\n{b}  (n={s['samples']})")
        for f in FIELDS:
            if f in s:
                v = s[f]
                unit = "ms" if f.endswith("Ms") else "s"
                print(f"   {f:<18} median {v['median']:>8,.2f} {unit}"
                      f"   range {v['min']:,.2f} - {v['max']:,.2f}")
    print(f"\n-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
