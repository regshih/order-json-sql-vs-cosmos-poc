"""Reconcile the operational stores against each other and against Fabric.

Three independent checks, each of which can fail on its own:

  1. SQL  vs Cosmos   - do the two operational stores hold the same orders,
                        the same business keys, and the same aggregates?
  2. SQL  vs Fabric   - did every mirrored row arrive, and did the
                        multi-hundred-KB JSON blocks survive intact?
  3. Cosmos vs Fabric - same, for the Cosmos mirror.

Run from inside the POC VNet (SQL and Cosmos are private-endpoint only).

    python tools/reconciliation.py --operational
    python tools/reconciliation.py --fabric
    python tools/reconciliation.py --all
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

STATE = Path("artifacts/fabric-environment.json")
FABRIC_SCOPE = "https://analysis.windows.net/powerbi/api/.default"


def fabric_conn(endpoint: str, database: str):
    import pyodbc
    from azure.identity import DefaultAzureCredential

    tok = DefaultAzureCredential().get_token(FABRIC_SCOPE).token.encode("utf-16-le")
    return pyodbc.connect(
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server={endpoint},1433;Database={database};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=90;",
        attrs_before={1256: struct.pack(f"<I{len(tok)}s", len(tok), tok)},
        autocommit=True,
    )


# --------------------------------------------------------------------------
# 1. SQL vs Cosmos
# --------------------------------------------------------------------------


def reconcile_operational(limit: int = 20_000) -> dict[str, Any]:
    from app.repositories.cosmos_repository import CosmosOrderRepository
    from app.repositories.sql_repository import SqlOrderRepository
    from app.telemetry.metrics import RequestMetrics, measure

    sql = SqlOrderRepository()
    cosmos = CosmosOrderRepository()

    t0 = time.perf_counter()
    sql_rows = {r["orderId"]: r for r in sql.list_order_ids(limit)}
    cos_rows = {r["orderId"]: r for r in cosmos.list_order_ids(limit)}

    only_sql = sorted(set(sql_rows) - set(cos_rows))
    only_cos = sorted(set(cos_rows) - set(sql_rows))
    both = sorted(set(sql_rows) & set(cos_rows))

    version_mismatch = [
        {"orderId": o, "sql": sql_rows[o]["orderVersion"], "cosmos": cos_rows[o]["orderVersion"]}
        for o in both if sql_rows[o]["orderVersion"] != cos_rows[o]["orderVersion"]
    ]
    # payloadBytes is a storage statistic (SQL sums block rows, Cosmos sums
    # items), so a small divergence is expected and is reported, not asserted.
    byte_delta = [
        {"orderId": o, "sql": sql_rows[o]["payloadBytes"], "cosmos": cos_rows[o]["payloadBytes"]}
        for o in both
        if sql_rows[o]["payloadBytes"] and cos_rows[o]["payloadBytes"]
        and abs(sql_rows[o]["payloadBytes"] - cos_rows[o]["payloadBytes"]) > 1024
    ]

    # Deep check on a sample: full summary equality.
    sample = both[:: max(1, len(both) // 25)][:25]
    summary_mismatch = []
    for oid in sample:
        with measure("recon", "recon", "summary") as ma:
            a = sql.get_summary(oid, ma)
        with measure("recon", "recon", "summary") as mb:
            b = cosmos.get_summary(oid, mb)
        if a and b:
            a.pop("payloadBytes", None)
            b.pop("payloadBytes", None)
            if a != b:
                diff = {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)}
                summary_mismatch.append({"orderId": oid, "differences": diff})

    sql_totals = sum(r["payloadBytes"] or 0 for r in sql_rows.values())
    cos_totals = sum(r["payloadBytes"] or 0 for r in cos_rows.values())

    sql.close()
    cosmos.close()

    return {
        "check": "sql-vs-cosmos",
        "elapsedSec": round(time.perf_counter() - t0, 2),
        "sqlOrders": len(sql_rows),
        "cosmosOrders": len(cos_rows),
        "inBoth": len(both),
        "onlyInSql": only_sql[:20],
        "onlyInSqlCount": len(only_sql),
        "onlyInCosmos": only_cos[:20],
        "onlyInCosmosCount": len(only_cos),
        "versionMismatches": version_mismatch[:20],
        "versionMismatchCount": len(version_mismatch),
        "payloadByteDeltas": byte_delta[:10],
        "payloadByteDeltaCount": len(byte_delta),
        "sqlTotalPayloadBytes": sql_totals,
        "cosmosTotalPayloadBytes": cos_totals,
        "summarySampleSize": len(sample),
        "summaryMismatches": summary_mismatch,
        "passed": (not only_sql and not only_cos and not version_mismatch
                   and not summary_mismatch),
    }


# --------------------------------------------------------------------------
# 2 & 3. Operational vs Fabric
# --------------------------------------------------------------------------


def reconcile_fabric(endpoint: str, database: str) -> dict[str, Any]:
    from app.repositories.cosmos_repository import CosmosOrderRepository
    from app.repositories.sql_repository import SqlOrderRepository

    out: dict[str, Any] = {"check": "operational-vs-fabric"}
    conn = fabric_conn(endpoint, database)
    cur = conn.cursor()

    def scalar(sql: str, default: Any = None) -> Any:
        try:
            cur.execute(sql)
            return cur.fetchval()
        except Exception as exc:
            out.setdefault("queryErrors", []).append({"sql": sql[:120], "error": str(exc)[:300]})
            return default

    # -- SQL side -------------------------------------------------------
    sql = SqlOrderRepository()
    c = sql.pool.acquire()
    try:
        cur2 = c.cursor()
        src_orders = cur2.execute("SELECT COUNT(*) FROM ord.Orders").fetchval()
        src_blocks = cur2.execute("SELECT COUNT(*) FROM ord.OrderJsonBlocks").fetchval()
        src_max_block = cur2.execute("SELECT MAX(PayloadBytes) FROM ord.OrderJsonBlocks").fetchval()
        src_loans = cur2.execute("SELECT COUNT(*) FROM ord.Loans").fetchval()
        src_loan_sum = cur2.execute("SELECT SUM(LoanAmount) FROM ord.Loans").fetchval()
        src_max_loan_sum = cur2.execute("SELECT SUM(MaxLoanAmount) FROM ord.Orders").fetchval()
    finally:
        sql.pool.release(c)
        sql.close()

    fab_orders = scalar("SELECT COUNT(*) FROM mir_sql_orders.dbo.Orders", 0)
    fab_blocks = scalar("SELECT COUNT(*) FROM mir_sql_orders.dbo.OrderJsonBlocks", 0)
    fab_max_block_chars = scalar("SELECT MAX(LEN(JsonPayload)) FROM mir_sql_orders.dbo.OrderJsonBlocks", 0)
    fab_invalid_json = scalar(
        "SELECT SUM(CASE WHEN ISJSON(JsonPayload) = 0 THEN 1 ELSE 0 END) "
        "FROM mir_sql_orders.dbo.OrderJsonBlocks", 0)
    fab_truncated = scalar(
        "SELECT SUM(CASE WHEN LEN(JsonPayload) <> PayloadBytes THEN 1 ELSE 0 END) "
        "FROM mir_sql_orders.dbo.OrderJsonBlocks", 0)
    fab_loans = scalar("SELECT COUNT(*) FROM mir_sql_orders.dbo.Loans", 0)
    fab_loan_sum = scalar("SELECT SUM(LoanAmount) FROM mir_sql_orders.dbo.Loans", 0)

    out["sqlToFabric"] = {
        "sourceOrders": src_orders, "fabricOrders": fab_orders,
        "orderDelta": (fab_orders or 0) - (src_orders or 0),
        "sourceBlocks": src_blocks, "fabricBlocks": fab_blocks,
        "blockDelta": (fab_blocks or 0) - (src_blocks or 0),
        "sourceMaxBlockBytes": src_max_block,
        "fabricMaxBlockChars": fab_max_block_chars,
        "invalidJsonRowsInFabric": fab_invalid_json,
        "lengthMismatchRows": fab_truncated,
        "sourceLoans": src_loans, "fabricLoans": fab_loans,
        "sourceLoanSum": float(src_loan_sum) if src_loan_sum is not None else None,
        "fabricLoanSum": float(fab_loan_sum) if fab_loan_sum is not None else None,
        "sourceMaxLoanSum": float(src_max_loan_sum) if src_max_loan_sum is not None else None,
        "rowCountsMatch": (fab_orders == src_orders and fab_blocks == src_blocks),
        "jsonIntact": (fab_invalid_json == 0 and fab_truncated == 0),
    }

    # -- Cosmos side ----------------------------------------------------
    cosmos = CosmosOrderRepository()
    items = list(cosmos.container.query_items(
        query="SELECT VALUE COUNT(1) FROM c", enable_cross_partition_query=True))
    src_items = items[0] if items else 0
    headers = list(cosmos.container.query_items(
        query="SELECT VALUE COUNT(1) FROM c WHERE c.docType = 'orderHeader'",
        enable_cross_partition_query=True))
    src_headers = headers[0] if headers else 0
    cosmos.close()

    fab_items = scalar("SELECT COUNT(*) FROM mir_cosmos_orders.dbo.CosmosOrderItems", 0)
    fab_headers = scalar(
        "SELECT COUNT(*) FROM mir_cosmos_orders.dbo.CosmosOrderItems WHERE docType = 'orderHeader'", 0)

    out["cosmosToFabric"] = {
        "sourceItems": src_items, "fabricItems": fab_items,
        "itemDelta": (fab_items or 0) - (src_items or 0),
        "sourceHeaders": src_headers, "fabricHeaders": fab_headers,
        "headerDelta": (fab_headers or 0) - (src_headers or 0),
        "rowCountsMatch": (fab_items == src_items and fab_headers == src_headers),
    }

    # -- Fact-level cross-check ------------------------------------------
    out["factReconciliation"] = {
        "factOrderSql": scalar("SELECT COUNT(*) FROM dbo.FactOrder WHERE SourceBackend = 'sql'", 0),
        "factOrderCosmos": scalar("SELECT COUNT(*) FROM dbo.FactOrder WHERE SourceBackend = 'cosmos'", 0),
        "onlyInSqlFacts": scalar(
            "SELECT COUNT(*) FROM (SELECT OrderId FROM dbo.FactOrder WHERE SourceBackend='sql' "
            "EXCEPT SELECT OrderId FROM dbo.FactOrder WHERE SourceBackend='cosmos') x", 0),
        "onlyInCosmosFacts": scalar(
            "SELECT COUNT(*) FROM (SELECT OrderId FROM dbo.FactOrder WHERE SourceBackend='cosmos' "
            "EXCEPT SELECT OrderId FROM dbo.FactOrder WHERE SourceBackend='sql') y", 0),
        "statusDisagreements": scalar(
            "SELECT COUNT(*) FROM dbo.FactOrder s JOIN dbo.FactOrder c "
            "ON c.OrderId = s.OrderId AND c.SourceBackend='cosmos' "
            "WHERE s.SourceBackend='sql' AND s.StatusKey <> c.StatusKey", 0),
        "chargeRows": scalar("SELECT COUNT(*) FROM dbo.FactOrderCharge", 0),
    }

    conn.close()
    out["passed"] = bool(
        out["sqlToFabric"]["rowCountsMatch"] and out["sqlToFabric"]["jsonIntact"]
        and out["cosmosToFabric"]["rowCountsMatch"]
    )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--operational", action="store_true")
    ap.add_argument("--fabric", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--endpoint")
    ap.add_argument("--database")
    ap.add_argument("--out", default="results/reconciliation")
    args = ap.parse_args()

    do_op = args.operational or args.all
    do_fab = args.fabric or args.all
    if not (do_op or do_fab):
        ap.error("choose --operational, --fabric or --all")

    report: dict[str, Any] = {"generatedUtc": datetime.now(timezone.utc).isoformat(), "checks": []}

    if do_op:
        print("reconciling SQL vs Cosmos ...")
        r = reconcile_operational()
        report["checks"].append(r)
        print(f"  sql={r['sqlOrders']} cosmos={r['cosmosOrders']} both={r['inBoth']} "
              f"onlySql={r['onlyInSqlCount']} onlyCosmos={r['onlyInCosmosCount']} "
              f"versionMismatch={r['versionMismatchCount']} "
              f"summaryMismatch={len(r['summaryMismatches'])} -> "
              f"{'PASS' if r['passed'] else 'FAIL'}")

    if do_fab:
        st = json.loads(STATE.read_text()) if STATE.exists() else {}
        endpoint = args.endpoint or (st.get("warehouseProperties", {}) or {}).get("connectionString")
        database = args.database or st.get("warehouseName")
        if not endpoint or not database:
            raise SystemExit("need --endpoint and --database for the Fabric check")
        print("reconciling operational vs Fabric ...")
        r = reconcile_fabric(endpoint, database)
        report["checks"].append(r)
        s2f, c2f = r["sqlToFabric"], r["cosmosToFabric"]
        print(f"  SQL -> Fabric: orders {s2f['sourceOrders']} vs {s2f['fabricOrders']} "
              f"(delta {s2f['orderDelta']}), blocks {s2f['sourceBlocks']} vs {s2f['fabricBlocks']} "
              f"(delta {s2f['blockDelta']})")
        print(f"    JSON integrity: maxSourceBytes={s2f['sourceMaxBlockBytes']} "
              f"maxFabricChars={s2f['fabricMaxBlockChars']} "
              f"invalid={s2f['invalidJsonRowsInFabric']} lengthMismatch={s2f['lengthMismatchRows']}")
        print(f"  Cosmos -> Fabric: items {c2f['sourceItems']} vs {c2f['fabricItems']} "
              f"(delta {c2f['itemDelta']})")
        print(f"  -> {'PASS' if r['passed'] else 'FAIL'}")

    report["allPassed"] = all(c.get("passed") for c in report["checks"])
    d = Path(args.out)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"reconciliation-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.json"
    p.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\n-> {p}")
    print("RECONCILIATION_" + ("PASS" if report["allPassed"] else "FAIL"))


if __name__ == "__main__":
    main()
