"""Measure what each size profile actually produces, against documented limits.

This runs entirely locally - no Azure resource is touched - and answers the part
of the size question that is a property of the DATA rather than of any database:

    * serialized JSON UTF-8 bytes  (what Cosmos NoSQL's 2 MB limit is measured in)
    * encoded BSON bytes           (what Mongo's 16 MB limit is measured in)
    * max array length, max object property count, max nesting depth
      (what Azure SQL's native `json` type caps at 65,535 / 65,535 / 128)

Why BSON is measured separately. A 15 MB JSON file is NOT a 15 MB Mongo document.
BSON adds a type byte and length prefix per field but stores numbers in binary, so
the encoded size can land either side of the JSON size depending on the shape of
the data. The brief is explicit that acceptance must be judged on the ENCODED
size, and that is only knowable by encoding it.

    python tools/document_size_probe.py
    python tools/document_size_probe.py --profile p17m --out artifacts/x.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import orjson  # noqa: E402

from generator.profiles import PROFILES_BY_NAME  # noqa: E402
from generator.synthetic_order_generator import (  # noqa: E402
    build_order,
    calibrated_profiles,
)

# Documented ceilings, all sourced in docs/SOURCES.md. Kept here as data so the
# report can say which limit each profile crosses instead of hard-coding verdicts.
LIMITS = {
    "cosmosNoSqlItemBytes": 2 * 1024 * 1024,      # SOURCES A.1
    "mongo16MbDocumentBytes": 16 * 1024 * 1024,   # SOURCES M.1
    "mongoDefaultDocumentBytes": 2 * 1024 * 1024,  # SOURCES M.1 (before enablement)
    "sqlJsonMaxArrayElements": 65_535,            # SOURCES N.1
    "sqlJsonMaxObjectProperties": 65_535,         # SOURCES N.1
    "sqlJsonMaxNestingLevels": 128,               # SOURCES N.1
    "sqlJsonMaxBytes": 2 * 1024 * 1024 * 1024,    # SOURCES N.1
}


def shape_stats(node: Any, depth: int = 1) -> tuple[int, int, int]:
    """(max array length, max object property count, max nesting depth)."""
    max_arr = 0
    max_props = 0
    max_depth = depth
    if isinstance(node, dict):
        max_props = len(node)
        for v in node.values():
            a, p, d = shape_stats(v, depth + 1)
            max_arr = max(max_arr, a)
            max_props = max(max_props, p)
            max_depth = max(max_depth, d)
    elif isinstance(node, list):
        max_arr = len(node)
        for v in node:
            a, p, d = shape_stats(v, depth + 1)
            max_arr = max(max_arr, a)
            max_props = max(max_props, p)
            max_depth = max(max_depth, d)
    return max_arr, max_props, max_depth


def mongo_document(envelope: dict[str, Any], payload_bytes: int) -> dict[str, Any]:
    """The document Scenario B would actually store, so BSON size is the real one."""
    from app.repositories.full_document_common import derive_projection

    proj = derive_projection(envelope, payload_bytes)
    doc = dict(envelope)
    doc["_id"] = proj["orderId"]
    doc["customerId"] = proj["customerId"]
    doc["orderVersion"] = proj["orderVersion"]
    doc["_proj"] = proj
    doc["_updatedAt"] = 0.0
    return doc


def probe(profile, seed: int, index: int) -> dict[str, Any]:
    import bson

    env = build_order(seed=seed, order_index=index, profile=profile,
                      customer_id="CUST-PROBE")
    payload = orjson.dumps(env)
    json_bytes = len(payload)
    # Indented/pretty JSON is what a file on disk often looks like; the limits are
    # measured against the compact form, so both are reported to stop anyone
    # comparing a pretty file size against a documented ceiling.
    pretty_bytes = len(json.dumps(env, indent=2).encode("utf-8"))

    doc = mongo_document(env, json_bytes)
    try:
        bson_bytes = len(bson.BSON.encode(doc))
        bson_error = None
    except Exception as exc:  # a document too large for BSON encoding at all
        bson_bytes = None
        bson_error = f"{type(exc).__name__}: {exc}"[:200]

    max_arr, max_props, max_depth = shape_stats(env)

    row: dict[str, Any] = {
        "profile": profile.name,
        "stress": bool(getattr(profile, "stress", False)),
        "targetBytes": profile.target_compact_bytes,
        "scale": profile.scale,
        "jsonBytesCompact": json_bytes,
        "jsonBytesPretty": pretty_bytes,
        "bsonBytes": bson_bytes,
        "bsonError": bson_error,
        "bsonOverheadPct": (round((bson_bytes - json_bytes) / json_bytes * 100, 2)
                            if bson_bytes else None),
        "maxArrayElements": max_arr,
        "maxObjectProperties": max_props,
        "maxNestingDepth": max_depth,
    }

    # Predicted outcome per platform, from documented limits only. These are
    # PREDICTIONS to be checked against live behaviour, never a substitute for it.
    row["predicted"] = {
        "cosmosNoSqlSingleItem": json_bytes <= LIMITS["cosmosNoSqlItemBytes"],
        "mongoWith16Mb": (bson_bytes is not None
                          and bson_bytes <= LIMITS["mongo16MbDocumentBytes"]),
        "mongoWithout16Mb": (bson_bytes is not None
                             and bson_bytes <= LIMITS["mongoDefaultDocumentBytes"]),
        "sqlJsonShapeOk": (max_arr <= LIMITS["sqlJsonMaxArrayElements"]
                           and max_props <= LIMITS["sqlJsonMaxObjectProperties"]
                           and max_depth <= LIMITS["sqlJsonMaxNestingLevels"]),
    }
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--index", type=int, default=1)
    ap.add_argument("--profile", action="append",
                    help="probe only these profiles (repeatable)")
    ap.add_argument("--out", default="artifacts/document-size-probe.json")
    args = ap.parse_args()

    profiles = calibrated_profiles(seed=args.seed)
    if args.profile:
        wanted = set(args.profile)
        unknown = wanted - set(PROFILES_BY_NAME)
        if unknown:
            raise SystemExit(f"unknown profile(s): {sorted(unknown)}")
        profiles = [p for p in profiles if p.name in wanted]

    rows = [probe(p, args.seed, args.index) for p in profiles]

    hdr = (f"{'profile':8} {'JSON MB':>9} {'BSON MB':>9} {'BSON ovh':>9} "
           f"{'maxArr':>7} {'maxProp':>8} {'depth':>6}  verdicts")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        b = r["bsonBytes"]
        v = r["predicted"]
        marks = (
            ("nosql:" + ("ok" if v["cosmosNoSqlSingleItem"] else "NO"))
            + "  " + ("mongo16:" + ("ok" if v["mongoWith16Mb"] else "NO"))
            + "  " + ("sqlshape:" + ("ok" if v["sqlJsonShapeOk"] else "NO"))
        )
        print(f"{r['profile']:8} {r['jsonBytesCompact']/1048576:9.3f} "
              f"{(b/1048576 if b else float('nan')):9.3f} "
              f"{(str(r['bsonOverheadPct']) + '%' if b else 'n/a'):>9} "
              f"{r['maxArrayElements']:7,} {r['maxObjectProperties']:8,} "
              f"{r['maxNestingDepth']:6}  {marks}"
              + (f"   BSON_ENCODE_FAILED {r['bsonError']}" if r["bsonError"] else ""))

    report = {
        "generatedUtc": datetime.now(timezone.utc).isoformat(),
        "seed": args.seed,
        "orderIndex": args.index,
        "documentedLimits": LIMITS,
        "note": ("Sizes are of ONE generated logical order per profile. 'predicted' "
                 "columns are derived from documented limits only and must be "
                 "confirmed against live database behaviour before being reported "
                 "as results."),
        "profiles": rows,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
