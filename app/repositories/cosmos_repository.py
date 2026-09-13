"""PATH B - Azure Cosmos DB for NoSQL aggregate-document repository.

Document model (see docs/COSMOS_DESIGN.md):

    { id, docType, customerId, orderId, orderVersion,
      blockType, blockSubType, sequence, chunkIndex, chunkCount,
      search: {...},          # routing/search fields, indexed
      data:   {...} }         # the business payload, NOT indexed

Partition key is HIERARCHICAL: /customerId then /orderId. Every item of one
order therefore lives in one logical partition, so:

  * summary      -> point read (id + full PK)      -> lowest possible RU
  * block read   -> single-partition query
  * full order   -> single-partition query, no cross-partition fan-out

RU charges are captured from the SDK response headers on every call and never
estimated.
"""

from __future__ import annotations

import hashlib
import os
import time
from typing import Any, Iterable

from azure.cosmos import CosmosClient, PartitionKey, exceptions

from app.repositories.base import (
    IngestResult,
    OrderRepository,
    OrderSearchCriteria,
    block_response,
)
from app.telemetry.metrics import RequestMetrics
from cosmos.modeling.size_guard import (
    COSMOS_ITEM_TARGET_BYTES,
    GuardStats,
    check_item,
    enforce,
)
from ingestion.parser.block_splitter import (
    Block,
    build_envelope,
    merge_chunks,
    parse_envelope,
    reassemble,
    split_object_data,
)
from ingestion.parser.relational_extract import extract

DOC_TYPE_HEADER = "orderHeader"
DOC_TYPE_BLOCK = "orderBlock"


def header_id(order_id: str, version: int) -> str:
    return f"{order_id}:v{version}:HEADER"


def block_id(order_id: str, version: int, block: Block) -> str:
    """Deterministic id so that re-ingesting the same version is idempotent."""
    return f"{order_id}:v{version}:{block.key}"


class CosmosOrderRepository(OrderRepository):
    backend = "cosmos"

    def __init__(
        self,
        endpoint: str | None = None,
        database: str | None = None,
        container: str | None = None,
        credential: Any = None,
        item_budget: int | None = None,
    ) -> None:
        endpoint = endpoint or os.environ["COSMOS_ENDPOINT"]
        self.database_name = database or os.getenv("COSMOS_DATABASE", "orderdb")
        self.container_name = container or os.getenv("COSMOS_CONTAINER", "orders")
        self.item_budget = item_budget or COSMOS_ITEM_TARGET_BYTES

        if credential is None:
            key = os.getenv("COSMOS_KEY")
            if key:
                credential = key
            else:
                from azure.identity import DefaultAzureCredential

                credential = DefaultAzureCredential()

        self.client = CosmosClient(endpoint, credential=credential)
        self.db = self.client.get_database_client(self.database_name)
        self.container = self.db.get_container_client(self.container_name)

    # -- plumbing ----------------------------------------------------------

    @staticmethod
    def _charge(m: RequestMetrics | None, headers: Any) -> None:
        if m is None:
            return
        try:
            ru = float(headers.get("x-ms-request-charge", 0.0))
        except (AttributeError, TypeError, ValueError):
            ru = 0.0
        m.request_charge += ru
        m.cosmos_requests += 1

    def _track(self, m: RequestMetrics | None, exc: Exception) -> None:
        if m is not None and isinstance(exc, exceptions.CosmosHttpResponseError):
            if exc.status_code == 429:
                m.throttled_429 += 1
            m.retries += 1

    def ping(self) -> dict[str, Any]:
        t0 = time.perf_counter()
        props = self.container.read()
        return {
            "backend": "cosmos",
            "ok": True,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
            "partitionKey": props.get("partitionKey", {}).get("paths"),
            "partitionKeyKind": props.get("partitionKey", {}).get("kind"),
            "itemBudgetBytes": self.item_budget,
        }

    # -- reads -------------------------------------------------------------

    def _find_header_by_order(self, order_id: str, m: RequestMetrics) -> dict[str, Any] | None:
        """Locate an order without knowing its customerId.

        This is a cross-partition query and is deliberately the ONLY place we
        allow one: the API contract is /orders/{orderId} with no tenant in the
        path. A production API would carry customerId in the route or the token
        and skip this entirely - measured separately as `lookup_ru`.
        """
        q = (
            "SELECT TOP 1 c.customerId, c.orderVersion, c.id FROM c "
            "WHERE c.orderId = @oid AND c.docType = @dt"
        )
        with m.db():  # type: ignore[attr-defined]
            it = self.container.query_items(
                query=q,
                parameters=[{"name": "@oid", "value": order_id}, {"name": "@dt", "value": DOC_TYPE_HEADER}],
                enable_cross_partition_query=True,
            )
            rows = list(it)
            self._charge(m, self.container.client_connection.last_response_headers)
        return rows[0] if rows else None

    def _read_header(self, order_id: str, m: RequestMetrics, customer_id: str | None = None) -> dict[str, Any] | None:
        if customer_id is None:
            stub = self._find_header_by_order(order_id, m)
            if stub is None:
                return None
            customer_id = stub["customerId"]
            version = stub["orderVersion"]
        else:
            version = None

        if version is None:
            found = self._find_header_by_order(order_id, m)
            if found is None:
                return None
            version = found["orderVersion"]

        # Point read: id + the full hierarchical partition key.
        try:
            with m.db():  # type: ignore[attr-defined]
                item = self.container.read_item(
                    item=header_id(order_id, version),
                    partition_key=[customer_id, order_id],
                )
                self._charge(m, self.container.client_connection.last_response_headers)
            m.items_read += 1
            return item
        except exceptions.CosmosResourceNotFoundError:
            return None
        except Exception as exc:
            self._track(m, exc)
            raise

    def get_summary(self, order_id: str, m: RequestMetrics) -> dict[str, Any] | None:
        header = self._read_header(order_id, m)
        if header is None:
            return None
        with m.reconstruct():  # type: ignore[attr-defined]
            return header["summary"]

    def get_block(
        self, order_id: str, block_type: str, m: RequestMetrics, block_sub_type: str | None = None
    ) -> dict[str, Any] | None:
        stub = self._find_header_by_order(order_id, m)
        if stub is None:
            return None
        customer_id, version = stub["customerId"], stub["orderVersion"]

        q = (
            "SELECT c.blockType, c.blockSubType, c.sequence, c.chunkIndex, c.chunkCount, "
            "c.payloadBytes, c.data FROM c "
            "WHERE c.customerId = @cid AND c.orderId = @oid AND c.orderVersion = @v "
            "  AND c.docType = @dt AND c.blockType = @bt"
        )
        params = [
            {"name": "@cid", "value": customer_id},
            {"name": "@oid", "value": order_id},
            {"name": "@v", "value": version},
            {"name": "@dt", "value": DOC_TYPE_BLOCK},
            {"name": "@bt", "value": block_type},
        ]
        if block_sub_type:
            q += " AND c.blockSubType = @bst"
            params.append({"name": "@bst", "value": block_sub_type})

        with m.db():  # type: ignore[attr-defined]
            # Single-partition query: the full HPK is in the predicate.
            items = list(
                self.container.query_items(
                    query=q, parameters=params, partition_key=[customer_id, order_id]
                )
            )
            self._charge(m, self.container.client_connection.last_response_headers)
        if not items:
            return None
        m.items_read += len(items)

        with m.reconstruct():  # type: ignore[attr-defined]
            blocks = _to_blocks(items)
            m.blocks_read = len(blocks)
            sections = reassemble(blocks)
            parts = [
                {
                    "blockSubType": i["blockSubType"],
                    "sequence": i["sequence"],
                    "payloadBytes": i.get("payloadBytes"),
                }
                for i in items
            ]
            return block_response(order_id, version, customer_id, block_type, sections, parts)

    def get_full_order(self, order_id: str, m: RequestMetrics) -> dict[str, Any] | None:
        stub = self._find_header_by_order(order_id, m)
        if stub is None:
            return None
        customer_id, version = stub["customerId"], stub["orderVersion"]

        with m.db():  # type: ignore[attr-defined]
            items = list(
                self.container.query_items(
                    query=(
                        "SELECT c.docType, c.blockType, c.blockSubType, c.sequence, "
                        "c.chunkIndex, c.chunkCount, c.chunkOfPath, c.data, c.extract FROM c "
                        "WHERE c.customerId = @cid AND c.orderId = @oid AND c.orderVersion = @v"
                    ),
                    parameters=[
                        {"name": "@cid", "value": customer_id},
                        {"name": "@oid", "value": order_id},
                        {"name": "@v", "value": version},
                    ],
                    partition_key=[customer_id, order_id],
                    max_item_count=200,
                )
            )
            self._charge(m, self.container.client_connection.last_response_headers)
        if not items:
            return None
        m.items_read += len(items)

        with m.reconstruct():  # type: ignore[attr-defined]
            header = next((i for i in items if i.get("docType") == DOC_TYPE_HEADER), None)
            blocks = _to_blocks([i for i in items if i.get("docType") == DOC_TYPE_BLOCK])
            m.blocks_read = len(blocks)
            object_data = reassemble(blocks)
            extract_meta = (header or {}).get("extract") or {}
            return build_envelope(
                customer_id,
                order_id,
                version,
                object_data,
                extract_timestamp=extract_meta.get("timestamp"),
                extract_server=extract_meta.get("server"),
                extract_type=extract_meta.get("type", "FULL"),
            )

    def search_orders(self, c: OrderSearchCriteria, m: RequestMetrics) -> list[dict[str, Any]]:
        where = ["c.docType = @dt"]
        params: list[dict[str, Any]] = [{"name": "@dt", "value": DOC_TYPE_HEADER}]
        if c.customer_id:
            where.append("c.customerId = @cid")
            params.append({"name": "@cid", "value": c.customer_id})
        if c.status:
            where.append("c.search.status = @st")
            params.append({"name": "@st", "value": c.status})
        if c.state:
            where.append("c.search.state = @state")
            params.append({"name": "@state", "value": c.state})
        if c.min_loan_amount is not None:
            where.append("c.search.maxLoanAmount >= @mla")
            params.append({"name": "@mla", "value": c.min_loan_amount})

        q = (
            f"SELECT TOP {int(c.limit)} c.orderId, c.customerId, c.orderVersion, "
            "c.search, c.payloadBytes FROM c WHERE " + " AND ".join(where)
        )

        kwargs: dict[str, Any] = {}
        if c.customer_id:
            # Scoping to one customer keeps this inside a single first-level
            # partition prefix instead of fanning out across every tenant.
            kwargs["partition_key"] = [c.customer_id]
        else:
            kwargs["enable_cross_partition_query"] = True

        with m.db():  # type: ignore[attr-defined]
            rows = list(self.container.query_items(query=q, parameters=params, **kwargs))
            self._charge(m, self.container.client_connection.last_response_headers)

        return [
            {
                "orderId": r["orderId"],
                "customerId": r["customerId"],
                "orderVersion": r["orderVersion"],
                "orderNumber": r["search"].get("orderNumber"),
                "status": r["search"].get("status"),
                "orderType": r["search"].get("orderType"),
                "project": r["search"].get("project"),
                "settlementDate": r["search"].get("settlementDate"),
                "state": r["search"].get("state"),
                "county": r["search"].get("county"),
                "maxLoanAmount": r["search"].get("maxLoanAmount"),
                "payloadBytes": r.get("payloadBytes"),
            }
            for r in rows
        ]

    def list_order_ids(self, limit: int = 1000) -> list[dict[str, Any]]:
        rows = self.container.query_items(
            query=(
                f"SELECT TOP {int(limit)} c.orderId, c.customerId, c.orderVersion, c.payloadBytes "
                "FROM c WHERE c.docType = @dt"
            ),
            parameters=[{"name": "@dt", "value": DOC_TYPE_HEADER}],
            enable_cross_partition_query=True,
        )
        return [
            {
                "orderId": r["orderId"],
                "customerId": r["customerId"],
                "orderVersion": r["orderVersion"],
                "payloadBytes": r.get("payloadBytes"),
            }
            for r in rows
        ]

    # -- writes ------------------------------------------------------------

    def build_items(self, envelope_doc: dict[str, Any]) -> tuple[list[dict[str, Any]], GuardStats]:
        """Split -> size-guard -> shape into Cosmos items. Pure; no I/O.

        Exposed separately so the negative test and unit tests can exercise the
        modelling without a live account.
        """
        env = parse_envelope(envelope_doc)
        proj = extract(env)
        blocks = split_object_data(env["objectData"])

        stats = GuardStats()
        guarded = enforce(blocks, budget=self.item_budget, stats=stats)

        order_id, version, customer_id = env["orderId"], env["orderVersion"], env["customerId"]
        o, sf = proj["order"], proj["searchFields"]
        total_bytes = sum(b.payload_bytes for b in blocks)

        from ingestion.parser.relational_extract import summary_from_projection

        search = {
            "orderNumber": o["OrderNumber"],
            "status": o["Status"],
            "orderType": o["OrderType"],
            "project": o["Project"],
            "settlementType": o["SettlementType"],
            "state": sf["state"],
            "county": sf["county"],
            "city": sf["city"],
            "maxLoanAmount": sf["maxLoanAmount"],
            "totalLoanAmount": sf["totalLoanAmount"],
            "isCommercial": o["IsCommercial"],
            "isRush": o["IsRush"],
            "settlementDate": o["SettlementDate"].isoformat() if o["SettlementDate"] else None,
            "createdDate": o["CreatedDate"].isoformat() if o["CreatedDate"] else None,
        }

        items: list[dict[str, Any]] = [
            {
                "id": header_id(order_id, version),
                "docType": DOC_TYPE_HEADER,
                "customerId": customer_id,
                "orderId": order_id,
                "orderVersion": version,
                "isCurrent": True,
                "payloadBytes": total_bytes,
                "blockCount": len(guarded),
                "search": search,
                "summary": summary_from_projection(proj, payload_bytes=total_bytes),
                "extract": {
                    "timestamp": env.get("extractTimestamp"),
                    "server": env.get("extractServer"),
                    "type": env.get("extractType", "FULL"),
                },
                "modifiedUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        ]

        for b in guarded:
            item = {
                "id": block_id(order_id, version, b),
                "docType": DOC_TYPE_BLOCK,
                "customerId": customer_id,
                "orderId": order_id,
                "orderVersion": version,
                "blockType": b.block_type,
                "blockSubType": b.block_sub_type,
                "sequence": b.sequence,
                "chunkIndex": b.chunk_index,
                "chunkCount": b.chunk_count,
                "chunkOfPath": b.chunk_of_path,
                "payloadBytes": b.payload_bytes,
                "payloadHash": b.payload_hash,
                "data": b.payload,
            }
            check_item(item, self.item_budget)
            items.append(item)

        return items, stats

    def ingest_order(
        self, envelope_doc: dict[str, Any], archive_uri: str | None = None, payload_hash: str | None = None
    ) -> IngestResult:
        t0 = time.perf_counter()
        items, stats = self.build_items(envelope_doc)
        if archive_uri:
            items[0]["archiveUri"] = archive_uri
            items[0]["sourcePayloadHash"] = payload_hash

        ru = 0.0
        retries = throttles = 0
        for item in items:
            for attempt in range(6):
                try:
                    self.container.upsert_item(item)
                    ru += float(
                        self.container.client_connection.last_response_headers.get(
                            "x-ms-request-charge", 0.0
                        )
                    )
                    break
                except exceptions.CosmosHttpResponseError as exc:
                    if exc.status_code == 429:
                        throttles += 1
                        retries += 1
                        wait_ms = int(exc.headers.get("x-ms-retry-after-ms", 100)) if exc.headers else 100
                        time.sleep(min(wait_ms, 2000) / 1000)
                        continue
                    raise
            else:
                raise RuntimeError(f"gave up upserting {item['id']} after repeated 429s")

        return IngestResult(
            order_id=items[0]["orderId"],
            order_version=items[0]["orderVersion"],
            customer_id=items[0]["customerId"],
            blocks_written=stats.blocks_in,
            items_written=len(items),
            bytes_written=items[0]["payloadBytes"],
            archive_uri=archive_uri,
            request_charge=round(ru, 3),
            duration_ms=(time.perf_counter() - t0) * 1000,
            chunked_blocks=stats.blocks_chunked,
            max_item_bytes=stats.max_item_bytes,
            extra={"retries": retries, "throttled429": throttles},
        )

    def update_block(
        self, order_id: str, block_type: str, block_sub_type: str, sequence: int, payload: dict[str, Any]
    ) -> IngestResult:
        t0 = time.perf_counter()
        m = RequestMetrics(backend="cosmos", endpoint="internal", operation="update_block")
        m.db = _Noop()          # type: ignore[attr-defined]
        m.reconstruct = _Noop()  # type: ignore[attr-defined]
        stub = self._find_header_by_order(order_id, m)
        if stub is None:
            raise KeyError(f"order {order_id} not found")
        customer_id, version = stub["customerId"], stub["orderVersion"]

        b = Block(block_type, block_sub_type, sequence, payload)
        item = {
            "id": block_id(order_id, version, b),
            "docType": DOC_TYPE_BLOCK,
            "customerId": customer_id,
            "orderId": order_id,
            "orderVersion": version,
            "blockType": block_type,
            "blockSubType": block_sub_type,
            "sequence": sequence,
            "chunkIndex": 0,
            "chunkCount": 1,
            "chunkOfPath": None,
            "payloadBytes": b.payload_bytes,
            "payloadHash": b.payload_hash,
            "data": payload,
        }
        check_item(item, self.item_budget)
        self.container.upsert_item(item)
        ru = float(self.container.client_connection.last_response_headers.get("x-ms-request-charge", 0.0))

        return IngestResult(
            order_id=order_id, order_version=version, customer_id=customer_id,
            blocks_written=1, items_written=1, bytes_written=b.payload_bytes,
            request_charge=round(ru + m.request_charge, 3),
            duration_ms=(time.perf_counter() - t0) * 1000,
            extra={"lookupRu": round(m.request_charge, 3)},
        )

    def update_order_header(self, order_id: str, changes: dict[str, Any]) -> IngestResult:
        t0 = time.perf_counter()
        m = RequestMetrics(backend="cosmos", endpoint="internal", operation="update_header")
        m.db = _Noop()          # type: ignore[attr-defined]
        m.reconstruct = _Noop()  # type: ignore[attr-defined]
        header = self._read_header(order_id, m)
        if header is None:
            raise KeyError(f"order {order_id} not found")

        field_map = {
            "Status": ("status", "status"),
            "Project": ("project", "project"),
            "Balance": (None, "balance"),
            "IsRush": ("isRush", "isRush"),
            "StatusComment": (None, None),
        }
        for k, v in changes.items():
            sk, mk = field_map.get(k, (None, None))
            if sk:
                header["search"][sk] = v
            if mk:
                header["summary"][mk] = v
        header["modifiedUtc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        self.container.upsert_item(header)
        ru = float(self.container.client_connection.last_response_headers.get("x-ms-request-charge", 0.0))
        return IngestResult(
            order_id=order_id, order_version=header["orderVersion"], customer_id=header["customerId"],
            blocks_written=0, items_written=1, bytes_written=0,
            request_charge=round(ru + m.request_charge, 3),
            duration_ms=(time.perf_counter() - t0) * 1000,
            extra={"lookupRu": round(m.request_charge, 3)},
        )

    # -- diagnostics -------------------------------------------------------

    def container_stats(self) -> dict[str, Any]:
        props = self.container.read()
        try:
            offer = self.container.get_throughput()
            tp = {
                "offerThroughput": offer.offer_throughput,
                "autoscaleMaxThroughput": (offer.properties.get("content", {})
                                           .get("offerAutopilotSettings", {}).get("maxThroughput")),
            }
        except Exception as exc:  # throughput may be database-level
            tp = {"error": str(exc)[:200]}
        return {
            "partitionKey": props.get("partitionKey"),
            "indexingPolicy": props.get("indexingPolicy"),
            "throughput": tp,
        }


def _to_blocks(items: Iterable[dict[str, Any]]) -> list[Block]:
    blocks = [
        Block(
            block_type=i["blockType"],
            block_sub_type=i["blockSubType"],
            sequence=i.get("sequence", 0),
            payload=i["data"],
            chunk_index=i.get("chunkIndex", 0) or 0,
            chunk_count=i.get("chunkCount", 1) or 1,
            chunk_of_path=i.get("chunkOfPath"),
        )
        for i in items
    ]
    return merge_chunks(blocks)


class _Noop:
    """Stand-in timer for internal calls that are not part of an API request."""

    def __call__(self):
        from contextlib import nullcontext

        return nullcontext()

    elapsed_ms = 0.0
