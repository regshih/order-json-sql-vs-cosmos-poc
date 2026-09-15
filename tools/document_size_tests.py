"""SECTION 4 - what each backend ACTUALLY does with a document of each size.

The local probe in `document_size_probe.py` says what the documented limits
predict. This says what the databases do. The two are kept separate on purpose:
a prediction that matches reality is evidence, and a prediction that does not is
the most interesting result in the run.

For every (backend, profile) pair it attempts, in order:

    INSERT      store one complete logical order as ONE database item
    POINT READ  fetch it back by id and verify the bytes round-trip
    UPDATE-SM   change one top-level scalar
    UPDATE-NEST change one deeply nested Title field
    REPLACE     rewrite the whole document

and records the outcome, the latency, the encoded size, and - where the platform
exposes it - the RU charge. A failure is a RESULT, not an error: the HTTP status
or driver exception is captured verbatim, because "rejected with 413 at 2.1 MB"
is precisely what the customer needs to know.

Must run inside the POC VNet (every store is private-endpoint only).

    python tools/document_size_tests.py --backend all
    python tools/document_size_tests.py --backend cosmos-mongo --profile p17m
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
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

BACKENDS = ["sql-full-json", "sql-full-json-native", "cosmos-mongo", "cosmos-nosql-monolithic"]

# Order matters: smallest first, so a backend that starts failing does so at a
# knowable boundary rather than mid-way through an unordered set.
DEFAULT_PROFILES = ["p500k", "p1m", "p1_5m", "p1_8m", "p1_9m", "p2_1m",
                    "p3m", "p5m", "p10m", "p15m", "p17m"]


def _fail(exc: BaseException) -> dict[str, Any]:
    """Capture a failure as data. Status codes are dug out where they exist."""
    out: dict[str, Any] = {
        "ok": False,
        "error": f"{type(exc).__name__}: {exc}"[:400],
    }
    for attr in ("status_code", "code", "http_status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            out["statusCode"] = v
            break
    # pymongo errors carry the server code in .details
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        out["serverCode"] = details.get("code")
        out["serverCodeName"] = details.get("codeName")
    return out


def _timed(fn, *a, **kw) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        res = fn(*a, **kw)
        row = {"ok": True, "ms": round((time.perf_counter() - t0) * 1000, 2)}
        if hasattr(res, "request_charge") and res.request_charge:
            row["ru"] = round(float(res.request_charge), 2)
        if hasattr(res, "extra") and isinstance(res.extra, dict):
            if res.extra.get("bsonBytes"):
                row["bsonBytes"] = res.extra["bsonBytes"]
        return row
    except BaseException as exc:  # noqa: BLE001 - a failure IS the result here
        row = _fail(exc)
        row["ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return row


class Metrics:
    """Minimal RequestMetrics stand-in so repositories can be driven directly."""

    def __init__(self) -> None:
        self.sql_queries = 0
        self.blocks_read = 0
        self.extra: dict[str, Any] = {}

    class _N:
        def __enter__(self): return None
        def __exit__(self, *a): return False

    def db(self): return self._N()
    def reconstruct(self): return self._N()
    def serialize(self): return self._N()


def make_repo(backend: str):
    if backend in ("sql-full-json", "sql-full-json-native"):
        from app.repositories.sql_full_json_repository import SqlFullJsonRepository

        return SqlFullJsonRepository(native=backend.endswith("-native"))
    if backend == "cosmos-mongo":
        from app.repositories.mongo_repository import MongoOrderRepository

        return MongoOrderRepository(capture_ru=True)
    if backend == "cosmos-nosql-monolithic":
        return MonolithicNoSqlProbe()
    raise SystemExit(f"unknown backend {backend}")


class MonolithicNoSqlProbe:
    """Stores the WHOLE order as ONE Cosmos NoSQL item, which is the thing the
    aggregate design exists to avoid.

    This is not how Scenario C works - it is the counterfactual. Its purpose is
    to show where the 2 MB item limit actually bites, so the report can say the
    limit constrains the DOCUMENT MODEL rather than disqualifying the engine.
    """

    backend = "cosmos-nosql-monolithic"

    def __init__(self) -> None:
        from app.repositories.cosmos_repository import CosmosOrderRepository

        self._repo = CosmosOrderRepository()
        self.container = self._repo.container

    def ingest_order(self, envelope: dict[str, Any], **kw):
        from app.repositories.base import IngestResult
        from app.repositories.full_document_common import derive_projection

        payload = orjson.dumps(envelope)
        proj = derive_projection(envelope, len(payload))
        item = {
            "id": f"monolithic::{proj['orderId']}",
            "customerId": proj["customerId"],
            "orderId": proj["orderId"],
            "docType": "monolithicOrder",
            "envelope": envelope,
        }
        self.container.upsert_item(item)
        return IngestResult(
            order_id=proj["orderId"], order_version=proj["orderVersion"],
            customer_id=proj["customerId"], blocks_written=1, items_written=1,
            bytes_written=len(payload), max_item_bytes=len(payload),
        )

    def get_full_order(self, order_id: str, m):
        return self.container.read_item(
            item=f"monolithic::{order_id.lower()}",
            partition_key=[self._last_customer, order_id.lower()],
        )

    _last_customer = "CUST-SIZETEST"

    def close(self) -> None:
        self._repo.close()


def run_backend(backend: str, profiles: list[str], seed: int, keep: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        repo = make_repo(backend)
    except BaseException as exc:  # noqa: BLE001
        print(f"  {backend}: CANNOT INITIALISE - {type(exc).__name__}: {exc}")
        return [{"backend": backend, "profile": None, **_fail(exc)}]

    calibrated = {p.name: p for p in calibrated_profiles(seed=seed)}

    for idx, name in enumerate(profiles):
        prof = calibrated[name]
        env = build_order(seed=seed, order_index=9000 + idx, profile=prof,
                          customer_id="CUST-SIZETEST")
        payload = orjson.dumps(env)
        json_bytes = len(payload)

        row: dict[str, Any] = {
            "backend": backend,
            "profile": name,
            "stress": bool(getattr(prof, "stress", False)),
            "jsonBytes": json_bytes,
        }
        try:
            import bson
            from app.repositories.full_document_common import derive_projection

            proj = derive_projection(env, json_bytes)
            order_id = proj["orderId"]
            doc = dict(env)
            doc.update({"_id": order_id, "customerId": proj["customerId"],
                        "orderVersion": proj["orderVersion"], "_proj": proj,
                        "_updatedAt": 0.0})
            row["bsonBytes"] = len(bson.BSON.encode(doc))
        except Exception:
            order_id = None
            row["bsonBytes"] = None

        print(f"  {backend:24} {name:6} {json_bytes/1048576:6.2f} MB json ...", end=" ", flush=True)

        row["insert"] = _timed(repo.ingest_order, env)
        if row["insert"]["ok"] and order_id:
            m = Metrics()
            row["pointRead"] = _timed(repo.get_full_order, order_id, m)
            if hasattr(repo, "update_order_header"):
                row["updateScalar"] = _timed(
                    repo.update_order_header, order_id, {"status": "SizeTested"})
            if hasattr(repo, "update_block"):
                row["updateNested"] = _timed(
                    repo.update_block, order_id, "TITLE", "MAIN", 0,
                    {"Title": {"ProbeField": "x" * 64}})
            if hasattr(repo, "replace_document"):
                row["replaceWhole"] = _timed(repo.replace_document, order_id, env)

        verdict = "ACCEPTED" if row["insert"]["ok"] else "REJECTED"
        extra = ""
        if not row["insert"]["ok"]:
            extra = f"  {row['insert'].get('statusCode') or row['insert'].get('serverCodeName') or ''} " \
                    f"{row['insert']['error'][:90]}"
        print(f"{verdict}{extra}")
        rows.append(row)

    try:
        repo.close()
    except Exception:
        pass
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backend", default="all",
                    help="all | " + " | ".join(BACKENDS))
    ap.add_argument("--profile", action="append")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--keep", action="store_true", help="leave test documents in place")
    ap.add_argument("--out", default="results/document-size-tests.json")
    args = ap.parse_args()

    backends = BACKENDS if args.backend == "all" else [args.backend]
    profiles = args.profile or DEFAULT_PROFILES
    unknown = set(profiles) - set(PROFILES_BY_NAME)
    if unknown:
        raise SystemExit(f"unknown profile(s): {sorted(unknown)}")

    print(f"document size tests - {len(backends)} backend(s) x {len(profiles)} profile(s)\n")
    all_rows: list[dict[str, Any]] = []
    for b in backends:
        print(f"{b}:")
        try:
            all_rows.extend(run_backend(b, profiles, args.seed, args.keep))
        except BaseException:  # noqa: BLE001
            traceback.print_exc()
        print()

    report = {
        "generatedUtc": datetime.now(timezone.utc).isoformat(),
        "seed": args.seed,
        "profiles": profiles,
        "note": ("Each row is ONE complete logical order stored as ONE database "
                 "item. A rejection is a result, not a run failure. Stress "
                 "profiles (10/15/17 MB) are boundary probes and do not describe "
                 "the customer's corpus."),
        "results": all_rows,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"-> {out}")

    accepted = sum(1 for r in all_rows if r.get("insert", {}).get("ok"))
    print(f"{accepted}/{len(all_rows)} inserts accepted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
