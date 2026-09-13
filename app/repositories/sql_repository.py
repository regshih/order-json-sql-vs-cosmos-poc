"""PATH A - Azure SQL Database hybrid relational + JSON repository.

Reads:
  summary  -> one indexed row from ord.Orders (no LOB touched)
  block    -> N JsonPayload rows filtered by BlockType
  full     -> all relational rows + all JsonPayload rows, reassembled

Authentication is Entra-only via DefaultAzureCredential; there are no passwords
or connection-string secrets anywhere in this module.
"""

from __future__ import annotations

import json
import os
import struct
import threading
import time
from datetime import datetime
from queue import Empty, LifoQueue
from typing import Any, Iterator

import pyodbc

from app.repositories.base import (
    BLOCK_ENDPOINTS,
    IngestResult,
    OrderRepository,
    OrderSearchCriteria,
    block_response,
)
from app.telemetry.metrics import RequestMetrics
from ingestion.parser.block_splitter import (
    Block,
    build_envelope,
    parse_envelope,
    reassemble,
    split_object_data,
)
from ingestion.parser.relational_extract import extract, summary_from_projection

# ODBC attribute id for an Entra access token.
SQL_COPT_SS_ACCESS_TOKEN = 1256
TOKEN_SCOPE = "https://database.windows.net/.default"


class TokenCache:
    """Entra tokens are valid for ~1 h. Fetching one per connection would add
    an HTTP round trip to every pool refill, so cache until 5 min before expiry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token: bytes | None = None
        self._expires: float = 0.0
        self._cred: Any = None

    def get(self) -> bytes:
        with self._lock:
            if self._token and time.time() < self._expires - 300:
                return self._token
            if self._cred is None:
                from azure.identity import DefaultAzureCredential

                self._cred = DefaultAzureCredential(exclude_interactive_browser_credential=False)
            tok = self._cred.get_token(TOKEN_SCOPE)
            raw = tok.token.encode("utf-16-le")
            self._token = struct.pack(f"<I{len(raw)}s", len(raw), raw)
            self._expires = tok.expires_on
            return self._token


_tokens = TokenCache()


class ConnectionPool:
    """Explicit pool.

    pyodbc's built-in pooling is process-global and opaque; an explicit pool
    lets us measure checkout time (reported as ``sql_pool_checkout_ms``) and
    size it deliberately for the 50-RPS target.
    """

    def __init__(self, conn_str: str, size: int = 16, use_token: bool = True) -> None:
        self.conn_str = conn_str
        self.size = size
        self.use_token = use_token
        self._pool: LifoQueue[pyodbc.Connection] = LifoQueue(maxsize=size)
        self._created = 0
        self._lock = threading.Lock()
        self.checkout_waits = 0

    def _new(self) -> pyodbc.Connection:
        attrs = {SQL_COPT_SS_ACCESS_TOKEN: _tokens.get()} if self.use_token else None
        conn = pyodbc.connect(self.conn_str, attrs_before=attrs, autocommit=True)
        # Large LOB reads: let pyodbc stream rather than buffering in 1 KB steps.
        conn.setdecoding(pyodbc.SQL_WCHAR, encoding="utf-16le")
        conn.maxwrite = 1024 * 1024 * 8
        return conn

    def acquire(self, timeout: float = 30.0) -> pyodbc.Connection:
        try:
            return self._pool.get_nowait()
        except Empty:
            pass
        with self._lock:
            if self._created < self.size:
                self._created += 1
                return self._new()
        self.checkout_waits += 1
        return self._pool.get(timeout=timeout)

    def release(self, conn: pyodbc.Connection, broken: bool = False) -> None:
        if broken:
            with self._lock:
                self._created -= 1
            try:
                conn.close()
            except Exception:
                pass
            return
        try:
            self._pool.put_nowait(conn)
        except Exception:
            conn.close()
            with self._lock:
                self._created -= 1

    def close(self) -> None:
        while True:
            try:
                self._pool.get_nowait().close()
            except Exception:
                break


class SqlOrderRepository(OrderRepository):
    backend = "sql"

    def __init__(
        self,
        server: str | None = None,
        database: str | None = None,
        pool_size: int | None = None,
        conn_str: str | None = None,
    ) -> None:
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

    def _cursor(self, m: RequestMetrics | None = None) -> "_CursorCtx":
        return _CursorCtx(self, m)

    def ping(self) -> dict[str, Any]:
        t0 = time.perf_counter()
        with self._cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return {
            "backend": "sql",
            "ok": True,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
            "pool_size": self.pool.size,
            "pool_created": self.pool._created,
        }

    def close(self) -> None:
        self.pool.close()

    # -- reads -------------------------------------------------------------

    SUMMARY_SQL = """
        SELECT o.OrderId, o.CustomerId, o.CurrentVersion, o.OrderNumber, o.OrderType,
               o.Status, o.Project, o.SettlementType, o.IsCommercial, o.IsRush, o.Balance,
               o.CreatedDate, o.SettlementDate, o.DisbursementDate,
               o.PrimaryState, o.PrimaryCounty, o.MaxLoanAmount, o.PayloadBytes, o.BlockCount,
               p.Address1, p.City,
               (SELECT COUNT(*) FROM ord.Properties  WHERE OrderId = o.OrderId) AS PropertyCount,
               (SELECT COUNT(*) FROM ord.Loans       WHERE OrderId = o.OrderId) AS LoanCount,
               (SELECT COUNT(*) FROM ord.OrderParties WHERE OrderId = o.OrderId) AS PartyCount,
               (SELECT SUM(LoanAmount) FROM ord.Loans WHERE OrderId = o.OrderId) AS TotalLoanAmount
        FROM ord.Orders o
        OUTER APPLY (SELECT TOP 1 Address1, City FROM ord.Properties
                     WHERE OrderId = o.OrderId ORDER BY Sequence) p
        WHERE o.OrderId = ?
    """

    def get_summary(self, order_id: str, m: RequestMetrics) -> dict[str, Any] | None:
        with self._cursor(m) as cur:
            with m.db():  # type: ignore[attr-defined]
                cur.execute(self.SUMMARY_SQL, order_id)
                row = cur.fetchone()
            m.sql_queries += 1
        if row is None:
            return None

        with m.reconstruct():  # type: ignore[attr-defined]
            roles = self._roles(order_id, m)
            body = {
                "orderId": str(row.OrderId),
                "customerId": row.CustomerId,
                "orderVersion": row.CurrentVersion,
                "orderNumber": row.OrderNumber,
                "orderType": row.OrderType,
                "status": row.Status,
                "project": row.Project,
                "settlementType": row.SettlementType,
                "isCommercial": bool(row.IsCommercial),
                "isRush": bool(row.IsRush),
                "balance": float(row.Balance) if row.Balance is not None else None,
                "createdDate": _iso(row.CreatedDate),
                "settlementDate": _iso(row.SettlementDate),
                "disbursementDate": _iso(row.DisbursementDate),
                "property": {
                    "address1": row.Address1,
                    "city": row.City,
                    "state": row.PrimaryState,
                    "county": row.PrimaryCounty,
                },
                "counts": {
                    "properties": row.PropertyCount,
                    "loans": row.LoanCount,
                    "parties": row.PartyCount,
                },
                "loanTotals": {
                    "maxLoanAmount": float(row.MaxLoanAmount) if row.MaxLoanAmount is not None else None,
                    "totalLoanAmount": float(row.TotalLoanAmount) if row.TotalLoanAmount is not None else None,
                },
                "roles": roles,
                "payloadBytes": row.PayloadBytes,
            }
        return body

    def _roles(self, order_id: str, m: RequestMetrics) -> list[str]:
        with self._cursor(m) as cur:
            with m.db():  # type: ignore[attr-defined]
                cur.execute(
                    "SELECT DISTINCT Role FROM ord.OrderParties WHERE OrderId = ?", order_id
                )
                rows = cur.fetchall()
            m.sql_queries += 1
        return sorted(r[0] for r in rows)

    def get_block(
        self, order_id: str, block_type: str, m: RequestMetrics, block_sub_type: str | None = None
    ) -> dict[str, Any] | None:
        sql = """
            SELECT b.BlockType, b.BlockSubType, b.Sequence, b.PayloadBytes, b.JsonPayload,
                   o.CustomerId, o.CurrentVersion
            FROM ord.OrderJsonBlocks b
            JOIN ord.Orders o ON o.OrderId = b.OrderId AND o.CurrentVersion = b.OrderVersion
            WHERE b.OrderId = ? AND b.BlockType = ?
        """
        params: list[Any] = [order_id, block_type]
        if block_sub_type:
            sql += " AND b.BlockSubType = ?"
            params.append(block_sub_type)
        sql += " ORDER BY b.BlockSubType, b.Sequence"

        with self._cursor(m) as cur:
            with m.db():  # type: ignore[attr-defined]
                cur.execute(sql, *params)
                rows = cur.fetchall()
            m.sql_queries += 1
        if not rows:
            return None

        with m.reconstruct():  # type: ignore[attr-defined]
            blocks = [
                Block(r.BlockType, r.BlockSubType, r.Sequence, json.loads(r.JsonPayload))
                for r in rows
            ]
            m.blocks_read = len(blocks)
            sections = reassemble(blocks)
            parts = [
                {
                    "blockSubType": r.BlockSubType,
                    "sequence": r.Sequence,
                    "payloadBytes": r.PayloadBytes,
                }
                for r in rows
            ]
            return block_response(
                order_id, rows[0].CurrentVersion, rows[0].CustomerId, block_type, sections, parts
            )

    def get_full_order(self, order_id: str, m: RequestMetrics) -> dict[str, Any] | None:
        with self._cursor(m) as cur:
            with m.db():  # type: ignore[attr-defined]
                cur.execute(
                    """
                    SELECT o.CustomerId, o.CurrentVersion, v.ExtractTimestamp, o.PayloadBytes
                    FROM ord.Orders o
                    LEFT JOIN ord.OrderVersions v
                           ON v.OrderId = o.OrderId AND v.Version = o.CurrentVersion
                    WHERE o.OrderId = ?
                    """,
                    order_id,
                )
                head = cur.fetchone()
                if head is None:
                    return None
                cur.execute(
                    """
                    SELECT BlockType, BlockSubType, Sequence, JsonPayload
                    FROM ord.OrderJsonBlocks
                    WHERE OrderId = ? AND OrderVersion = ?
                    ORDER BY BlockType, BlockSubType, Sequence
                    """,
                    order_id,
                    head.CurrentVersion,
                )
                rows = cur.fetchall()
            m.sql_queries += 2

        with m.reconstruct():  # type: ignore[attr-defined]
            blocks = [
                Block(r.BlockType, r.BlockSubType, r.Sequence, json.loads(r.JsonPayload))
                for r in rows
            ]
            m.blocks_read = len(blocks)
            object_data = reassemble(blocks)
            return build_envelope(
                head.CustomerId,
                order_id,
                head.CurrentVersion,
                object_data,
                extract_timestamp=_iso(head.ExtractTimestamp),
            )

    def search_orders(self, c: OrderSearchCriteria, m: RequestMetrics) -> list[dict[str, Any]]:
        where, params = ["1 = 1"], []
        if c.customer_id:
            where.append("o.CustomerId = ?")
            params.append(c.customer_id)
        if c.status:
            where.append("o.Status = ?")
            params.append(c.status)
        if c.state:
            where.append("o.PrimaryState = ?")
            params.append(c.state)
        if c.min_loan_amount is not None:
            where.append("o.MaxLoanAmount >= ?")
            params.append(c.min_loan_amount)

        sql = f"""
            SELECT TOP (?) o.OrderId, o.CustomerId, o.CurrentVersion, o.OrderNumber, o.Status,
                   o.OrderType, o.Project, o.SettlementDate, o.PrimaryState, o.PrimaryCounty,
                   o.MaxLoanAmount, o.PayloadBytes
            FROM ord.Orders o
            WHERE {' AND '.join(where)}
            ORDER BY o.ModifiedDate DESC
        """
        with self._cursor(m) as cur:
            with m.db():  # type: ignore[attr-defined]
                cur.execute(sql, c.limit, *params)
                rows = cur.fetchall()
            m.sql_queries += 1

        return [
            {
                "orderId": str(r.OrderId),
                "customerId": r.CustomerId,
                "orderVersion": r.CurrentVersion,
                "orderNumber": r.OrderNumber,
                "status": r.Status,
                "orderType": r.OrderType,
                "project": r.Project,
                "settlementDate": _iso(r.SettlementDate),
                "state": r.PrimaryState,
                "county": r.PrimaryCounty,
                "maxLoanAmount": float(r.MaxLoanAmount) if r.MaxLoanAmount is not None else None,
                "payloadBytes": r.PayloadBytes,
            }
            for r in rows
        ]

    def list_order_ids(self, limit: int = 1000) -> list[dict[str, Any]]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT TOP (?) OrderId, CustomerId, CurrentVersion, PayloadBytes "
                "FROM ord.Orders ORDER BY OrderId",
                limit,
            )
            return [
                {
                    "orderId": str(r.OrderId),
                    "customerId": r.CustomerId,
                    "orderVersion": r.CurrentVersion,
                    "payloadBytes": r.PayloadBytes,
                }
                for r in cur.fetchall()
            ]

    # -- writes ------------------------------------------------------------

    def ingest_order(
        self, envelope_doc: dict[str, Any], archive_uri: str | None = None, payload_hash: str | None = None
    ) -> IngestResult:
        t0 = time.perf_counter()
        env = parse_envelope(envelope_doc)
        proj = extract(env)
        blocks = split_object_data(env["objectData"])
        payload_bytes = sum(b.payload_bytes for b in blocks)

        order_id = env["orderId"]
        version = env["orderVersion"]
        sf = proj["searchFields"]

        conn = self.pool.acquire()
        broken = False
        try:
            conn.autocommit = False
            cur = conn.cursor()
            cur.fast_executemany = True

            cur.execute(
                """
                MERGE ord.Customers AS t
                USING (SELECT ? AS CustomerId) AS s ON t.CustomerId = s.CustomerId
                WHEN NOT MATCHED THEN
                    INSERT (CustomerId, CustomerName, TenantKey)
                    VALUES (s.CustomerId, s.CustomerId, s.CustomerId);
                """,
                proj["order"]["CustomerId"],
            )

            o = proj["order"]
            cur.execute(
                """
                MERGE ord.Orders AS t
                USING (SELECT ? AS OrderId) AS s ON t.OrderId = s.OrderId
                WHEN MATCHED THEN UPDATE SET
                    CustomerId=?, CurrentVersion=?, OrderNumber=?, OrderType=?, Status=?,
                    Project=?, SettlementType=?, IsCommercial=?, IsRush=?, Balance=?,
                    CreatedDate=?, SettlementDate=?, DisbursementDate=?, CompletedDate=?,
                    ExtractDate=?, PrimaryState=?, PrimaryCounty=?, MaxLoanAmount=?,
                    PayloadBytes=?, BlockCount=?, ModifiedDate=SYSUTCDATETIME()
                WHEN NOT MATCHED THEN INSERT
                    (OrderId, CustomerId, CurrentVersion, OrderNumber, OrderType, Status,
                     Project, SettlementType, IsCommercial, IsRush, Balance, CreatedDate,
                     SettlementDate, DisbursementDate, CompletedDate, ExtractDate,
                     PrimaryState, PrimaryCounty, MaxLoanAmount, PayloadBytes, BlockCount)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);
                """,
                order_id,
                # UPDATE
                o["CustomerId"], o["CurrentVersion"], o["OrderNumber"], o["OrderType"], o["Status"],
                o["Project"], o["SettlementType"], o["IsCommercial"], o["IsRush"], o["Balance"],
                o["CreatedDate"], o["SettlementDate"], o["DisbursementDate"], o["CompletedDate"],
                o["ExtractDate"], sf["state"], sf["county"], sf["maxLoanAmount"],
                payload_bytes, len(blocks),
                # INSERT
                order_id,
                o["CustomerId"], o["CurrentVersion"], o["OrderNumber"], o["OrderType"], o["Status"],
                o["Project"], o["SettlementType"], o["IsCommercial"], o["IsRush"], o["Balance"],
                o["CreatedDate"], o["SettlementDate"], o["DisbursementDate"], o["CompletedDate"],
                o["ExtractDate"], sf["state"], sf["county"], sf["maxLoanAmount"],
                payload_bytes, len(blocks),
            )

            cur.execute(
                """
                MERGE ord.OrderVersions AS t
                USING (SELECT ? AS OrderId, ? AS Version) AS s
                   ON t.OrderId = s.OrderId AND t.Version = s.Version
                WHEN MATCHED THEN UPDATE SET
                    ExtractTimestamp=?, SourceArchiveUri=?, PayloadHash=?, PayloadBytes=?, BlockCount=?
                WHEN NOT MATCHED THEN INSERT
                    (OrderId, Version, ExtractTimestamp, SourceArchiveUri, PayloadHash, PayloadBytes, BlockCount)
                    VALUES (?,?,?,?,?,?,?);
                """,
                order_id, version,
                o["ExtractDate"], archive_uri, payload_hash, payload_bytes, len(blocks),
                order_id, version, o["ExtractDate"], archive_uri, payload_hash, payload_bytes, len(blocks),
            )

            # Child rows are version-scoped state: delete + reinsert is both
            # simpler and faster than row-by-row merge for <100 rows.
            for table, key in (
                ("ord.OrderJsonBlocks", "OrderId"),
                ("ord.Properties", "OrderId"),
                ("ord.OrderParties", "OrderId"),
                ("ord.Loans", "OrderId"),
            ):
                cur.execute(f"DELETE FROM {table} WHERE {key} = ?", order_id)

            if proj["parties"]:
                cur.executemany(
                    """
                    MERGE ord.Parties AS t
                    USING (SELECT ? AS PartyId) AS s ON t.PartyId = s.PartyId
                    WHEN MATCHED THEN UPDATE SET
                        PartyType=?, FirstName=?, LastName=?, CompanyName=?, DisplayName=?, Email=?, Phone=?
                    WHEN NOT MATCHED THEN INSERT
                        (PartyId, PartyType, FirstName, LastName, CompanyName, DisplayName, Email, Phone)
                        VALUES (?,?,?,?,?,?,?,?);
                    """,
                    [
                        (p["PartyId"], p["PartyType"], p["FirstName"], p["LastName"],
                         p["CompanyName"], p["DisplayName"], p["Email"], p["Phone"],
                         p["PartyId"], p["PartyType"], p["FirstName"], p["LastName"],
                         p["CompanyName"], p["DisplayName"], p["Email"], p["Phone"])
                        for p in proj["parties"]
                    ],
                )

            if proj["properties"]:
                cur.executemany(
                    "INSERT INTO ord.Properties (PropertyId, OrderId, Sequence, Address1, Address2, "
                    "City, [State], Zip, County, Acreage, PropertyType) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (p["PropertyId"], p["OrderId"], p["Sequence"], p["Address1"], p["Address2"],
                         p["City"], p["State"], p["Zip"], p["County"], p["Acreage"], p["PropertyType"])
                        for p in proj["properties"]
                    ],
                )

            if proj["orderParties"]:
                seen = set()
                rows = []
                for op in proj["orderParties"]:
                    k = (op["OrderId"], op["PartyId"], op["Role"], op["Sequence"])
                    if k in seen:
                        continue
                    seen.add(k)
                    rows.append(k)
                cur.executemany(
                    "INSERT INTO ord.OrderParties (OrderId, PartyId, Role, Sequence) VALUES (?,?,?,?)",
                    rows,
                )

            if proj["loans"]:
                cur.executemany(
                    "INSERT INTO ord.Loans (LoanId, OrderId, Sequence, LenderPartyId, LoanAmount, "
                    "LoanType, LoanNumber, InterestRate, LoanTermMonths) VALUES (?,?,?,?,?,?,?,?,?)",
                    [
                        (l["LoanId"], l["OrderId"], l["Sequence"], l["LenderPartyId"], l["LoanAmount"],
                         l["LoanType"], l["LoanNumber"], l["InterestRate"], l["LoanTermMonths"])
                        for l in proj["loans"]
                    ],
                )

            cur.executemany(
                "INSERT INTO ord.OrderJsonBlocks (OrderId, OrderVersion, BlockType, BlockSubType, "
                "Sequence, JsonPayload, PayloadBytes, PayloadHash) VALUES (?,?,?,?,?,?,?,?)",
                [
                    (order_id, version, b.block_type, b.block_sub_type, b.sequence,
                     b.compact.decode("utf-8"), b.payload_bytes, b.payload_hash)
                    for b in blocks
                ],
            )

            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                broken = True
            raise
        finally:
            conn.autocommit = True
            self.pool.release(conn, broken=broken)

        return IngestResult(
            order_id=order_id,
            order_version=version,
            customer_id=env["customerId"],
            blocks_written=len(blocks),
            items_written=len(blocks),
            bytes_written=payload_bytes,
            archive_uri=archive_uri,
            duration_ms=(time.perf_counter() - t0) * 1000,
            max_item_bytes=max((b.payload_bytes for b in blocks), default=0),
        )

    def update_block(
        self, order_id: str, block_type: str, block_sub_type: str, sequence: int, payload: dict[str, Any]
    ) -> IngestResult:
        t0 = time.perf_counter()
        b = Block(block_type, block_sub_type, sequence, payload)
        with self._cursor() as cur:
            cur.execute(
                """
                UPDATE ord.OrderJsonBlocks
                SET JsonPayload = ?, PayloadBytes = ?, PayloadHash = ?, LastModified = SYSUTCDATETIME()
                WHERE OrderId = ? AND BlockType = ? AND BlockSubType = ? AND Sequence = ?
                  AND OrderVersion = (SELECT CurrentVersion FROM ord.Orders WHERE OrderId = ?)
                """,
                b.compact.decode("utf-8"), b.payload_bytes, b.payload_hash,
                order_id, block_type, block_sub_type, sequence, order_id,
            )
            affected = cur.rowcount
            cur.execute(
                "UPDATE ord.Orders SET ModifiedDate = SYSUTCDATETIME() WHERE OrderId = ?", order_id
            )
        return IngestResult(
            order_id=order_id, order_version=0, customer_id="",
            blocks_written=affected, items_written=affected, bytes_written=b.payload_bytes,
            duration_ms=(time.perf_counter() - t0) * 1000,
            extra={"rowsAffected": affected},
        )

    def update_order_header(self, order_id: str, changes: dict[str, Any]) -> IngestResult:
        t0 = time.perf_counter()
        allowed = {"Status", "StatusComment", "Project", "Balance", "SettlementDate", "IsRush"}
        sets = {k: v for k, v in changes.items() if k in allowed}
        if not sets:
            raise ValueError(f"no updatable header fields in {list(changes)}; allowed: {sorted(allowed)}")
        clause = ", ".join(f"{k} = ?" for k in sets)
        with self._cursor() as cur:
            cur.execute(
                f"UPDATE ord.Orders SET {clause}, ModifiedDate = SYSUTCDATETIME() WHERE OrderId = ?",
                *sets.values(), order_id,
            )
            affected = cur.rowcount
        return IngestResult(
            order_id=order_id, order_version=0, customer_id="",
            blocks_written=0, items_written=affected, bytes_written=0,
            duration_ms=(time.perf_counter() - t0) * 1000,
            extra={"rowsAffected": affected},
        )

    # -- utilisation -------------------------------------------------------

    def resource_stats(self) -> dict[str, Any]:
        """Azure SQL resource utilisation over the last ~15 minutes, from the
        service's own DMV - avoids guessing at CPU/IO from the client side."""
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT TOP 30 end_time, avg_cpu_percent, avg_data_io_percent,
                       avg_log_write_percent, max_worker_percent, max_session_percent,
                       avg_instance_cpu_percent, avg_memory_usage_percent
                FROM sys.dm_db_resource_stats ORDER BY end_time DESC
                """
            )
            rows = cur.fetchall()
            cur.execute(
                "SELECT COUNT(*) FROM sys.dm_exec_connections WHERE session_id <> @@SPID"
            )
            conns = cur.fetchval()
        samples = [
            {
                "endTime": _iso(r.end_time),
                "cpuPct": r.avg_cpu_percent,
                "dataIoPct": r.avg_data_io_percent,
                "logWritePct": r.avg_log_write_percent,
                "workerPct": r.max_worker_percent,
                "sessionPct": r.max_session_percent,
                "memoryPct": r.avg_memory_usage_percent,
            }
            for r in rows
        ]
        cpus = [s["cpuPct"] for s in samples if s["cpuPct"] is not None]
        ios = [s["dataIoPct"] for s in samples if s["dataIoPct"] is not None]
        return {
            "samples": samples,
            "activeConnections": conns,
            "maxCpuPct": max(cpus, default=None),
            "maxDataIoPct": max(ios, default=None),
            "avgCpuPct": round(sum(cpus) / len(cpus), 2) if cpus else None,
            "poolCheckoutWaits": self.pool.checkout_waits,
        }


class _CursorCtx:
    def __init__(self, repo: SqlOrderRepository, m: RequestMetrics | None) -> None:
        self.repo = repo
        self.m = m
        self.conn: pyodbc.Connection | None = None
        self.cur: pyodbc.Cursor | None = None
        self.broken = False

    def __enter__(self) -> pyodbc.Cursor:
        t0 = time.perf_counter()
        self.conn = self.repo.pool.acquire()
        if self.m is not None:
            self.m.sql_pool_checkout_ms += (time.perf_counter() - t0) * 1000
        self.cur = self.conn.cursor()
        return self.cur

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None and issubclass(exc_type, pyodbc.Error):
            self.broken = True
        try:
            if self.cur is not None:
                self.cur.close()
        except Exception:
            self.broken = True
        if self.conn is not None:
            self.repo.pool.release(self.conn, broken=self.broken)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
