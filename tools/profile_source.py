"""Profile the customer-derived source order JSON.

Emits a STRUCTURE-ONLY profile. No customer values are written to the output:
every string is reduced to a length/shape, every GUID to a reference count.
The resulting artifacts are safe to commit.

Usage:
    python tools/profile_source.py \
        --input data/source/150159_Order.MASKED.json.json \
        --json-out artifacts/data-profile.json \
        --md-out docs/DATA_PROFILE.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
RTF_RE = re.compile(r"^\{\\rtf", re.IGNORECASE)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}:\d{2})?")

# A "large text" field is one whose string value exceeds this many bytes.
LARGE_TEXT_BYTES = 512


def compact_bytes(obj: Any) -> int:
    """Serialized UTF-8 byte length with no whitespace - the size that matters
    for Cosmos item limits and for network payload accounting."""
    return len(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def pretty_bytes(obj: Any) -> int:
    return len(json.dumps(obj, indent=2, ensure_ascii=False).encode("utf-8"))


class Walker:
    """Single pass over the document collecting structural statistics."""

    def __init__(self) -> None:
        self.max_depth = 0
        self.empty_strings = 0
        self.empty_objects = 0
        self.empty_arrays = 0
        self.null_values = 0
        self.total_scalars = 0
        self.total_objects = 0
        self.total_arrays = 0
        self.total_keys = 0
        # GUID value -> number of places it appears
        self.guid_values: Counter[str] = Counter()
        # path -> count of GUID-shaped values at that path
        self.guid_paths: Counter[str] = Counter()
        # (path, bytes) for oversized strings
        self.large_text: list[dict[str, Any]] = []
        self.rtf_fields: list[dict[str, Any]] = []
        # path -> list of array lengths seen
        self.array_cardinalities: dict[str, list[int]] = defaultdict(list)
        # path -> set of scalar type names
        self.path_types: dict[str, Counter[str]] = defaultdict(Counter)
        self.date_paths: Counter[str] = Counter()

    def walk(self, node: Any, path: str = "$", depth: int = 1) -> None:
        self.max_depth = max(self.max_depth, depth)

        if isinstance(node, dict):
            self.total_objects += 1
            if not node:
                self.empty_objects += 1
            for key, value in node.items():
                self.total_keys += 1
                self.walk(value, f"{path}.{key}", depth + 1)

        elif isinstance(node, list):
            self.total_arrays += 1
            if not node:
                self.empty_arrays += 1
            self.array_cardinalities[path].append(len(node))
            for item in node:
                # Collapse the index so all elements share one path signature.
                self.walk(item, f"{path}[]", depth + 1)

        else:
            self.total_scalars += 1
            if node is None:
                self.null_values += 1
                self.path_types[path]["null"] += 1
                return
            if isinstance(node, bool):
                self.path_types[path]["bool"] += 1
                return
            if isinstance(node, (int, float)):
                self.path_types[path]["number"] += 1
                return

            # string
            self.path_types[path]["string"] += 1
            if node == "":
                self.empty_strings += 1
                return
            nbytes = len(node.encode("utf-8"))
            if GUID_RE.match(node):
                self.guid_values[node] += 1
                self.guid_paths[path] += 1
            elif DATE_RE.match(node):
                self.date_paths[path] += 1
            if RTF_RE.match(node):
                self.rtf_fields.append({"path": path, "bytes": nbytes})
            elif nbytes >= LARGE_TEXT_BYTES:
                self.large_text.append({"path": path, "bytes": nbytes})


def classify_section(name: str, node: Any, nbytes: int) -> dict[str, Any]:
    """Heuristic classification of a top-level ObjectData property into a
    candidate storage treatment. This drives both the SQL hybrid model and the
    Cosmos aggregate model."""
    is_array = isinstance(node, list)
    is_object = isinstance(node, dict)
    scalar = not (is_array or is_object)

    if scalar:
        treatment = "relational-candidate-scalar"
    elif nbytes < 2_000:
        treatment = "relational-candidate-small" if is_object else "json-block-small"
    elif nbytes < 20_000:
        treatment = "json-block"
    else:
        treatment = "json-block-large-split-candidate"

    return {
        "kind": "array" if is_array else ("object" if is_object else "scalar"),
        "treatment": treatment,
    }


def profile(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    doc = json.loads(text)

    extract_details = doc.get("ExtractDetails", {})
    objects = doc.get("ExtractData", {}).get("ExtractObjects", [])
    first = objects[0] if objects else {}
    object_details = first.get("ObjectDetails", {})
    object_data = first.get("ObjectData", {})

    walker = Walker()
    walker.walk(doc)

    # --- top-level section sizing -------------------------------------------
    sections = []
    for name, node in object_data.items():
        cb = compact_bytes(node)
        info = classify_section(name, node, cb)
        entry = {
            "name": name,
            "compactBytes": cb,
            "prettyBytes": pretty_bytes(node),
            "kind": info["kind"],
            "treatment": info["treatment"],
        }
        if isinstance(node, list):
            entry["arrayLength"] = len(node)
            if node and isinstance(node[0], dict):
                entry["elementKeyCount"] = len(node[0])
                entry["maxElementCompactBytes"] = max(compact_bytes(e) for e in node)
                entry["meanElementCompactBytes"] = round(
                    statistics.fmean(compact_bytes(e) for e in node), 1
                )
        elif isinstance(node, dict):
            entry["keyCount"] = len(node)
            # second-level breakdown for the big composite sections
            if cb >= 10_000:
                entry["children"] = sorted(
                    (
                        {
                            "name": k,
                            "compactBytes": compact_bytes(v),
                            "kind": (
                                "array"
                                if isinstance(v, list)
                                else "object"
                                if isinstance(v, dict)
                                else "scalar"
                            ),
                            "arrayLength": len(v) if isinstance(v, list) else None,
                        }
                        for k, v in node.items()
                    ),
                    key=lambda c: -c["compactBytes"],
                )[:15]
        sections.append(entry)

    sections.sort(key=lambda s: -s["compactBytes"])

    total_compact = compact_bytes(doc)
    total_pretty = len(raw)

    # --- array cardinalities -------------------------------------------------
    arrays = []
    for apath, lengths in walker.array_cardinalities.items():
        arrays.append(
            {
                "path": apath,
                "occurrences": len(lengths),
                "min": min(lengths),
                "max": max(lengths),
                "total": sum(lengths),
            }
        )
    arrays.sort(key=lambda a: -a["total"])

    # --- recurring GUIDs -----------------------------------------------------
    recurring = [c for c in walker.guid_values.values() if c > 1]
    guid_summary = {
        "distinctGuidValues": len(walker.guid_values),
        "totalGuidOccurrences": sum(walker.guid_values.values()),
        "guidValuesAppearingMoreThanOnce": len(recurring),
        "maxOccurrencesOfASingleGuid": max(walker.guid_values.values(), default=0),
        # Paths only - never the values themselves.
        "topGuidPaths": [
            {"path": p, "count": c} for p, c in walker.guid_paths.most_common(25)
        ],
    }

    # --- stable identifiers --------------------------------------------------
    id_paths = sorted(
        {
            p
            for p in walker.path_types
            if re.search(r"(^|\.)(\w*)(ID|Id|Number|Code|Key)$", p.split(".")[-1])
        }
    )

    # --- large text ----------------------------------------------------------
    walker.large_text.sort(key=lambda x: -x["bytes"])
    large_text_agg: Counter[str] = Counter()
    for item in walker.large_text:
        large_text_agg[item["path"]] += item["bytes"]

    profile_doc = {
        "generatedUtc": datetime.now(timezone.utc).isoformat(),
        "sourceFile": path.name,
        "sourceSha256": hashlib.sha256(raw).hexdigest(),
        "note": (
            "STRUCTURE ONLY. No customer values appear in this file. "
            "Strings are reduced to byte lengths; GUIDs to occurrence counts."
        ),
        "envelope": {
            "extractDetailKeys": sorted(extract_details.keys()),
            "objectDetailKeys": sorted(object_details.keys()),
            "extractObjectCount": len(objects),
            "objectDataTopLevelPropertyCount": len(object_data),
        },
        "size": {
            "rawFileBytes": total_pretty,
            "compactUtf8Bytes": total_compact,
            "compactKiB": round(total_compact / 1024, 1),
            "rawKiB": round(total_pretty / 1024, 1),
            "whitespaceOverheadPct": round(
                (total_pretty - total_compact) / total_pretty * 100, 1
            ),
            "objectDataCompactBytes": compact_bytes(object_data),
        },
        "shape": {
            "maxNestingDepth": walker.max_depth,
            "totalObjects": walker.total_objects,
            "totalArrays": walker.total_arrays,
            "totalScalars": walker.total_scalars,
            "totalKeys": walker.total_keys,
        },
        "sparsity": {
            "emptyStrings": walker.empty_strings,
            "emptyObjects": walker.empty_objects,
            "emptyArrays": walker.empty_arrays,
            "nullValues": walker.null_values,
            "emptyStringPctOfScalars": round(
                walker.empty_strings / max(walker.total_scalars, 1) * 100, 1
            ),
        },
        "topLevelSections": sections,
        "arrayCardinalities": arrays[:40],
        "guidReferences": guid_summary,
        "stableIdentifierPaths": id_paths[:60],
        "largeTextFields": {
            "thresholdBytes": LARGE_TEXT_BYTES,
            "count": len(walker.large_text),
            "totalBytes": sum(i["bytes"] for i in walker.large_text),
            "maxBytes": max((i["bytes"] for i in walker.large_text), default=0),
            "topPathsByTotalBytes": [
                {"path": p, "totalBytes": b} for p, b in large_text_agg.most_common(20)
            ],
        },
        "rtfFields": {
            "count": len(walker.rtf_fields),
            "totalBytes": sum(i["bytes"] for i in walker.rtf_fields),
            "maxBytes": max((i["bytes"] for i in walker.rtf_fields), default=0),
            "paths": sorted({i["path"] for i in walker.rtf_fields})[:20],
        },
        "datePaths": [{"path": p, "count": c} for p, c in walker.date_paths.most_common(25)],
    }

    profile_doc["designCandidates"] = derive_design_candidates(profile_doc)
    return profile_doc


def derive_design_candidates(p: dict[str, Any]) -> dict[str, Any]:
    """Turn the measured profile into concrete modelling recommendations."""
    sections = p["topLevelSections"]
    total = p["size"]["objectDataCompactBytes"]

    relational: list[str] = []
    json_blocks: list[dict[str, Any]] = []
    for s in sections:
        if s["treatment"].startswith("relational"):
            relational.append(s["name"])
        else:
            json_blocks.append(
                {
                    "section": s["name"],
                    "compactBytes": s["compactBytes"],
                    "pctOfObjectData": round(s["compactBytes"] / total * 100, 2),
                    "splitCandidate": s["treatment"].endswith("split-candidate"),
                }
            )

    top10 = sections[:10]
    top10_bytes = sum(s["compactBytes"] for s in top10)

    return {
        "concentration": {
            "top10SectionBytes": top10_bytes,
            "top10SectionPct": round(top10_bytes / total * 100, 1),
            "sectionsUnder1KiB": sum(1 for s in sections if s["compactBytes"] < 1024),
            "sectionsOver10KiB": sum(1 for s in sections if s["compactBytes"] > 10240),
        },
        "relationalCandidateSections": relational,
        "jsonBlockCandidateSections": json_blocks[:25],
        "aggregateBoundaries": [
            {
                "blockType": s["name"].upper(),
                "compactBytes": s["compactBytes"],
                "needsSubSplit": s["compactBytes"] > 100_000,
                "naturalSubSplit": [c["name"] for c in s.get("children", [])[:6]],
            }
            for s in sections[:12]
        ],
    }


def write_markdown(p: dict[str, Any], out: Path) -> None:
    sz = p["size"]
    sh = p["shape"]
    sp = p["sparsity"]
    dc = p["designCandidates"]

    def row(cells: list[str]) -> str:
        return "| " + " | ".join(cells) + " |"

    lines: list[str] = []
    a = lines.append
    a("# Source Data Profile")
    a("")
    a(f"Generated `{p['generatedUtc']}` by [tools/profile_source.py](../tools/profile_source.py).")
    a("")
    a("> **Structure only.** This document and `artifacts/data-profile.json` contain")
    a("> no customer values — every string is reduced to a byte length, every GUID to an")
    a("> occurrence count. The source sample itself is git-ignored and never committed.")
    a("")
    a("## 1. Envelope")
    a("")
    a("The file is not a bare order. It is an *extract envelope*:")
    a("")
    a("```")
    a("ExtractDetails { " + ", ".join(p["envelope"]["extractDetailKeys"]) + " }")
    a("ExtractData")
    a("  └── ExtractObjects[" + str(p["envelope"]["extractObjectCount"]) + "]")
    a("        ├── ObjectDetails { " + ", ".join(p["envelope"]["objectDetailKeys"]) + " }")
    a("        └── ObjectData    { " + str(p["envelope"]["objectDataTopLevelPropertyCount"]) + " properties }")
    a("```")
    a("")
    a("**Design consequence:** the envelope carries the version/identity metadata that both")
    a("storage paths need for keys, and `ObjectData` is the actual payload to model.")
    a("")
    a("## 2. Measured size")
    a("")
    a(row(["Measure", "Value"]))
    a(row(["---", "---"]))
    a(row(["Raw file (as supplied, formatted)", f"{sz['rawFileBytes']:,} bytes ({sz['rawKiB']:,} KiB)"]))
    a(row(["Compact UTF-8 (no whitespace)", f"{sz['compactUtf8Bytes']:,} bytes ({sz['compactKiB']:,} KiB)"]))
    a(row(["Whitespace overhead", f"{sz['whitespaceOverheadPct']}%"]))
    a(row(["`ObjectData` compact", f"{sz['objectDataCompactBytes']:,} bytes"]))
    a("")
    a("**Compact bytes is the number that matters** — it is what a Cosmos item is measured")
    a("against, what travels over the API, and what `nvarchar(max)` stores.")
    a("")
    a("## 3. Shape and sparsity")
    a("")
    a(row(["Measure", "Value"]))
    a(row(["---", "---"]))
    a(row(["Max nesting depth", str(sh["maxNestingDepth"])]))
    a(row(["Objects", f"{sh['totalObjects']:,}"]))
    a(row(["Arrays", f"{sh['totalArrays']:,}"]))
    a(row(["Scalars", f"{sh['totalScalars']:,}"]))
    a(row(["Keys", f"{sh['totalKeys']:,}"]))
    a(row(["Empty strings", f"{sp['emptyStrings']:,} ({sp['emptyStringPctOfScalars']}% of scalars)"]))
    a(row(["Empty objects", f"{sp['emptyObjects']:,}"]))
    a(row(["Empty arrays", f"{sp['emptyArrays']:,}"]))
    a(row(["Nulls", f"{sp['nullValues']:,}"]))
    a("")
    a("## 4. Top-level sections by size")
    a("")
    a(row(["#", "Section", "Compact bytes", "% of ObjectData", "Kind", "Array len", "Treatment"]))
    a(row(["---", "---", "---:", "---:", "---", "---:", "---"]))
    total = sz["objectDataCompactBytes"]
    for i, s in enumerate(p["topLevelSections"][:30], 1):
        a(
            row(
                [
                    str(i),
                    f"`{s['name']}`",
                    f"{s['compactBytes']:,}",
                    f"{s['compactBytes'] / total * 100:.2f}%",
                    s["kind"],
                    str(s.get("arrayLength", "")),
                    s["treatment"],
                ]
            )
        )
    a("")
    a(
        f"The top 10 sections hold **{dc['concentration']['top10SectionPct']}%** of `ObjectData` "
        f"({dc['concentration']['top10SectionBytes']:,} bytes). "
        f"{dc['concentration']['sectionsUnder1KiB']} of "
        f"{p['envelope']['objectDataTopLevelPropertyCount']} sections are under 1 KiB."
    )
    a("")
    a("**Design consequence:** size is highly concentrated. A small number of sections drive")
    a("nearly all bytes, which is exactly what makes a *block* model (SQL JSON blocks / Cosmos")
    a("aggregate items) viable — split the few big ones, leave the long tail alone.")
    a("")
    a("## 5. Largest array cardinalities")
    a("")
    a(row(["Path", "Occurrences", "Min", "Max", "Total elements"]))
    a(row(["---", "---:", "---:", "---:", "---:"]))
    for arr in p["arrayCardinalities"][:20]:
        a(row([f"`{arr['path']}`", str(arr["occurrences"]), str(arr["min"]), str(arr["max"]), str(arr["total"])]))
    a("")
    a("**Design consequence:** these are the arrays that grow. Document growth in a real order")
    a("comes from more elements in these arrays, not from wider objects — which is how the")
    a("synthetic generator must scale payloads, and which arrays the Cosmos splitter must")
    a("be able to chunk.")
    a("")
    a("## 6. Large text / RTF fields")
    a("")
    a(
        f"- Strings ≥ {p['largeTextFields']['thresholdBytes']} bytes: "
        f"**{p['largeTextFields']['count']:,}**, totalling "
        f"{p['largeTextFields']['totalBytes']:,} bytes "
        f"({p['largeTextFields']['totalBytes'] / total * 100:.1f}% of ObjectData); "
        f"largest single value {p['largeTextFields']['maxBytes']:,} bytes."
    )
    a(
        f"- RTF-encoded fields (`{{\\rtf…`): **{p['rtfFields']['count']:,}**, totalling "
        f"{p['rtfFields']['totalBytes']:,} bytes; largest {p['rtfFields']['maxBytes']:,} bytes."
    )
    a("")
    if p["largeTextFields"]["topPathsByTotalBytes"]:
        a(row(["Path", "Total bytes"]))
        a(row(["---", "---:"]))
        for t in p["largeTextFields"]["topPathsByTotalBytes"][:12]:
            a(row([f"`{t['path']}`", f"{t['totalBytes']:,}"]))
        a("")
    a("**Design consequence:** large free-text/RTF is pure pass-through. It is never filtered,")
    a("joined or sorted on, so it belongs in a JSON block (SQL) or a payload item (Cosmos) and")
    a("should be excluded from Cosmos indexing.")
    a("")
    a("## 7. GUID reference structure")
    a("")
    g = p["guidReferences"]
    a(row(["Measure", "Value"]))
    a(row(["---", "---:"]))
    a(row(["Distinct GUID values", f"{g['distinctGuidValues']:,}"]))
    a(row(["Total GUID occurrences", f"{g['totalGuidOccurrences']:,}"]))
    a(row(["GUIDs appearing more than once", f"{g['guidValuesAppearingMoreThanOnce']:,}"]))
    a(row(["Max occurrences of one GUID", f"{g['maxOccurrencesOfASingleGuid']:,}"]))
    a("")
    a("Top GUID-bearing paths:")
    a("")
    a(row(["Path", "Count"]))
    a(row(["---", "---:"]))
    for gp in g["topGuidPaths"][:15]:
        a(row([f"`{gp['path']}`", str(gp["count"])]))
    a("")
    a("**Design consequence:** repeated GUIDs are intra-document foreign keys — the same party")
    a("or property referenced from several sections. They are the natural join keys for the")
    a("relational extraction and the natural `id` seeds for deterministic Cosmos item ids.")
    a("")
    a("## 8. Candidate aggregate boundaries")
    a("")
    a(row(["Block type", "Compact bytes", "Needs sub-split", "Natural sub-blocks"]))
    a(row(["---", "---:", "---", "---"]))
    for b in dc["aggregateBoundaries"]:
        a(
            row(
                [
                    f"`{b['blockType']}`",
                    f"{b['compactBytes']:,}",
                    "yes" if b["needsSubSplit"] else "no",
                    ", ".join(b["naturalSubSplit"]) or "—",
                ]
            )
        )
    a("")
    a("## 9. Modelling conclusions")
    a("")
    a("**Relational (searched / joined / filtered / sorted / secured):**")
    a("")
    a("- Order identity and lifecycle: order id, version, type, status, project, dates")
    a("- Customer / tenant key")
    a("- Property address components (state and county drive reporting)")
    a("- Parties with a role discriminator — one party table, not one per role")
    a("- Loan amount, type and number")
    a("")
    a("**JSON blocks (deep, sparse, variable, pass-through):**")
    a("")
    for jb in dc["jsonBlockCandidateSections"][:10]:
        flag = " — *split candidate*" if jb["splitCandidate"] else ""
        a(f"- `{jb['section']}` ({jb['compactBytes']:,} bytes, {jb['pctOfObjectData']}%){flag}")
    a("")
    a("**Why not fully normalise:** max nesting depth is "
      f"{sh['maxNestingDepth']} and there are {sh['totalObjects']:,} objects across "
      f"{p['envelope']['objectDataTopLevelPropertyCount']} top-level sections. A faithful "
      "relational decomposition would run to well over a hundred tables for data that is "
      "almost entirely read back as-is. See [SQL_DESIGN.md](SQL_DESIGN.md) §control.")
    a("")
    a("**Why not one blob:** the fields in the relational list above appear in every search, "
      "filter and report. Leaving them inside a multi-megabyte blob forces a full scan plus "
      "JSON parse per row for queries that should be an index seek.")
    a("")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="data/source/150159_Order.MASKED.json.json")
    ap.add_argument("--json-out", default="artifacts/data-profile.json")
    ap.add_argument("--md-out", default="docs/DATA_PROFILE.md")
    args = ap.parse_args()

    src = Path(args.input)
    if not src.exists():
        raise SystemExit(f"source sample not found: {src}")

    p = profile(src)

    jout = Path(args.json_out)
    jout.parent.mkdir(parents=True, exist_ok=True)
    jout.write_text(json.dumps(p, indent=2), encoding="utf-8")

    mout = Path(args.md_out)
    mout.parent.mkdir(parents=True, exist_ok=True)
    write_markdown(p, mout)

    print(f"raw={p['size']['rawFileBytes']:,}B compact={p['size']['compactUtf8Bytes']:,}B "
          f"depth={p['shape']['maxNestingDepth']} sections={p['envelope']['objectDataTopLevelPropertyCount']}")
    print(f"wrote {jout} and {mout}")


if __name__ == "__main__":
    main()
