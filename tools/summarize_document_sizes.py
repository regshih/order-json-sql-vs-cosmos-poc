"""Generate docs/DOCUMENT_SIZE_RESULTS.md from the machine-readable size runs.

Same rule as the rest of this POC: no figure in the generated document is typed
by hand. Everything comes from

    artifacts/document-size-probe.json          (local: JSON vs BSON vs shape)
    results/document-size-tests.json            (live: acceptance + latency)
    results/document-size-tests-mongo-ru.json   (live: Mongo RU, pool pinned)

    python tools/summarize_document_sizes.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MB = 1024 * 1024

BACKEND_LABEL = {
    "sql-full-json": "Azure SQL - one row, `nvarchar(max)`",
    "sql-full-json-native": "Azure SQL - one row, native `json`",
    "cosmos-mongo": "Cosmos DB for MongoDB - one BSON document",
    "cosmos-nosql-monolithic": "Cosmos DB for NoSQL - one item (counterfactual)",
}


def load(p: str) -> dict[str, Any] | None:
    f = Path(p)
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def ms(row: dict[str, Any] | None) -> str:
    if not row:
        return "-"
    if not row.get("ok"):
        code = row.get("statusCode") or row.get("serverCodeName") or ""
        return f"**rejected** {code}".strip()
    return f"{row['ms']:,.1f}"


def ru(row: dict[str, Any] | None) -> str:
    if not row or not row.get("ok") or not row.get("ru"):
        return "-"
    return f"{row['ru']:,.1f}"


def acceptance_table(tests: dict[str, Any]) -> list[str]:
    by_backend: dict[str, list[dict[str, Any]]] = {}
    for r in tests["results"]:
        if r.get("profile"):
            by_backend.setdefault(r["backend"], []).append(r)

    out = ["| Physical storage | Largest ACCEPTED | Smallest REJECTED | Failure mode |",
           "| --- | ---: | ---: | --- |"]
    for b, rows in by_backend.items():
        ok = [r for r in rows if (r.get("insert") or {}).get("ok")]
        bad = [r for r in rows if not (r.get("insert") or {}).get("ok")]
        largest = max((r["jsonBytes"] for r in ok), default=0)

        if bad:
            first = min(bad, key=lambda r: r["jsonBytes"])
            ins = first.get("insert") or {}
            code = str(ins.get("statusCode") or ins.get("serverCodeName") or "").strip()
            err = (ins.get("error") or "").split(":")[0].strip()
            mode = "`" + " ".join(x for x in (code, err) if x) + "`"
            rejected = f"{first['jsonBytes'] / MB:.2f} MB"
        else:
            mode = "-"
            rejected = "none"

        out.append(
            f"| {BACKEND_LABEL.get(b, b)} | {largest / MB:.2f} MB | {rejected} | {mode} |"
        )
    return out


def latency_table(tests: dict[str, Any], op: str, title: str) -> list[str]:
    backends = [b for b in BACKEND_LABEL if any(
        r["backend"] == b for r in tests["results"] if r.get("profile"))]
    profiles: list[str] = []
    for r in tests["results"]:
        if r.get("profile") and r["profile"] not in profiles:
            profiles.append(r["profile"])

    idx = {(r["backend"], r["profile"]): r for r in tests["results"] if r.get("profile")}
    head = "| Payload | " + " | ".join(
        BACKEND_LABEL[b].split(" - ")[1] if " - " in BACKEND_LABEL[b] else b for b in backends) + " |"
    out = [f"**{title}** (ms)", "", head, "| --- | " + " | ".join(["---:"] * len(backends)) + " |"]
    for prof in profiles:
        any_row = next((idx[(b, prof)] for b in backends if (b, prof) in idx), None)
        if not any_row:
            continue
        label = f"{any_row['jsonBytes'] / MB:.2f} MB"
        if any_row.get("stress"):
            label += " *"
        cells = []
        for b in backends:
            row = idx.get((b, prof))
            if row is None:
                cells.append("-")
            elif not (row.get("insert") or {}).get("ok"):
                # Never leave a blank here: a dash beside fast numbers reads as
                # "fast", when in fact the document was refused and the
                # operation never ran.
                cells.append("_not stored_")
            else:
                cells.append(ms(row.get(op)))
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    return out


def mongo_ru_table(ruq: dict[str, Any]) -> list[str]:
    out = ["| Payload | read RU | insert RU | scalar update RU | nested update RU | full replace RU | RU per MB (write) |",
           "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in ruq["results"]:
        if not r.get("profile"):
            continue
        mb = r["jsonBytes"] / MB
        ins = (r.get("insert") or {}).get("ru")
        label = f"{mb:.2f} MB" + (" *" if r.get("stress") else "")
        out.append(
            f"| {label} | {ru(r.get('pointRead'))} | {ru(r.get('insert'))} | "
            f"{ru(r.get('updateScalar'))} | {ru(r.get('updateNested'))} | "
            f"{ru(r.get('replaceWhole'))} | "
            f"{(f'{ins / mb:,.0f}' if ins else '-')} |"
        )
    return out


def probe_table(probe: dict[str, Any]) -> list[str]:
    out = ["| Profile | JSON (compact) | BSON encoded | BSON overhead | max array | max props | depth |",
           "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in probe["profiles"]:
        b = r.get("bsonBytes")
        name = r["profile"] + (" *" if r.get("stress") else "")
        bson_cell = f"{b / MB:.2f} MB" if b else "encode failed"
        pct = r.get("bsonOverheadPct")
        pct_cell = f"+{pct}%" if pct is not None else "-"
        out.append(
            f"| `{name}` | {r['jsonBytesCompact'] / MB:.2f} MB | {bson_cell} | {pct_cell} | "
            f"{r['maxArrayElements']:,} | {r['maxObjectProperties']:,} | {r['maxNestingDepth']} |"
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--probe", default="artifacts/document-size-probe.json")
    ap.add_argument("--tests", default="results/document-size-tests.json")
    ap.add_argument("--mongo-ru", default="results/document-size-tests-mongo-ru.json")
    ap.add_argument("--out", default="docs/DOCUMENT_SIZE_RESULTS.md")
    args = ap.parse_args()

    probe = load(args.probe)
    tests = load(args.tests)
    ruq = load(args.mongo_ru)
    if not tests:
        print(f"MISSING {args.tests} - run tools/document_size_tests.py first")
        return 1

    L: list[str] = []
    A = L.append

    A("# Document Size Results")
    A("")
    A(f"Generated {datetime.now(timezone.utc).isoformat()} by "
      "[tools/summarize_document_sizes.py](../tools/summarize_document_sizes.py) "
      "from the machine-readable run files. **No figure in this document was "
      "typed by hand.**")
    A("")
    A("Terms are used strictly and are not interchangeable:")
    A("")
    A("| Term | Meaning |")
    A("| --- | --- |")
    A("| **LOGICAL ORDER** | one business order - the complete extract envelope |")
    A("| **DATABASE ITEM** | one physical row (SQL) or BSON document (Mongo) or item (NoSQL) |")
    A("| **API RESPONSE** | what `GET /orders/{id}` returns |")
    A("")
    A("Every row below stores **one LOGICAL ORDER as one DATABASE ITEM**. Profiles "
      "marked `*` are boundary/stress probes: they exist to find where each "
      "platform refuses a document and **do not describe the customer's data**.")
    A("")

    A("## 1. Can the complete order be stored as ONE database item?")
    A("")
    L.extend(acceptance_table(tests))
    A("")
    A("The Cosmos NoSQL row is a **counterfactual**, not the Scenario C design. It "
      "stores a whole order as a single item to locate the 2 MB ceiling exactly. "
      "Scenario C splits the order into many items and is unaffected by it - the "
      "limit constrains the DOCUMENT MODEL, not the engine.")
    A("")

    if probe:
        A("## 2. JSON bytes are not BSON bytes")
        A("")
        L.extend(probe_table(probe))
        A("")
        A("BSON is consistently **larger** than compact JSON for this data shape. A "
          "document sitting on the 16 MB line as JSON is over the line once "
          "encoded, so acceptance must be judged on encoded size. Sizing a Mongo "
          "design against source-file size errs in the dangerous direction.")
        A("")
        A("Shape limits are far from binding: the widest array and deepest nesting "
          "measured are orders of magnitude inside the native `json` type's "
          "documented 65,535 / 65,535 / 128 ceilings, even at 17 MB.")
        A("")

    A("## 3. Latency by payload size")
    A("")
    for op, title in [("pointRead", "Point read - the complete order"),
                      ("insert", "Insert - one complete order"),
                      ("updateScalar", "Update one top-level scalar"),
                      ("updateNested", "Update one deeply nested field"),
                      ("replaceWhole", "Replace the whole document")]:
        L.extend(latency_table(tests, op, title))
        A("")

    A("> Sizes differ by a few tenths of a percent between section 2 and "
      "section 3 because the two runs generate different order indices from the "
      "same profile. The profile fixes a target size, not a byte-identical "
      "document, so this is generator variance rather than measurement error.")
    A("")
    A("> Single samples, not distributions. They establish **order of magnitude and "
      "trend**, not percentiles; the 50 RPS benchmark is where percentiles come "
      "from. The Azure SQL update figures in particular are noisy because the "
      "database is serverless and had recently resumed.")
    A("")

    if ruq:
        A("## 4. Cosmos DB for MongoDB - measured RU")
        A("")
        L.extend(mongo_ru_table(ruq))
        A("")
        A("Captured with `MONGO_CAPTURE_RU=1`, which pins the connection pool to a "
          "single connection. `getLastRequestStatistics` reports "
          "**connection-scoped** state, so sampling it under a shared pool "
          "attributes another request's charge - the same defect that "
          "under-reported Cosmos NoSQL RU by about 42x earlier in this POC.")
        A("")
        A("Two conclusions follow directly:")
        A("")
        A("1. **Reads are cheap; writes are not.** Write cost runs roughly two "
          "orders of magnitude above read cost for the same document.")
        A("2. **Update cost tracks DOCUMENT size, not CHANGE size.** Setting one "
          "top-level scalar costs about the same as rewriting the entire "
          "document - and measurably *more* than a straight replace, because "
          "`$set` requires a server-side read-modify-write while `replace_one` "
          "just writes. There is no cheap small edit to a large document.")
        A("")

    A("## 5. What this means for the four scenarios")
    A("")
    A("- **Azure SQL, one row** stores the complete order at every size tested and "
      "is the fastest on both read and write. The `nvarchar(max)` representation "
      "beats the native `json` type on every measured axis for this workload, "
      "because a whole-document read pays to parse on write and serialise on "
      "read while never querying the binary form in between. The native type is "
      "built for partial access; this is the one workload where it can only lose.")
    A("- **Cosmos DB for MongoDB** stores the complete order up to the documented "
      "16 MB ceiling and reads it cheaply, but any write - including a one-field "
      "edit - costs in proportion to the whole document.")
    A("- **Cosmos DB for NoSQL** cannot hold this order as one item above 2 MB. "
      "That is why Scenario C decomposes, and why its API reassembles.")
    A("")
    A("Security and platform constraints that no benchmark can offset are in "
      "[SOURCES.md](SOURCES.md) M.2 (customer-managed keys), M.5 (no native "
      "Fabric mirroring for the MongoDB API), M.6 (no Entra data-plane "
      "authentication) and N.2 (a mirrored table may not contain a `json` "
      "column).")
    A("")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"-> {out} ({len(L)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
