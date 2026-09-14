"""Push operational data into Fabric via Open Mirroring landing zones.

Runs INSIDE the POC VNet. Reads the operational store over its private endpoint
and writes Parquet outbound to a Fabric Open Mirroring landing zone; Fabric's
managed replicator merges each file into a Delta table with insert/update/delete
semantics. No inbound path to the databases is required, which is what makes
this work under the tenant's private-only networking policy.

Landing-zone contract (Fabric Open Mirroring):
    <LandingZone>/<TableName>/_metadata.json      key columns declaration
    <LandingZone>/<TableName>/00000000000000000001.parquet
    ...                     /00000000000000000002.parquet   monotonically increasing

Every row carries ``__rowMarker__`` (0=insert, 1=update, 2=delete, 4=upsert) and
``_extractedUtc``, which is the clock the freshness test reads.

    python -m ingestion.fabric.push_to_onelake --backend sql --mode full
    python -m ingestion.fabric.push_to_onelake --backend sql --mode incremental
    python -m ingestion.fabric.push_to_onelake --backend cosmos --mode full
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

ONELAKE_DFS = "https://onelake.dfs.fabric.microsoft.com"
STATE_FILE = Path("artifacts/fabric-environment.json")
WATERMARK_FILE = Path("artifacts/fabric-push-watermarks.json")

# Relational projection mirrored from Azure SQL.
#
# OrderJsonBlocks is included ON PURPOSE. Its JsonPayload is nvarchar(max) with
# values up to ~760 KB, and whether multi-hundred-KB strings survive the trip to
# OneLake is one of the questions this POC has to answer with evidence rather
# than assumption. See docs/FABRIC_ANALYTICS.md.
SQL_TABLES: dict[str, dict[str, Any]] = {
    "Customers":      {"keys": ["CustomerId"],              "watermark": None},
    "Orders":         {"keys": ["OrderId"],                 "watermark": "ModifiedDate"},
    "OrderVersions":  {"keys": ["OrderId", "Version"],      "watermark": "CreatedDate"},
    "Properties":     {"keys": ["PropertyId"],              "watermark": None},
    "Parties":        {"keys": ["PartyId"],                 "watermark": None},
    "OrderParties":   {"keys": ["OrderId", "PartyId", "Role", "Sequence"], "watermark": None},
    "Loans":          {"keys": ["LoanId"],                  "watermark": None},
    "OrderJsonBlocks": {"keys": ["OrderId", "OrderVersion", "BlockType", "BlockSubType", "Sequence"],
                        "watermark": "LastModified"},
}

# Cosmos is a single container, so the mirror is one table of items. The nested
# payload is carried as a JSON string column - Delta has no place for an
# arbitrary variant tree, and analytics reads it with JSON functions.
COSMOS_TABLE = "CosmosOrderItems"
COSMOS_KEYS = ["id"]


def onelake_client(workspace_id: str):
    from azure.identity import DefaultAzureCredential
    from azure.storage.filedatalake import DataLakeServiceClient

    svc = DataLakeServiceClient(account_url=ONELAKE_DFS, credential=DefaultAzureCredential())
    return svc.get_file_system_client(workspace_id)


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        raise SystemExit(f"{STATE_FILE} not found - run fabric/setup_mirroring.py --open-mirroring")
    st = json.loads(STATE_FILE.read_text())
    if "mirrors" not in st:
        raise SystemExit("no Open Mirroring landing zones in state")
    return st


def load_watermarks() -> dict[str, Any]:
    return json.loads(WATERMARK_FILE.read_text()) if WATERMARK_FILE.exists() else {}


def save_watermarks(w: dict[str, Any]) -> None:
    WATERMARK_FILE.parent.mkdir(parents=True, exist_ok=True)
    WATERMARK_FILE.write_text(json.dumps(w, indent=2, default=str), encoding="utf-8")


def write_metadata(fs, lz: str, table: str, keys: list[str]) -> None:
    """Declare the table's key columns. Written once per table."""
    path = f"{lz}/{table}/_metadata.json"
    fc = fs.get_file_client(path)
    body = json.dumps({"keyColumns": keys}).encode()
    fc.upload_data(body, overwrite=True)


def next_sequence(fs, lz: str, table: str, watermarks: dict[str, Any]) -> int:
    """Next landing-zone file number.

    Fabric Open Mirroring requires monotonically increasing 20-digit file names
    and IGNORES a sequence it has already processed.

    The listing alone is not a safe source of truth: once the replicator has
    consumed a file it removes it from the landing zone (leaving only
    ``_metadata.json`` and ``_FilesReadyToDelete``), so a listing-derived counter
    resets to 1 and the next upload is silently ignored. That is why an
    incremental push appeared to succeed while the change never became visible
    in Fabric.

    So: take the maximum of what is still on disk and what we last used, which
    is persisted alongside the extraction watermarks.
    """
    highest = 0
    try:
        for p in fs.get_paths(path=f"{lz}/{table}", recursive=False):
            name = p.name.rsplit("/", 1)[-1]
            if name.endswith(".parquet") and name[: -len(".parquet")].isdigit():
                highest = max(highest, int(name[: -len(".parquet")]))
    except Exception:
        pass
    seen = watermarks.setdefault("_sequences", {})
    highest = max(highest, int(seen.get(f"{lz}/{table}", 0)))
    return highest + 1


def record_sequence(watermarks: dict[str, Any], lz: str, table: str, seq: int) -> None:
    """Remember the highest sequence used, so it survives the replicator
    deleting the file from the landing zone."""
    seen = watermarks.setdefault("_sequences", {})
    key = f"{lz}/{table}"
    seen[key] = max(int(seen.get(key, 0)), int(seq))


def upload_parquet(fs, lz: str, table: str, df: pd.DataFrame, seq: int) -> dict[str, Any]:
    """Write one Parquet file into the landing zone."""
    buf = io.BytesIO()
    tbl = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(tbl, buf, compression="snappy")
    data = buf.getvalue()
    name = f"{seq:020d}.parquet"
    t0 = time.perf_counter()
    fs.get_file_client(f"{lz}/{table}/{name}").upload_data(data, overwrite=True)
    return {
        "table": table, "file": name, "rows": len(df),
        "bytes": len(data),
        "uploadMs": round((time.perf_counter() - t0) * 1000, 1),
    }


def _mark(df: pd.DataFrame, marker: int = 4) -> pd.DataFrame:
    """Add the Open Mirroring row marker and the freshness clock.

    marker 4 = upsert, which is the right semantic for a watermark-based pull:
    we cannot tell an insert from an update without change tracking, and an
    upsert is correct either way.
    """
    df = df.copy()
    df["__rowMarker__"] = marker
    df["_extractedUtc"] = datetime.now(timezone.utc).replace(tzinfo=None)
    return df


def push_sql(mode: str, lz: str, fs, tables: list[str], batch_rows: int) -> dict[str, Any]:
    from app.repositories.sql_repository import SqlOrderRepository

    repo = SqlOrderRepository()
    watermarks = load_watermarks()
    wm_key = "sql"
    wm = watermarks.setdefault(wm_key, {})
    out: list[dict[str, Any]] = []

    conn = repo.pool.acquire()
    try:
        for table in tables:
            cfg = SQL_TABLES[table]
            where, params = "", []
            if mode == "incremental" and cfg["watermark"] and wm.get(table):
                where = f" WHERE {cfg['watermark']} > ?"
                params = [wm[table]]

            t0 = time.perf_counter()
            df = pd.read_sql(f"SELECT * FROM ord.{table}{where}", conn, params=params or None)
            query_ms = (time.perf_counter() - t0) * 1000

            if df.empty:
                out.append({"table": table, "rows": 0, "skipped": "no changes",
                            "queryMs": round(query_ms, 1)})
                continue

            # uniqueidentifier arrives as a string; keep the canonical lowercase
            # form so Delta joins line up with the Cosmos mirror.
            for col in df.columns:
                if df[col].dtype == object and col.endswith(("Id", "OrderId")):
                    df[col] = df[col].astype(str).str.lower()

            payload_stats = None
            if "JsonPayload" in df.columns:
                lens = df["JsonPayload"].fillna("").str.len()
                payload_stats = {
                    "maxChars": int(lens.max()), "meanChars": int(lens.mean()),
                    "over1MiB": int((lens > 1024 * 1024).sum()),
                }

            write_metadata(fs, lz, table, cfg["keys"])
            seq = next_sequence(fs, lz, table, watermarks)
            # Chunk large tables so no single Parquet file is unwieldy.
            for i in range(0, len(df), batch_rows):
                chunk = _mark(df.iloc[i:i + batch_rows])
                r = upload_parquet(fs, lz, table, chunk, seq)
                r["queryMs"] = round(query_ms, 1)
                r["sequence"] = seq
                if payload_stats:
                    r["jsonPayloadStats"] = payload_stats
                out.append(r)
                record_sequence(watermarks, lz, table, seq)
                seq += 1

            if cfg["watermark"] and cfg["watermark"] in df.columns:
                wm[table] = str(df[cfg["watermark"]].max())
    finally:
        repo.pool.release(conn)
        repo.close()

    save_watermarks(watermarks)
    return {"backend": "sql", "mode": mode, "files": out}


def push_cosmos(mode: str, lz: str, fs, batch_rows: int) -> dict[str, Any]:
    from app.repositories.cosmos_repository import CosmosOrderRepository

    repo = CosmosOrderRepository()
    watermarks = load_watermarks()
    wm = watermarks.setdefault("cosmos", {})
    out: list[dict[str, Any]] = []

    # Project each item to a flat analytics row. The nested business payload
    # becomes a JSON string; the routing/search fields become real columns.
    query = (
        "SELECT c.id, c.docType, c.customerId, c.orderId, c.orderVersion, c.blockType, "
        "c.blockSubType, c.sequence, c.chunkIndex, c.chunkCount, c.payloadBytes, "
        "c.search, c.summary, c.modifiedUtc, c._ts FROM c"
    )
    if mode == "incremental" and wm.get("ts"):
        query += f" WHERE c._ts > {int(wm['ts'])}"

    t0 = time.perf_counter()
    rows: list[dict[str, Any]] = []
    ru = 0.0
    it = repo.container.query_items(query=query, enable_cross_partition_query=True, max_item_count=1000)
    max_ts = int(wm.get("ts", 0))
    for item in it:
        max_ts = max(max_ts, int(item.get("_ts") or 0))
        rows.append({
            "id": item["id"],
            "docType": item.get("docType"),
            "customerId": item.get("customerId"),
            "orderId": (item.get("orderId") or "").lower(),
            "orderVersion": item.get("orderVersion"),
            "blockType": item.get("blockType"),
            "blockSubType": item.get("blockSubType"),
            "sequence": item.get("sequence"),
            "chunkIndex": item.get("chunkIndex"),
            "chunkCount": item.get("chunkCount"),
            "payloadBytes": item.get("payloadBytes"),
            "searchJson": json.dumps(item.get("search")) if item.get("search") else None,
            "summaryJson": json.dumps(item.get("summary")) if item.get("summary") else None,
            "modifiedUtc": item.get("modifiedUtc"),
            "sourceTs": item.get("_ts"),
        })
    ru += float(repo.container.client_connection.last_response_headers.get("x-ms-request-charge", 0))
    query_ms = (time.perf_counter() - t0) * 1000

    if not rows:
        repo.close()
        return {"backend": "cosmos", "mode": mode, "files": [],
                "note": "no changes", "queryMs": round(query_ms, 1), "readRu": round(ru, 2)}

    df = pd.DataFrame(rows)
    write_metadata(fs, lz, COSMOS_TABLE, COSMOS_KEYS)
    seq = next_sequence(fs, lz, COSMOS_TABLE, watermarks)
    for i in range(0, len(df), batch_rows):
        r = upload_parquet(fs, lz, COSMOS_TABLE, _mark(df.iloc[i:i + batch_rows]), seq)
        r["queryMs"] = round(query_ms, 1)
        r["readRu"] = round(ru, 2)
        r["sequence"] = seq
        out.append(r)
        record_sequence(watermarks, lz, COSMOS_TABLE, seq)
        seq += 1

    wm["ts"] = max_ts
    save_watermarks(watermarks)
    repo.close()
    return {"backend": "cosmos", "mode": mode, "files": out,
            "itemsRead": len(rows), "readRu": round(ru, 2), "queryMs": round(query_ms, 1)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backend", choices=["sql", "cosmos", "both"], required=True)
    ap.add_argument("--mode", choices=["full", "incremental"], default="full")
    ap.add_argument("--tables", default=",".join(SQL_TABLES),
                    help="SQL tables to push (comma separated)")
    ap.add_argument("--batch-rows", type=int, default=20_000)
    ap.add_argument("--out", default="results/fabric-analytics")
    args = ap.parse_args()

    st = load_state()
    wsid = st["workspaceId"]
    fs = onelake_client(wsid)

    backends = ["sql", "cosmos"] if args.backend == "both" else [args.backend]
    report: dict[str, Any] = {
        "startedUtc": datetime.now(timezone.utc).isoformat(),
        "workspaceId": wsid,
        "mode": args.mode,
        "pushes": [],
    }

    for b in backends:
        lz = st["mirrors"][b]["landingZoneDfsPath"]
        print(f"[{b}] mode={args.mode} landing zone={lz}")
        t0 = time.perf_counter()
        if b == "sql":
            tables = [t for t in args.tables.split(",") if t in SQL_TABLES]
            res = push_sql(args.mode, lz, fs, tables, args.batch_rows)
        else:
            res = push_cosmos(args.mode, lz, fs, args.batch_rows)
        res["elapsedSec"] = round(time.perf_counter() - t0, 2)
        res["landingZone"] = lz
        report["pushes"].append(res)

        total_rows = sum(f.get("rows", 0) for f in res["files"])
        total_bytes = sum(f.get("bytes", 0) for f in res["files"])
        print(f"  {len(res['files'])} files, {total_rows:,} rows, {total_bytes:,} parquet bytes "
              f"in {res['elapsedSec']}s")
        for f in res["files"]:
            if f.get("jsonPayloadStats"):
                s = f["jsonPayloadStats"]
                print(f"    {f['table']}: JsonPayload max={s['maxChars']:,} chars "
                      f"mean={s['meanChars']:,} over1MiB={s['over1MiB']}")
            elif f.get("skipped"):
                print(f"    {f['table']}: {f['skipped']}")

    report["completedUtc"] = datetime.now(timezone.utc).isoformat()
    d = Path(args.out)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"push-{args.backend}-{args.mode}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.json"
    p.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"-> {p}")
    print("PUSH_OK")


if __name__ == "__main__":
    main()
