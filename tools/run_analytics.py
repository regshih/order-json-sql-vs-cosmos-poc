"""Build and query the Fabric analytics model, and measure data freshness.

Three jobs, all against the Fabric Warehouse / SQL analytics endpoint:

    --build        run fabric/warehouse/analytics_model.sql (star schema + loads)
    --query        run fabric/warehouse/analytics_queries.sql, timing each
    --freshness    write to the operational store, push, and time how long the
                   change takes to become visible in Fabric

Results land in results/fabric-analytics/ and feed docs/FABRIC_ANALYTICS.md.

    python tools/run_analytics.py --build --query
    python tools/run_analytics.py --freshness --backend sql
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyodbc

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FABRIC_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
STATE = Path("artifacts/fabric-environment.json")


def fabric_conn(endpoint: str, database: str) -> pyodbc.Connection:
    from azure.identity import DefaultAzureCredential

    tok = DefaultAzureCredential().get_token(FABRIC_SCOPE).token.encode("utf-16-le")
    attrs = {1256: struct.pack(f"<I{len(tok)}s", len(tok), tok)}
    cs = (
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server={endpoint},1433;Database={database};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=90;"
    )
    return pyodbc.connect(cs, attrs_before=attrs, autocommit=True)


def split_named(sql_text: str) -> list[tuple[str, str]]:
    """Split a .sql file into (name, statement) using '-- name: x' markers."""
    out: list[tuple[str, str]] = []
    current_name: str | None = None
    buf: list[str] = []
    for line in sql_text.splitlines():
        m = re.match(r"^\s*--\s*name:\s*(\S+)", line)
        if m:
            if current_name and buf:
                out.append((current_name, "\n".join(buf).strip()))
            current_name, buf = m.group(1), []
            continue
        buf.append(line)
    if current_name and buf:
        out.append((current_name, "\n".join(buf).strip()))
    return [(n, s) for n, s in out if s and not s.startswith("/*") or s]


def split_batches(sql_text: str) -> list[str]:
    """Split on ';' at statement level.

    Block comments are stripped FIRST: a /* ... */ banner spanning several lines
    otherwise gets cut in half by the statement splitter and the fragment is
    sent to the server, which rejects it with "Missing end comment mark".
    """
    sql_text = re.sub(r"/\*.*?\*/", "", sql_text, flags=re.DOTALL)
    parts, buf, depth = [], [], 0
    for line in sql_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        buf.append(line)
        depth += line.count("(") - line.count(")")
        if stripped.endswith(";") and depth <= 0:
            s = "\n".join(buf).strip().rstrip(";").strip()
            s = re.sub(r"/\*.*?\*/", "", s, flags=re.DOTALL).strip()
            if s:
                parts.append(s)
            buf, depth = [], 0
    tail = "\n".join(buf).strip()
    tail = re.sub(r"/\*.*?\*/", "", tail, flags=re.DOTALL).strip()
    if tail:
        parts.append(tail)
    return parts


def populate_dim_date(conn: pyodbc.Connection, start: str = "2024-01-01",
                      days: int = 1461) -> dict[str, Any]:
    """Fill DimDate from the client.

    Fabric Warehouse has no supported set-based row generator (`sys.all_objects`
    is rejected in distributed processing mode), so the calendar is built here
    and inserted in batches.
    """
    from datetime import date, timedelta

    d0 = date.fromisoformat(start)
    rows = []
    for i in range(days):
        d = d0 + timedelta(days=i)
        rows.append((
            d.year * 10000 + d.month * 100 + d.day, d, d.year,
            (d.month - 1) // 3 + 1, d.month, d.strftime("%B"), d.day,
            d.isoweekday() % 7 + 1, f"{d.year}-{d.month:02d}",
            1 if d.isoweekday() >= 6 else 0,
        ))
    cur = conn.cursor()
    cur.execute("DELETE FROM dbo.DimDate")
    t0 = time.perf_counter()
    batch = 200
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        values = ",".join(
            "(" + ",".join([
                str(r[0]), f"'{r[1].isoformat()}'", str(r[2]), str(r[3]), str(r[4]),
                f"'{r[5]}'", str(r[6]), str(r[7]), f"'{r[8]}'", str(r[9]),
            ]) + ")" for r in chunk
        )
        cur.execute(f"INSERT INTO dbo.DimDate VALUES {values}")
    ms = (time.perf_counter() - t0) * 1000
    n = cur.execute("SELECT COUNT(*) FROM dbo.DimDate").fetchval()
    print(f"  [--] DimDate populated client-side: {n:,} rows in {ms:,.0f} ms")
    return {"statement": "populate DimDate (client-side)", "ms": round(ms, 1),
            "rows": n, "ok": n == days}


def build_model(conn: pyodbc.Connection, path: Path) -> list[dict[str, Any]]:
    results = []
    cur = conn.cursor()
    for i, stmt in enumerate(split_batches(path.read_text(encoding="utf-8")), 1):
        label = " ".join(stmt.split()[:6])
        t0 = time.perf_counter()
        try:
            cur.execute(stmt)
            while cur.nextset():
                pass
            ms = (time.perf_counter() - t0) * 1000
            results.append({"n": i, "statement": label, "ms": round(ms, 1), "ok": True})
            print(f"  [{i:02d}] {label:<58} {ms:8.1f} ms")
        except Exception as exc:
            ms = (time.perf_counter() - t0) * 1000
            results.append({"n": i, "statement": label, "ms": round(ms, 1), "ok": False,
                            "error": str(exc)[:400]})
            print(f"  [{i:02d}] {label:<58} FAILED: {str(exc)[:180]}")
    return results


def run_queries(conn: pyodbc.Connection, path: Path, max_rows: int = 25) -> list[dict[str, Any]]:
    results = []
    cur = conn.cursor()
    for name, stmt in split_named(path.read_text(encoding="utf-8")):
        stmt = stmt.rstrip().rstrip(";")
        t0 = time.perf_counter()
        try:
            cur.execute(stmt)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchall() if cur.description else []
            ms = (time.perf_counter() - t0) * 1000
            data = [
                {c: (float(v) if isinstance(v, __import__("decimal").Decimal) else
                     v.isoformat() if hasattr(v, "isoformat") else v)
                 for c, v in zip(cols, row)}
                for row in rows[:max_rows]
            ]
            results.append({"name": name, "ms": round(ms, 1), "rowCount": len(rows),
                            "columns": cols, "rows": data, "ok": True})
            print(f"  {name:<38} {ms:8.1f} ms  rows={len(rows)}")
        except Exception as exc:
            ms = (time.perf_counter() - t0) * 1000
            results.append({"name": name, "ms": round(ms, 1), "ok": False, "error": str(exc)[:400]})
            print(f"  {name:<38} FAILED: {str(exc)[:180]}")
    return results


def measure_freshness(backend: str, endpoint: str, database: str,
                      poll_seconds: float, timeout: float) -> dict[str, Any]:
    """Write to the operational store, push, then poll Fabric until it appears.

    Measures three separate intervals so the result is diagnostic rather than a
    single opaque number:
        writeToPush   - operational write until the extractor uploads Parquet
        pushToVisible - upload until the row is queryable in Fabric
        endToEnd      - operational write until queryable
    """
    import subprocess

    if backend == "sql":
        from app.repositories.sql_repository import SqlOrderRepository

        repo = SqlOrderRepository()
    else:
        from app.repositories.cosmos_repository import CosmosOrderRepository

        repo = CosmosOrderRepository()

    pool = repo.list_order_ids(20)
    if not pool:
        raise SystemExit("no orders available")
    target = pool[0]
    oid = target["orderId"]
    marker = f"FRESHNESS-{int(time.time())}"

    t_write = time.perf_counter()
    res = repo.update_order_header(oid, {"Status": "Title Review", "Project": marker})
    write_done = time.perf_counter()
    print(f"  wrote Project={marker} to {oid} ({backend}) in {(write_done - t_write) * 1000:.0f} ms")

    t_push = time.perf_counter()
    cmd = [sys.executable, "-m", "ingestion.fabric.push_to_onelake",
           "--backend", backend, "--mode", "incremental"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    push_done = time.perf_counter()
    print(f"  push completed in {push_done - t_push:.1f}s (rc={proc.returncode})")
    if proc.returncode != 0:
        print(f"    {proc.stderr[-500:]}")

    table = "mir_sql_orders.dbo.Orders" if backend == "sql" else "mir_cosmos_orders.dbo.CosmosOrderItems"
    if backend == "sql":
        query = f"SELECT COUNT(*) FROM {table} WHERE OrderId = ? AND Project = ?"
        params = (oid, marker)
    else:
        # A plain substring test, not JSON_VALUE. Fabric Warehouse does not
        # guarantee predicate evaluation order, so an ISJSON() guard in the same
        # WHERE clause does not stop JSON_VALUE being applied to a non-JSON row,
        # and the whole query fails with "JSON text is not properly formatted".
        query = (f"SELECT COUNT(*) FROM {table} "
                 f"WHERE orderId = ? AND docType = 'orderHeader' "
                 f"AND CHARINDEX(?, ISNULL(searchJson, '')) > 0")
        params = (oid, marker)

    conn = fabric_conn(endpoint, database)
    cur = conn.cursor()
    visible_at = None
    polls = 0
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        polls += 1
        try:
            cur.execute(query, *params)
            if (cur.fetchval() or 0) > 0:
                visible_at = time.perf_counter()
                break
        except Exception as exc:
            if polls == 1:
                print(f"    poll error (will retry): {str(exc)[:180]}")
        time.sleep(poll_seconds)

    repo.close()
    out = {
        "backend": backend,
        "orderId": oid,
        "marker": marker,
        "writeMs": round((write_done - t_write) * 1000, 1),
        "pushSec": round(push_done - t_push, 2),
        "pushReturnCode": proc.returncode,
        "polls": polls,
        "pollIntervalSec": poll_seconds,
        "timeoutSec": timeout,
        "visible": visible_at is not None,
    }
    if visible_at is not None:
        out["pushToVisibleSec"] = round(visible_at - push_done, 2)
        out["endToEndSec"] = round(visible_at - t_write, 2)
        print(f"  VISIBLE in Fabric after {out['endToEndSec']}s end-to-end "
              f"({out['pushToVisibleSec']}s after the push)")
    else:
        out["note"] = f"not visible within {timeout}s"
        print(f"  NOT VISIBLE within {timeout}s")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--query", action="store_true")
    ap.add_argument("--freshness", action="store_true")
    ap.add_argument("--backend", choices=["sql", "cosmos", "both"], default="both")
    ap.add_argument("--endpoint")
    ap.add_argument("--database")
    ap.add_argument("--poll-seconds", type=float, default=5)
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--out", default="results/fabric-analytics")
    args = ap.parse_args()

    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    endpoint = args.endpoint or (st.get("warehouseProperties", {}) or {}).get("connectionString")
    database = args.database or st.get("warehouseName")
    if not endpoint or not database:
        raise SystemExit("need --endpoint and --database (or a provisioned warehouse in state)")
    print(f"Fabric endpoint: {endpoint}\ndatabase: {database}")

    report: dict[str, Any] = {"startedUtc": datetime.now(timezone.utc).isoformat(),
                              "endpoint": endpoint, "database": database}

    if args.build:
        print("\nbuilding analytics model ...")
        conn = fabric_conn(endpoint, database)
        report["build"] = build_model(conn, Path("fabric/warehouse/analytics_model.sql"))
        report["build"].append(populate_dim_date(conn))
        conn.close()

    if args.query:
        print("\nrunning analytics queries ...")
        conn = fabric_conn(endpoint, database)
        report["queries"] = run_queries(conn, Path("fabric/warehouse/analytics_queries.sql"))
        conn.close()

    if args.freshness:
        backends = ["sql", "cosmos"] if args.backend == "both" else [args.backend]
        report["freshness"] = []
        for b in backends:
            print(f"\nmeasuring {b} -> Fabric freshness ...")
            report["freshness"].append(
                measure_freshness(b, endpoint, database, args.poll_seconds, args.timeout))

    report["completedUtc"] = datetime.now(timezone.utc).isoformat()
    d = Path(args.out)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"analytics-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.json"
    p.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\n-> {p}")


if __name__ == "__main__":
    main()
