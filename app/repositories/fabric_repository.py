"""OPTIONAL CONTROL (§24) - serve the API directly from Fabric / Delta.

This is NOT a third operational architecture. It exists to answer one question
with measurement instead of assertion:

    "Why not just have the API query Fabric/Delta directly?"

It queries the Fabric **SQL analytics endpoint** over the mirrored Delta tables
using T-SQL - the current supported low-latency query surface over OneLake. It
does NOT launch a Spark job per request; that would be an unfair strawman.

Read docs/FABRIC_DIRECT_SERVING_CONTROL.md before drawing any conclusion from
its numbers. Passing a throughput target is not the same as being architecturally
fit for operational serving.
"""

from __future__ import annotations

import json
import os
import struct
import time
from typing import Any

import pyodbc

from app.repositories.base import (
    IngestResult,
    OrderRepository,
    OrderSearchCriteria,
    block_response,
)
from app.repositories.sql_repository import ConnectionPool, _guid, _iso
from app.telemetry.metrics import RequestMetrics
from ingestion.parser.block_splitter import Block, build_envelope, reassemble

FABRIC_SCOPE = "https://analysis.windows.net/powerbi/api/.default"


class FabricOrderRepository(OrderRepository):
    """Reads the SQL-mirrored Delta tables through the Fabric SQL endpoint.

    The Delta schema is the mirror of ord.* from PATH A, so the reconstruction
    logic is identical to SqlOrderRepository - which is the point: any latency
    difference is the engine, not the data model.
    """

    backend = "fabric"

    def __init__(
        self,
        endpoint: str | None = None,
        database: str | None = None,
        pool_size: int | None = None,
    ) -> None:
        endpoint = endpoint or os.environ["FABRIC_SQL_ENDPOINT"]
        database = database or os.environ["FABRIC_SQL_DATABASE"]
        pool_size = pool_size or int(os.getenv("FABRIC_POOL_SIZE", "16"))

        conn_str = (
            "Driver={ODBC Driver 18 for SQL Server};"
            f"Server={endpoint},1433;Database={database};"
            "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60;"
        )
        self.pool = _FabricPool(conn_str, size=pool_size)
        self.schema = os.getenv("FABRIC_SCHEMA", "dbo")

    # -- plumbing ----------------------------------------------------------

    def ping(self) -> dict[str, Any]:
        t0 = time.perf_counter()
        conn = self.pool.acquire()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
        finally:
            self.pool.release(conn)
        return {
            "backend": "fabric",
            "ok": True,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
            "pool_size": self.pool.size,
            "note": "CONTROL EXPERIMENT - not a recommended serving path",
        }

    def close(self) -> None:
        self.pool.close()

    def _query(self, m: RequestMetrics | None, sql: str, *params: Any) -> list[Any]:
        conn = self.pool.acquire()
        try:
            cur = conn.cursor()
            if m is not None:
                with m.db():  # type: ignore[attr-defined]
                    cur.execute(sql, *params)
                    rows = cur.fetchall()
                m.sql_queries += 1
            else:
                cur.execute(sql, *params)
                rows = cur.fetchall()
            return rows
        finally:
            self.pool.release(conn)

    # -- reads -------------------------------------------------------------

    def get_summary(self, order_id: str, m: RequestMetrics) -> dict[str, Any] | None:
        rows = self._query(
            m,
            f"""
            SELECT TOP 1 o.OrderId, o.CustomerId, o.CurrentVersion, o.OrderNumber, o.OrderType,
                   o.Status, o.Project, o.SettlementType, o.IsCommercial, o.IsRush, o.Balance,
                   o.CreatedDate, o.SettlementDate, o.DisbursementDate,
                   o.PrimaryState, o.PrimaryCounty, o.MaxLoanAmount, o.PayloadBytes
            FROM {self.schema}.Orders o WHERE o.OrderId = ?
            """,
            order_id,
        )
        if not rows:
            return None
        r = rows[0]
        counts = self._query(
            m,
            f"""
            SELECT
              (SELECT COUNT(*) FROM {self.schema}.Properties   WHERE OrderId = ?) AS P,
              (SELECT COUNT(*) FROM {self.schema}.Loans        WHERE OrderId = ?) AS L,
              (SELECT COUNT(*) FROM {self.schema}.OrderParties WHERE OrderId = ?) AS PA,
              (SELECT SUM(LoanAmount) FROM {self.schema}.Loans WHERE OrderId = ?) AS TL
            """,
            order_id, order_id, order_id, order_id,
        )[0]
        addr = self._query(
            m, f"SELECT TOP 1 Address1, City FROM {self.schema}.Properties WHERE OrderId = ? ORDER BY Sequence",
            order_id,
        )
        roles = self._query(
            m, f"SELECT DISTINCT Role FROM {self.schema}.OrderParties WHERE OrderId = ?", order_id
        )

        with m.reconstruct():  # type: ignore[attr-defined]
            return {
                "orderId": _guid(r.OrderId),
                "customerId": r.CustomerId,
                "orderVersion": r.CurrentVersion,
                "orderNumber": r.OrderNumber,
                "orderType": r.OrderType,
                "status": r.Status,
                "project": r.Project,
                "settlementType": r.SettlementType,
                "isCommercial": bool(r.IsCommercial),
                "isRush": bool(r.IsRush),
                "balance": round(float(r.Balance), 2) if r.Balance is not None else None,
                "createdDate": _iso(r.CreatedDate),
                "settlementDate": _iso(r.SettlementDate),
                "disbursementDate": _iso(r.DisbursementDate),
                "property": {
                    "address1": addr[0].Address1 if addr else None,
                    "city": addr[0].City if addr else None,
                    "state": r.PrimaryState,
                    "county": r.PrimaryCounty,
                },
                "counts": {"properties": counts.P, "loans": counts.L, "parties": counts.PA},
                "loanTotals": {
                    "maxLoanAmount": round(float(r.MaxLoanAmount), 2) if r.MaxLoanAmount is not None else None,
                    "totalLoanAmount": round(float(counts.TL), 2) if counts.TL is not None else None,
                },
                "roles": sorted(x[0] for x in roles),
                "payloadBytes": r.PayloadBytes,
            }

    def get_block(
        self, order_id: str, block_type: str, m: RequestMetrics, block_sub_type: str | None = None
    ) -> dict[str, Any] | None:
        sql = f"""
            SELECT b.BlockType, b.BlockSubType, b.Sequence, b.PayloadBytes, b.JsonPayload,
                   o.CustomerId, o.CurrentVersion
            FROM {self.schema}.OrderJsonBlocks b
            JOIN {self.schema}.Orders o
              ON o.OrderId = b.OrderId AND o.CurrentVersion = b.OrderVersion
            WHERE b.OrderId = ? AND b.BlockType = ?
        """
        params: list[Any] = [order_id, block_type]
        if block_sub_type:
            sql += " AND b.BlockSubType = ?"
            params.append(block_sub_type)
        rows = self._query(m, sql, *params)
        if not rows:
            return None
        with m.reconstruct():  # type: ignore[attr-defined]
            blocks = [Block(r.BlockType, r.BlockSubType, r.Sequence, json.loads(r.JsonPayload)) for r in rows]
            m.blocks_read = len(blocks)
            parts = [{"blockSubType": r.BlockSubType, "sequence": r.Sequence,
                      "payloadBytes": r.PayloadBytes} for r in rows]
            return block_response(_guid(order_id), rows[0].CurrentVersion, rows[0].CustomerId,
                                  block_type, reassemble(blocks), parts)

    def get_full_order(self, order_id: str, m: RequestMetrics) -> dict[str, Any] | None:
        head = self._query(
            m, f"SELECT TOP 1 CustomerId, CurrentVersion FROM {self.schema}.Orders WHERE OrderId = ?",
            order_id,
        )
        if not head:
            return None
        h = head[0]
        rows = self._query(
            m,
            f"""
            SELECT BlockType, BlockSubType, Sequence, JsonPayload
            FROM {self.schema}.OrderJsonBlocks
            WHERE OrderId = ? AND OrderVersion = ?
            """,
            order_id, h.CurrentVersion,
        )
        with m.reconstruct():  # type: ignore[attr-defined]
            blocks = [Block(r.BlockType, r.BlockSubType, r.Sequence, json.loads(r.JsonPayload)) for r in rows]
            m.blocks_read = len(blocks)
            return build_envelope(h.CustomerId, _guid(order_id), h.CurrentVersion, reassemble(blocks))

    def search_orders(self, c: OrderSearchCriteria, m: RequestMetrics) -> list[dict[str, Any]]:
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
        rows = self._query(
            m,
            f"""
            SELECT TOP (?) OrderId, CustomerId, CurrentVersion, OrderNumber, Status, OrderType,
                   Project, SettlementDate, PrimaryState, PrimaryCounty, MaxLoanAmount, PayloadBytes
            FROM {self.schema}.Orders WHERE {' AND '.join(where)}
            ORDER BY ModifiedDate DESC
            """,
            c.limit, *params,
        )
        return [
            {
                "orderId": _guid(r.OrderId), "customerId": r.CustomerId,
                "orderVersion": r.CurrentVersion, "orderNumber": r.OrderNumber,
                "status": r.Status, "orderType": r.OrderType, "project": r.Project,
                "settlementDate": _iso(r.SettlementDate), "state": r.PrimaryState,
                "county": r.PrimaryCounty,
                "maxLoanAmount": round(float(r.MaxLoanAmount), 2) if r.MaxLoanAmount is not None else None,
                "payloadBytes": r.PayloadBytes,
            }
            for r in rows
        ]

    def list_order_ids(self, limit: int = 1000) -> list[dict[str, Any]]:
        rows = self._query(
            None,
            f"SELECT TOP (?) OrderId, CustomerId, CurrentVersion, PayloadBytes "
            f"FROM {self.schema}.Orders ORDER BY OrderId",
            limit,
        )
        return [{"orderId": _guid(r.OrderId), "customerId": r.CustomerId,
                 "orderVersion": r.CurrentVersion, "payloadBytes": r.PayloadBytes} for r in rows]

    # -- writes: not supported, and that is the point ----------------------

    _WRITE_MSG = (
        "The Fabric SQL analytics endpoint over mirrored Delta is READ-ONLY. "
        "Writes must go to the operational store, which is exactly why direct "
        "Fabric serving cannot replace an operational database - see "
        "docs/FABRIC_DIRECT_SERVING_CONTROL.md"
    )

    def ingest_order(self, envelope: dict[str, Any], archive_uri: str | None = None,
                     payload_hash: str | None = None) -> IngestResult:
        raise NotImplementedError(self._WRITE_MSG)

    def update_block(self, order_id: str, block_type: str, block_sub_type: str,
                     sequence: int, payload: dict[str, Any]) -> IngestResult:
        raise NotImplementedError(self._WRITE_MSG)

    def update_order_header(self, order_id: str, changes: dict[str, Any]) -> IngestResult:
        raise NotImplementedError(self._WRITE_MSG)


class _FabricPool(ConnectionPool):
    """Connection pool that mints Fabric (Power BI) tokens rather than SQL ones."""

    def __init__(self, conn_str: str, size: int = 16) -> None:
        super().__init__(conn_str, size=size, use_token=True)
        self._cred: Any = None
        self._token: bytes | None = None
        self._expires = 0.0

    def _fabric_token(self) -> bytes:
        if self._token and time.time() < self._expires - 300:
            return self._token
        if self._cred is None:
            from azure.identity import DefaultAzureCredential

            self._cred = DefaultAzureCredential()
        tok = self._cred.get_token(FABRIC_SCOPE)
        raw = tok.token.encode("utf-16-le")
        self._token = struct.pack(f"<I{len(raw)}s", len(raw), raw)
        self._expires = tok.expires_on
        return self._token

    def _new(self) -> pyodbc.Connection:
        conn = pyodbc.connect(self.conn_str, attrs_before={1256: self._fabric_token()}, autocommit=True)
        conn.setdecoding(pyodbc.SQL_WCHAR, encoding="utf-16le")
        return conn
