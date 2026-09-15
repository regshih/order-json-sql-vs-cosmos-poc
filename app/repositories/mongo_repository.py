"""SCENARIO B - Azure Cosmos DB for MongoDB, ONE complete order per BSON document.

    STORAGE_BACKEND=cosmos-mongo

One LOGICAL ORDER == one DATABASE ITEM. Nothing is decomposed: no separate CDF,
Title, Notes, Checklist, Buyers or Sellers documents. `GET /orders/{id}` is a
single `find_one` by `_id`.

Two platform facts govern this file, both verified in SOURCES.md before any code:

  * **M.2** `EnableMongo16MBDocumentSupport` and customer-managed keys cannot
    coexist, and the capability cannot be removed. The 16 MB limit also applies
    only to collections created AFTER enablement, so provisioning order matters.
  * **M.6** The MongoDB (RU) API has **no Entra data-plane authentication**. The
    account key is the only supported credential.

M.6 is why this is the one backend that cannot use `DefaultAzureCredential`
directly. The connection string is fetched from ARM at startup *with*
`DefaultAzureCredential`, held in memory, and never logged or written to disk -
the documented mitigation, not a pretence that the constraint is absent.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

import orjson

from app.repositories.base import (
    IngestResult,
    OrderRepository,
    OrderSearchCriteria,
    block_response,
)
from app.repositories.full_document_common import (
    derive_projection,
    sections_for_block,
    synthetic_parts,
)
from app.telemetry.metrics import RequestMetrics

ARM = "https://management.azure.com"
ARM_API_VERSION = "2024-11-15"
CAPABILITY_16MB = "EnableMongo16MBDocumentSupport"

# Keys the repository adds alongside the payload. Stripped on read so the API
# response is the envelope exactly as ingested.
_META_KEYS = frozenset({"_id", "customerId", "orderVersion", "_proj", "_updatedAt"})


def _arm_get(path: str, method: str = "GET") -> dict[str, Any]:
    import requests
    from azure.identity import DefaultAzureCredential

    token = DefaultAzureCredential().get_token(f"{ARM}/.default").token
    url = f"{ARM}{path}?api-version={ARM_API_VERSION}"
    r = requests.request(
        method, url, timeout=60,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    r.raise_for_status()
    return r.json() if r.content else {}


class MongoOrderRepository(OrderRepository):
    backend = "cosmos-mongo"

    def __init__(
        self,
        account: str | None = None,
        database: str | None = None,
        collection: str | None = None,
        connection_string: str | None = None,
        capture_ru: bool | None = None,
    ) -> None:
        from pymongo import MongoClient

        self.account = account or os.environ["MONGO_ACCOUNT"]
        self.database_name = database or os.getenv("MONGO_DATABASE", "orderdb")
        self.collection_name = collection or os.getenv("MONGO_COLLECTION", "orders_full")
        if capture_ru is None:
            capture_ru = os.getenv("MONGO_CAPTURE_RU", "0").lower() in ("1", "true", "yes")
        self.capture_ru = capture_ru

        cs = connection_string or os.getenv("MONGO_CONNECTION_STRING") or self._fetch_connection_string()

        # SOURCES.md M.7: getLastRequestStatistics is connection-scoped state, the
        # same shape of bug that under-reported NoSQL RU by ~42x here. When RU
        # capture is on, pin the pool to one connection and serialise, so "the
        # last request" is unambiguous. Otherwise use a normal pool.
        pool = 1 if capture_ru else int(os.getenv("MONGO_POOL_SIZE", "32"))
        self._ru_lock = threading.Lock()

        opts: dict[str, Any] = dict(
            maxPoolSize=pool,
            minPoolSize=1,
            retryWrites=False,  # Cosmos RU rejects retryable writes unless the capability is on
            serverSelectionTimeoutMS=30_000,
            connectTimeoutMS=30_000,
            socketTimeoutMS=120_000,  # a 15 MB document read needs room
        )
        # Only pass compressors when actually configured: pymongo validates the
        # option by iterating it, so an explicit None raises TypeError rather
        # than meaning "default".
        if os.getenv("MONGO_COMPRESSORS"):
            opts["compressors"] = os.environ["MONGO_COMPRESSORS"]
        self.client = MongoClient(cs, **opts)
        self.db = self.client[self.database_name]
        self.col = self.db[self.collection_name]

    # -- connection / capability -------------------------------------------

    def _fetch_connection_string(self) -> str:
        """Fetch the primary Mongo connection string from ARM. Never persisted.

        Deliberately not cached to disk and never logged. `MONGO_ACCOUNT`,
        `AZURE_SUBSCRIPTION_ID` and `AZURE_RESOURCE_GROUP` are configuration, not
        secrets; the key this returns is a secret and stays in memory.
        """
        sub = os.environ["AZURE_SUBSCRIPTION_ID"]
        rg = os.environ["AZURE_RESOURCE_GROUP"]
        body = _arm_get(
            f"/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.DocumentDB"
            f"/databaseAccounts/{self.account}/listConnectionStrings",
            method="POST",
        )
        for entry in body.get("connectionStrings", []):
            if "Primary MongoDB Connection String" in entry.get("description", ""):
                return entry["connectionString"]
        raise RuntimeError(
            f"no primary MongoDB connection string returned for account {self.account}"
        )

    def account_capabilities(self) -> list[str]:
        """The account's enabled capabilities, from the control plane.

        Used to PROVE `EnableMongo16MBDocumentSupport` is on rather than assuming
        it, which matters because a collection created before enablement silently
        keeps the 2 MB limit (SOURCES.md M.1).
        """
        sub = os.environ["AZURE_SUBSCRIPTION_ID"]
        rg = os.environ["AZURE_RESOURCE_GROUP"]
        body = _arm_get(
            f"/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.DocumentDB"
            f"/databaseAccounts/{self.account}"
        )
        caps = body.get("properties", {}).get("capabilities", []) or []
        return [c.get("name") for c in caps if c.get("name")]

    def ping(self) -> dict[str, Any]:
        """Liveness that actually touches the DATA plane.

        `ping` alone is not enough and that is not a theoretical concern: on an
        account created with `disableLocalAuth: true` - which is what the CLI
        produced here without being asked - the handshake succeeds while every
        read and write fails `Unauthorized (13)`. An earlier version of this
        method swallowed that into a bare `except` and reported ok=True on a
        backend where nothing worked. A health check that green-lights a dead
        dependency is worse than no health check.
        """
        t0 = time.perf_counter()
        self.db.command("ping")
        handshake_ms = round((time.perf_counter() - t0) * 1000, 2)
        out: dict[str, Any] = {
            "backend": self.backend,
            "database": self.database_name,
            "collection": self.collection_name,
            "captureRu": self.capture_ru,
            "handshakeMs": handshake_ms,
        }
        try:
            t1 = time.perf_counter()
            out["documents"] = self.col.estimated_document_count()
            out["dataPlaneMs"] = round((time.perf_counter() - t1) * 1000, 2)
            out["ok"] = True
        except Exception as exc:
            out["ok"] = False
            out["dataPlaneError"] = f"{type(exc).__name__}: {exc}"[:220]
            if "Unauthorized" in str(exc):
                out["hint"] = ("data plane rejected the account key - check "
                               "disableLocalAuth on the account. The MongoDB RU "
                               "API has no Entra data-plane alternative.")
        out["latency_ms"] = handshake_ms
        return out

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass

    # -- RU telemetry ------------------------------------------------------

    def _last_ru(self) -> dict[str, Any]:
        """Charge for the immediately preceding operation ON THIS CONNECTION.

        Only meaningful when `capture_ru` pinned the pool to one connection; see
        SOURCES.md M.7. Returns {} rather than a misleading number on failure.
        """
        if not self.capture_ru:
            return {}
        try:
            r = self.db.command("getLastRequestStatistics")
            return {
                "commandName": r.get("CommandName"),
                "requestCharge": float(r.get("RequestCharge", 0.0)),
                "serverDurationMs": float(r.get("RequestDurationInMilliSeconds", 0.0)),
            }
        except Exception:
            return {}

    # -- reads -------------------------------------------------------------

    def get_summary(self, order_id: str, m: RequestMetrics) -> dict[str, Any] | None:
        """Projection-only read. The multi-megabyte payload is never fetched.

        The `_proj.summary` sub-document is computed once at ingest by the same
        canonical function the decomposed backends use, so this returns exactly
        what they return without touching the order body.
        """
        oid = order_id.lower()
        with m.db():  # type: ignore[attr-defined]
            doc = self.col.find_one({"_id": oid}, {"_proj.summary": 1})
            if self.capture_ru:
                m.extra.setdefault("ru", []).append(self._last_ru())  # type: ignore[attr-defined]
        m.sql_queries += 1
        if not doc:
            return None
        return (doc.get("_proj") or {}).get("summary")

    def get_full_order(self, order_id: str, m: RequestMetrics) -> dict[str, Any] | None:
        """One document out. The only transformation is stripping our own metadata.

        BSON cannot be handed to the HTTP layer as bytes the way SQL's nvarchar
        can - the driver decodes it into Python objects on the way in, so a
        re-serialise is unavoidable here. That asymmetry is a real property of the
        two platforms and is reported as such rather than engineered away.
        """
        oid = order_id.lower()
        with m.db():  # type: ignore[attr-defined]
            doc = self.col.find_one({"_id": oid})
            if self.capture_ru:
                m.extra.setdefault("ru", []).append(self._last_ru())  # type: ignore[attr-defined]
        m.sql_queries += 1
        if not doc:
            return None
        m.blocks_read = 1
        with m.reconstruct():  # type: ignore[attr-defined]
            return {k: v for k, v in doc.items() if k not in _META_KEYS}

    def get_block(
        self, order_id: str, block_type: str, m: RequestMetrics, block_sub_type: str | None = None
    ) -> dict[str, Any] | None:
        """Server-side projection of one section, so 450 KB crosses the wire, not 5 MB.

        The Mongo equivalent of Scenario A's `JSON_QUERY`: a projection on the
        ObjectData sub-path. Without this the block endpoints would measure
        network transfer of the whole order, which would be a property of the
        implementation rather than of the storage model.
        """
        from app.repositories.full_document_common import BLOCK_KEYS

        base = "ExtractData.ExtractObjects.0.ObjectData"
        keys: list[str] = []
        for (btype, sub), ks in BLOCK_KEYS.items():
            if btype != block_type:
                continue
            if block_sub_type and sub != block_sub_type:
                continue
            keys.extend(ks)

        oid = order_id.lower()
        projection: dict[str, Any] = {"_proj.orderVersion": 1, "_proj.customerId": 1,
                                      "_proj.payloadBytes": 1}
        if keys:
            for k in keys:
                projection[f"{base}.{k}"] = 1
        else:
            projection[base] = 1  # MISC: complement, needs the whole ObjectData

        with m.db():  # type: ignore[attr-defined]
            doc = self.col.find_one({"_id": oid}, projection)
            if self.capture_ru:
                m.extra.setdefault("ru", []).append(self._last_ru())  # type: ignore[attr-defined]
        m.sql_queries += 1
        if not doc:
            return None

        with m.reconstruct():  # type: ignore[attr-defined]
            p = doc.get("_proj") or {}
            sections = sections_for_block(doc, block_type, block_sub_type)
            if not sections:
                return None
            m.blocks_read = len(sections)
            return block_response(
                oid, p.get("orderVersion", 0), p.get("customerId", ""), block_type,
                sections, synthetic_parts(block_type, sections, p.get("payloadBytes", 0)),
            )

    def search_orders(self, c: OrderSearchCriteria, m: RequestMetrics) -> list[dict[str, Any]]:
        q: dict[str, Any] = {}
        if c.customer_id:
            q["customerId"] = c.customer_id
        if c.status:
            q["_proj.status"] = c.status
        if c.state:
            q["_proj.primaryState"] = c.state
        if c.min_loan_amount is not None:
            q["_proj.maxLoanAmount"] = {"$gte": float(c.min_loan_amount)}

        with m.db():  # type: ignore[attr-defined]
            cur = self.col.find(q, {"_proj": 1, "customerId": 1, "orderVersion": 1}).limit(int(c.limit))
            docs = list(cur)
            if self.capture_ru:
                m.extra.setdefault("ru", []).append(self._last_ru())  # type: ignore[attr-defined]
        m.sql_queries += 1
        out = []
        for d in docs:
            p = d.get("_proj") or {}
            out.append({
                "orderId": d["_id"],
                "customerId": d.get("customerId"),
                "orderVersion": d.get("orderVersion"),
                "orderNumber": p.get("orderNumber"),
                "status": p.get("status"),
                "state": p.get("primaryState"),
                "maxLoanAmount": p.get("maxLoanAmount"),
                "totalLoanAmount": p.get("totalLoanAmount"),
                "payloadBytes": p.get("payloadBytes"),
            })
        return out

    def list_order_ids(self, limit: int = 1000) -> list[dict[str, Any]]:
        """Pool for the load generator. Shape must match every other backend.

        `customerId` and `orderVersion` are required by the harness, which groups
        the pool by customer to build search queries. Omitting them fails every
        run with `KeyError: 'customerId'` before any request is issued.
        """
        cur = self.col.find(
            {}, {"_proj.payloadBytes": 1, "customerId": 1, "orderVersion": 1}
        ).limit(int(limit))
        return [
            {
                "orderId": d["_id"],
                "customerId": d.get("customerId"),
                "orderVersion": d.get("orderVersion"),
                "payloadBytes": (d.get("_proj") or {}).get("payloadBytes", 0),
            }
            for d in cur
        ]

    # -- writes ------------------------------------------------------------

    def _document(self, envelope: dict[str, Any]) -> tuple[dict[str, Any], int, dict[str, Any]]:
        payload_bytes = len(orjson.dumps(envelope))
        proj = derive_projection(envelope, payload_bytes)
        doc = dict(envelope)
        doc["_id"] = proj["orderId"]
        doc["customerId"] = proj["customerId"]
        doc["orderVersion"] = proj["orderVersion"]
        doc["_proj"] = proj
        doc["_updatedAt"] = time.time()
        return doc, payload_bytes, proj

    def bson_size(self, envelope: dict[str, Any]) -> int:
        """Encoded BSON size of the document this envelope would become.

        The brief asks for BSON size as well as JSON size, and they differ: BSON
        adds per-field type bytes and length prefixes but stores numbers binary.
        Acceptance must be judged on the ENCODED size, not on the source file
        size - a 15 MB JSON file is not a 15 MB document.
        """
        import bson

        doc, _, _ = self._document(envelope)
        return len(bson.BSON.encode(doc))

    def ingest_order(
        self, envelope: dict[str, Any], archive_uri: str | None = None, payload_hash: str | None = None
    ) -> IngestResult:
        t0 = time.perf_counter()
        doc, payload_bytes, proj = self._document(envelope)
        import bson

        encoded = len(bson.BSON.encode(doc))
        with self._ru_lock if self.capture_ru else _NullCtx():
            self.col.replace_one({"_id": doc["_id"]}, doc, upsert=True)
            ru = self._last_ru()
        return IngestResult(
            order_id=proj["orderId"],
            order_version=proj["orderVersion"],
            customer_id=proj["customerId"],
            blocks_written=1,
            items_written=1,
            bytes_written=payload_bytes,
            archive_uri=archive_uri,
            request_charge=float(ru.get("requestCharge", 0.0)),
            duration_ms=(time.perf_counter() - t0) * 1000,
            max_item_bytes=encoded,
            extra={"bsonBytes": encoded, "jsonBytes": payload_bytes, "ru": ru,
                   "mode": "replace_one(upsert)"},
        )

    def update_block(
        self, order_id: str, block_type: str, block_sub_type: str, sequence: int, payload: dict[str, Any]
    ) -> IngestResult:
        """Targeted `$set` on a nested path - no whole-document rewrite from the client.

        Whether the SERVER rewrites the whole document is the question §9 exists
        to answer; that shows up in the RU charge, which is why this returns it.
        """
        from app.repositories.full_document_common import BLOCK_KEYS

        keys = BLOCK_KEYS.get((block_type, block_sub_type))
        if not keys:
            raise ValueError(f"unknown block {block_type}/{block_sub_type}")
        key = keys[0]
        path = f"ExtractData.ExtractObjects.0.ObjectData.{key}"
        value = payload.get(key, payload)

        t0 = time.perf_counter()
        with self._ru_lock if self.capture_ru else _NullCtx():
            res = self.col.update_one({"_id": order_id.lower()},
                                      {"$set": {path: value, "_updatedAt": time.time()}})
            ru = self._last_ru()
        return IngestResult(
            order_id=order_id.lower(), order_version=0, customer_id="",
            blocks_written=res.modified_count, items_written=res.modified_count,
            bytes_written=len(orjson.dumps(value)),
            request_charge=float(ru.get("requestCharge", 0.0)),
            duration_ms=(time.perf_counter() - t0) * 1000,
            extra={"mode": "$set nested", "path": path, "ru": ru},
        )

    def update_order_header(self, order_id: str, changes: dict[str, Any]) -> IngestResult:
        status = changes.get("status") or changes.get("Status")
        if status is None:
            raise ValueError("update_order_header expects a 'status' change")
        base = "ExtractData.ExtractObjects.0.ObjectData"
        t0 = time.perf_counter()
        with self._ru_lock if self.capture_ru else _NullCtx():
            res = self.col.update_one(
                {"_id": order_id.lower()},
                {"$set": {f"{base}.Status": status, "_proj.status": status,
                          "_proj.summary.status": status, "_updatedAt": time.time()}},
            )
            ru = self._last_ru()
        return IngestResult(
            order_id=order_id.lower(), order_version=0, customer_id="",
            blocks_written=res.modified_count, items_written=res.modified_count,
            bytes_written=len(str(status)),
            request_charge=float(ru.get("requestCharge", 0.0)),
            duration_ms=(time.perf_counter() - t0) * 1000,
            extra={"mode": "$set scalar", "ru": ru},
        )

    def replace_document(self, order_id: str, envelope: dict[str, Any]) -> IngestResult:
        """§9A: full replacement, for comparison against the targeted updates."""
        return self.ingest_order(envelope)


class _NullCtx:
    def __enter__(self): return None
    def __exit__(self, *a): return False
