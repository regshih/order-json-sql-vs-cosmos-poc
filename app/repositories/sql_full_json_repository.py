"""SCENARIO A - Azure SQL Database, ONE complete order JSON per row.

    STORAGE_BACKEND=sql-full-json          JsonPayload nvarchar(max) + ISJSON check
    STORAGE_BACKEND=sql-full-json-native   JsonPayload json  (native binary type)

This is deliberately NOT the hybrid model in `sql_repository.py`. There is one
row per LOGICAL ORDER and the entire payload lives in one column. Nothing is
split. `GET /orders/{id}` is a single-row point read whose body is handed to the
HTTP layer as raw bytes, never parsed and re-serialised.

Why two variants. Both are documented in SOURCES.md N.1/N.2 and the choice is
forced, not stylistic:

  * The native `json` type stores a parsed binary form and is the type Microsoft
    built for this. It is GA on Azure SQL Database and present on this server.
  * **A table containing a `json` column cannot be mirrored to Fabric.** So the
    native variant cannot feed the analytics requirement at all.

Measuring only the mirrorable variant would understate what Azure SQL can do;
measuring only the native one would recommend something undeployable. Both exist
so the report can state what the Fabric constraint actually costs in milliseconds
instead of asserting that it is "probably similar".

Reads that need only part of the order (title, cdf) extract server-side with
`JSON_QUERY` so a 450 KB section does not drag 5 MB across the network first.
That is the honest implementation of "as little reconstruction as reasonably
possible" - the alternative would make Scenario A look artificially bad on the
block endpoints for reasons that have nothing to do with how it stores data.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

import orjson
import pyodbc

from app.repositories.base import (
    IngestResult,
    OrderRepository,
    OrderSearchCriteria,
    block_response,
)
from app.repositories.full_document_common import (
    BLOCK_KEYS,
    NAMED_KEYS,
    derive_projection,
    object_data,
    sections_for_block,
    synthetic_parts,
)
from app.repositories.sql_repository import ConnectionPool, _CursorCtx, _guid
from app.telemetry.metrics import RequestMetrics

OBJECTDATA = "$.ExtractData.ExtractObjects[0].ObjectData"


class SqlFullJsonRepository(OrderRepository):
    """One row per logical order. `native=True` uses the `json` column type."""

    def __init__(
        self,
        server: str | None = None,
        database: str | None = None,
        pool_size: int | None = None,
        conn_str: str | None = None,
        native: bool | None = None,
    ) -> None:
        if native is None:
            native = os.getenv("SQL_FULL_JSON_NATIVE", "0").lower() in ("1", "true", "yes")
        self.native = native
        self.backend = "sql-full-json-native" if native else "sql-full-json"
        self.table = "ord.OrderDocumentsNative" if native else "ord.OrderDocuments"

        server = server or os.environ["SQL_SERVER"]
        database = database or os.getenv("SQL_DATABASE", "OrderDb")
        pool_size = pool_size or int(os.getenv("SQL_POOL_SIZE", "16"))
        self.conn_str = conn_str or (
            "Driver={ODBC Driver 18 for SQL Server};"
            f"Server=tcp:{server},1433;Database={database};"
            "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
        )
        self.pool = ConnectionPool(self.conn_str, size=pool_size, use_token=conn_str is None)

    # -- plumbing ----------------------------------------------------------

    def _cursor(self, m: RequestMetrics | None = None) -> _CursorCtx:
        return _CursorCtx(self, m)  # type: ignore[arg-type]

    @property
    def _payload_param(self) -> str:
        """How to BIND a payload parameter.

        The native `json` type accepts no implicit conversion from nvarchar
        (SOURCES.md N.1), so a plain `?` fails with
        `22018 Operand type clash: nvarchar(max) is incompatible with json`.
        An explicit CAST is required on every write. The nvarchar variant binds
        directly.
        """
        return "CAST(? AS json)" if self.native else "?"

    @property
    def _payload_expr(self) -> str:
        """How to SELECT the payload so pyodbc can bind the result.

        The native `json` type is not a type the ODBC driver maps (SOURCES.md
        N.1: drivers see varchar/nvarchar, and a direct fetch raises
        `ODBC SQL type -16 is not yet supported`). An explicit CAST is required,
        and that CAST is part of what the native variant costs - so it is inside
        the measured window rather than hidden.
        """
        return "CAST(JsonPayload AS nvarchar(max))" if self.native else "JsonPayload"

    def ping(self) -> dict[str, Any]:
        t0 = time.perf_counter()
        with self._cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {self.table}")
            n = cur.fetchval()
        return {
            "backend": self.backend,
            "ok": True,
            "table": self.table,
            "nativeJsonType": self.native,
            "documents": n,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    def close(self) -> None:
        self.pool.close()

    # -- reads -------------------------------------------------------------

    def get_summary(self, order_id: str, m: RequestMetrics) -> dict[str, Any] | None:
        """Served entirely from the stored projection - the payload is untouched."""
        with self._cursor(m) as cur:
            with m.db():  # type: ignore[attr-defined]
                cur.execute(
                    f"SELECT SummaryJson FROM {self.table} WHERE OrderId = ?", order_id
                )
                row = cur.fetchone()
            m.sql_queries += 1
        if row is None:
            return None
        with m.reconstruct():  # type: ignore[attr-defined]
            return orjson.loads(row.SummaryJson)

    def get_full_order(self, order_id: str, m: RequestMetrics) -> bytes | None:
        """The point of Scenario A: one row out, no parse, no reassembly.

        Returns raw UTF-8 bytes. The API layer passes them straight through, so
        `reconstruct` and `serialize` are both genuinely ~0 here and the
        comparison against the decomposed backends is not flattered by hiding
        their reassembly cost in ours.
        """
        with self._cursor(m) as cur:
            with m.db():  # type: ignore[attr-defined]
                cur.execute(
                    f"SELECT {self._payload_expr} AS P FROM {self.table} WHERE OrderId = ?",
                    order_id,
                )
                row = cur.fetchone()
            m.sql_queries += 1
        if row is None or row.P is None:
            return None
        m.blocks_read = 1
        with m.serialize():  # type: ignore[attr-defined]
            return row.P.encode("utf-8") if isinstance(row.P, str) else bytes(row.P)

    def get_block(
        self, order_id: str, block_type: str, m: RequestMetrics, block_sub_type: str | None = None
    ) -> dict[str, Any] | None:
        """Extract one section server-side rather than shipping the whole order.

        Key names come from BLOCK_MAP, a module constant - never from the
        request - so building the projection list into the SQL text is not an
        injection surface. The guard below enforces that invariant anyway.
        """
        keys: list[str] = []
        if block_type == "MISC":
            keys = []  # complement; needs the whole payload, handled below
        else:
            for (btype, sub), ks in BLOCK_KEYS.items():
                if btype != block_type:
                    continue
                if block_sub_type and sub != block_sub_type:
                    continue
                keys.extend(ks)
        if any(not k.isidentifier() for k in keys):  # pragma: no cover - defensive
            raise ValueError(f"refusing unsafe ObjectData key in {keys!r}")

        if keys:
            projections = ", ".join(
                f"JSON_QUERY({self._payload_expr}, '{OBJECTDATA}.{k}') AS [{k}]" for k in keys
            )
            sql = (
                f"SELECT CustomerId, OrderVersion, PayloadBytes, {projections} "
                f"FROM {self.table} WHERE OrderId = ?"
            )
            with self._cursor(m) as cur:
                with m.db():  # type: ignore[attr-defined]
                    cur.execute(sql, order_id)
                    row = cur.fetchone()
                m.sql_queries += 1
            if row is None:
                return None
            with m.reconstruct():  # type: ignore[attr-defined]
                sections = {
                    k: orjson.loads(getattr(row, k))
                    for k in keys
                    if getattr(row, k, None) is not None
                }
                if not sections:
                    return None
                m.blocks_read = len(sections)
                return block_response(
                    _guid(order_id), row.OrderVersion, row.CustomerId, block_type,
                    sections, synthetic_parts(block_type, sections, row.PayloadBytes),
                )

        # MISC: the complement of every named block is not expressible as a fixed
        # set of JSON paths, so this one case does read the payload.
        with self._cursor(m) as cur:
            with m.db():  # type: ignore[attr-defined]
                cur.execute(
                    f"SELECT CustomerId, OrderVersion, PayloadBytes, {self._payload_expr} AS P "
                    f"FROM {self.table} WHERE OrderId = ?",
                    order_id,
                )
                row = cur.fetchone()
            m.sql_queries += 1
        if row is None:
            return None
        with m.reconstruct():  # type: ignore[attr-defined]
            env = orjson.loads(row.P)
            sections = sections_for_block(env, block_type, block_sub_type)
            if not sections:
                return None
            m.blocks_read = len(sections)
            return block_response(
                _guid(order_id), row.OrderVersion, row.CustomerId, block_type,
                sections, synthetic_parts(block_type, sections, row.PayloadBytes),
            )

    def search_orders(self, c: OrderSearchCriteria, m: RequestMetrics) -> list[dict[str, Any]]:
        """Filters on the indexed projection columns, never on the payload."""
        where, params = ["1 = 1"], []
        if c.customer_id:
            where.append("CustomerId = ?")
            params.append(c.customer_id)
        if c.status:
            where.append("Status = ?")
            params.append(c.status)
        if c.state:
            where.append("PrimaryState = ?")
            params.append(c.state)
        if c.min_loan_amount is not None:
            where.append("MaxLoanAmount >= ?")
            params.append(c.min_loan_amount)
        sql = (
            f"SELECT TOP (?) OrderId, CustomerId, OrderVersion, OrderNumber, Status, "
            f"PrimaryState, MaxLoanAmount, TotalLoanAmount, PayloadBytes, UpdatedAt "
            f"FROM {self.table} WHERE {' AND '.join(where)} ORDER BY UpdatedAt DESC"
        )
        with self._cursor(m) as cur:
            with m.db():  # type: ignore[attr-defined]
                cur.execute(sql, int(c.limit), *params)
                rows = cur.fetchall()
            m.sql_queries += 1
        return [
            {
                "orderId": _guid(r.OrderId),
                "customerId": r.CustomerId,
                "orderVersion": r.OrderVersion,
                "orderNumber": r.OrderNumber,
                "status": r.Status,
                "state": r.PrimaryState,
                "maxLoanAmount": round(float(r.MaxLoanAmount), 2) if r.MaxLoanAmount is not None else None,
                "totalLoanAmount": round(float(r.TotalLoanAmount), 2) if r.TotalLoanAmount is not None else None,
                "payloadBytes": r.PayloadBytes,
            }
            for r in rows
        ]

    def list_order_ids(self, limit: int = 1000) -> list[dict[str, Any]]:
        with self._cursor() as cur:
            cur.execute(
                f"SELECT TOP (?) OrderId, PayloadBytes FROM {self.table} ORDER BY OrderId",
                int(limit),
            )
            return [
                {"orderId": _guid(r.OrderId), "payloadBytes": r.PayloadBytes}
                for r in cur.fetchall()
            ]

    # -- writes ------------------------------------------------------------

    def ingest_order(
        self, envelope: dict[str, Any], archive_uri: str | None = None, payload_hash: str | None = None
    ) -> IngestResult:
        t0 = time.perf_counter()
        payload = orjson.dumps(envelope)
        payload_bytes = len(payload)
        proj = derive_projection(envelope, payload_bytes)
        digest = payload_hash or hashlib.sha256(payload).hexdigest()
        text = payload.decode("utf-8")

        with self._cursor() as cur:
            # nvarchar(max) parameters must bind as SQL_WVARCHAR with precision 0.
            # Anything else raises HY104 on a multi-megabyte value - the defect
            # that once failed 100% of block writes in the hybrid path.
            #
            # pyodbc applies this list POSITIONALLY, so it must span all 30
            # parameters - this MERGE binds SummaryJson and the payload twice,
            # once per branch. A short list leaves the later long values on the
            # default binding, which is how HY104 appeared the first time.
            #
            # Only the four long strings are forced; everything else is None so
            # ints, decimals and the uniqueidentifier keep their natural types
            # instead of being coerced to wide strings.
            sizes: list[Any] = [None] * 30
            for i in (13, 14, 28, 29):  # SummaryJson, JsonPayload, x2 branches
                sizes[i] = (pyodbc.SQL_WVARCHAR, 0, 0)
            cur.setinputsizes(sizes)
            cur.execute(
                """
                MERGE {table} AS t
                USING (SELECT CAST(? AS uniqueidentifier) AS OrderId) AS s
                   ON t.OrderId = s.OrderId
                WHEN MATCHED THEN UPDATE SET
                    CustomerId = ?, OrderVersion = ?, OrderNumber = ?, Status = ?,
                    PrimaryState = ?, MaxLoanAmount = ?, TotalLoanAmount = ?,
                    PropertyCount = ?, LoanCount = ?, PartyCount = ?,
                    PayloadBytes = ?, PayloadHash = ?, SummaryJson = ?,
                    UpdatedAt = SYSUTCDATETIME(), JsonPayload = {payload_param}
                WHEN NOT MATCHED THEN INSERT
                    (OrderId, CustomerId, OrderVersion, OrderNumber, Status,
                     PrimaryState, MaxLoanAmount, TotalLoanAmount,
                     PropertyCount, LoanCount, PartyCount,
                     PayloadBytes, PayloadHash, SummaryJson, UpdatedAt, JsonPayload)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, SYSUTCDATETIME(), {payload_param});
                """.format(table=self.table, payload_param=self._payload_param),
                proj["orderId"],
                # matched
                proj["customerId"], proj["orderVersion"], proj["orderNumber"], proj["status"],
                proj["primaryState"], proj["maxLoanAmount"], proj["totalLoanAmount"],
                proj["propertyCount"], proj["loanCount"], proj["partyCount"],
                payload_bytes, digest, json.dumps(proj["summary"]), text,
                # not matched
                proj["orderId"], proj["customerId"], proj["orderVersion"], proj["orderNumber"],
                proj["status"], proj["primaryState"], proj["maxLoanAmount"], proj["totalLoanAmount"],
                proj["propertyCount"], proj["loanCount"], proj["partyCount"],
                payload_bytes, digest, json.dumps(proj["summary"]), text,
            )
            cur.connection.commit()

        return IngestResult(
            order_id=proj["orderId"],
            order_version=proj["orderVersion"],
            customer_id=proj["customerId"],
            blocks_written=1,
            items_written=1,
            bytes_written=payload_bytes,
            archive_uri=archive_uri,
            duration_ms=(time.perf_counter() - t0) * 1000,
            max_item_bytes=payload_bytes,
            extra={"table": self.table, "nativeJsonType": self.native, "payloadHash": digest},
        )

    def update_block(
        self, order_id: str, block_type: str, block_sub_type: str, sequence: int, payload: dict[str, Any]
    ) -> IngestResult:
        """Patch one section IN PLACE with JSON_MODIFY - no payload round-trip.

        This is the interesting half of the update comparison. A decomposed
        backend rewrites one small row. A full-document backend must either
        rewrite the whole document or modify it server-side; JSON_MODIFY is the
        latter, and measuring it is the only way to know whether "one big
        document is expensive to update" is actually true on Azure SQL.

        `modify()` on the native json type is documented as preview and SQL
        Server 2025 only (SOURCES.md N.1), so both variants use JSON_MODIFY.
        """
        keys = BLOCK_KEYS.get((block_type, block_sub_type))
        if not keys:
            raise ValueError(f"unknown block {block_type}/{block_sub_type}")
        key = keys[0]
        if not key.isidentifier():  # pragma: no cover - defensive
            raise ValueError(f"unsafe ObjectData key {key!r}")

        t0 = time.perf_counter()
        fragment = json.dumps(payload.get(key, payload))
        with self._cursor() as cur:
            cur.setinputsizes([(pyodbc.SQL_WVARCHAR, 0, 0)])
            if self.native:
                # Cast out, modify, cast back: the json type accepts an explicit
                # nvarchar conversion in both directions.
                sql = (
                    f"UPDATE {self.table} SET JsonPayload = CAST(JSON_MODIFY("
                    f"CAST(JsonPayload AS nvarchar(max)), '{OBJECTDATA}.{key}', "
                    f"JSON_QUERY(?)) AS json), UpdatedAt = SYSUTCDATETIME() WHERE OrderId = ?"
                )
            else:
                sql = (
                    f"UPDATE {self.table} SET JsonPayload = JSON_MODIFY("
                    f"JsonPayload, '{OBJECTDATA}.{key}', JSON_QUERY(?)), "
                    f"UpdatedAt = SYSUTCDATETIME() WHERE OrderId = ?"
                )
            cur.execute(sql, fragment, order_id)
            affected = cur.rowcount
            cur.connection.commit()

        return IngestResult(
            order_id=_guid(order_id), order_version=0, customer_id="",
            blocks_written=affected, items_written=affected, bytes_written=len(fragment),
            duration_ms=(time.perf_counter() - t0) * 1000,
            extra={"mode": "JSON_MODIFY", "path": f"{OBJECTDATA}.{key}", "table": self.table},
        )

    def update_order_header(self, order_id: str, changes: dict[str, Any]) -> IngestResult:
        """Small top-level change: the projection column AND the stored payload.

        Both must move or the row contradicts its own document. This is the
        hidden cost of keeping routing metadata outside the payload, and it is
        measured rather than assumed away.
        """
        t0 = time.perf_counter()
        status = changes.get("status") or changes.get("Status")
        if status is None:
            raise ValueError("update_order_header expects a 'status' change")
        with self._cursor() as cur:
            if self.native:
                expr = ("CAST(JSON_MODIFY(CAST(JsonPayload AS nvarchar(max)), "
                        f"'{OBJECTDATA}.Status', ?) AS json)")
            else:
                expr = f"JSON_MODIFY(JsonPayload, '{OBJECTDATA}.Status', ?)"
            cur.execute(
                f"UPDATE {self.table} SET Status = ?, JsonPayload = {expr}, "
                f"UpdatedAt = SYSUTCDATETIME() WHERE OrderId = ?",
                status, status, order_id,
            )
            affected = cur.rowcount
            cur.connection.commit()
        return IngestResult(
            order_id=_guid(order_id), order_version=0, customer_id="",
            blocks_written=affected, items_written=affected, bytes_written=len(str(status)),
            duration_ms=(time.perf_counter() - t0) * 1000,
            extra={"mode": "column+JSON_MODIFY", "table": self.table},
        )

    # -- extra surface used by the size/update experiments -----------------

    def replace_document(self, order_id: str, envelope: dict[str, Any]) -> IngestResult:
        """Scenario 9A: full replacement of the complete document."""
        return self.ingest_order(envelope)

    def resource_stats(self) -> dict[str, Any]:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT TOP 1 avg_cpu_percent, avg_data_io_percent, avg_log_write_percent
                FROM sys.dm_db_resource_stats ORDER BY end_time DESC
                """
            )
            r = cur.fetchone()
        if r is None:
            return {}
        return {
            "cpuPercent": float(r.avg_cpu_percent),
            "dataIoPercent": float(r.avg_data_io_percent),
            "logWritePercent": float(r.avg_log_write_percent),
        }
