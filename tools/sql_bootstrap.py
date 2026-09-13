"""Create the schema and grant the API managed identity access to Azure SQL.

Runs on a VM inside the POC VNet (Azure SQL has publicNetworkAccess disabled by
tenant policy, so the private endpoint is the only route).

Authenticates with an Entra access token supplied in SQL_ACCESS_TOKEN - the
token of the Entra SQL admin, minted on the operator's workstation. That avoids
putting any credential on the VM and avoids needing the VM identity to be admin.

    python tools/sql_bootstrap.py --server <fqdn> --database OrderDb \
        --grant-identity vm-orderjsonpoc-api
"""

from __future__ import annotations

import argparse
import os
import re
import struct
import sys
from pathlib import Path

import pyodbc

SQL_COPT_SS_ACCESS_TOKEN = 1256


def token_attr(raw_token: str) -> bytes:
    b = raw_token.encode("utf-16-le")
    return struct.pack(f"<I{len(b)}s", len(b), b)


def connect(server: str, database: str) -> pyodbc.Connection:
    cs = (
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server=tcp:{server},1433;Database={database};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60;"
    )
    tok = os.getenv("SQL_ACCESS_TOKEN")
    if tok:
        return pyodbc.connect(cs, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_attr(tok)}, autocommit=True)
    # Fall back to the ambient identity (managed identity on a VM).
    from azure.identity import DefaultAzureCredential

    t = DefaultAzureCredential().get_token("https://database.windows.net/.default").token
    return pyodbc.connect(cs, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_attr(t)}, autocommit=True)


def run_script(conn: pyodbc.Connection, path: Path) -> int:
    """Execute a .sql file, splitting on GO batch separators."""
    text = path.read_text(encoding="utf-8")
    batches = [b.strip() for b in re.split(r"^\s*GO\s*$", text, flags=re.MULTILINE | re.IGNORECASE)]
    cur = conn.cursor()
    n = 0
    for b in batches:
        if not b:
            continue
        cur.execute(b)
        while cur.nextset():
            pass
        n += 1
    return n


def grant_identity(conn: pyodbc.Connection, name: str) -> str:
    """Create a contained database user for a managed identity and grant the
    least privilege the API needs: read everything, write through ingestion."""
    safe = re.sub(r"[^A-Za-z0-9_\-]", "", name)
    if safe != name:
        raise ValueError(f"refusing suspicious identity name {name!r}")
    cur = conn.cursor()
    cur.execute(
        f"""
        IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = '{safe}')
            CREATE USER [{safe}] FROM EXTERNAL PROVIDER;
        """
    )
    for role in ("db_datareader", "db_datawriter"):
        cur.execute(f"ALTER ROLE {role} ADD MEMBER [{safe}];")
    # sys.dm_db_resource_stats needs VIEW DATABASE STATE for the telemetry endpoint.
    cur.execute(f"GRANT VIEW DATABASE STATE TO [{safe}];")
    return safe


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--server", required=True)
    ap.add_argument("--database", default="OrderDb")
    ap.add_argument("--schema-dir", default="sql/schema")
    ap.add_argument("--grant-identity", action="append", default=[])
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()

    conn = connect(args.server, args.database)
    cur = conn.cursor()
    print("connected:", cur.execute("SELECT DB_NAME(), SUSER_SNAME()").fetchone())

    if not args.verify_only:
        for f in sorted(Path(args.schema_dir).glob("*.sql")):
            n = run_script(conn, f)
            print(f"ran {f} ({n} batches)")
        for ident in args.grant_identity:
            print("granted:", grant_identity(conn, ident))

    tables = cur.execute(
        "SELECT s.name + '.' + t.name, "
        "(SELECT SUM(row_count) FROM sys.dm_db_partition_stats p "
        " WHERE p.object_id = t.object_id AND p.index_id IN (0,1)) "
        "FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id "
        "WHERE s.name = 'ord' ORDER BY t.name"
    ).fetchall()
    print(f"tables ({len(tables)}):")
    for name, rows in tables:
        print(f"  {name:28} rows={rows}")
    idx = cur.execute(
        "SELECT COUNT(*) FROM sys.indexes i JOIN sys.tables t ON t.object_id = i.object_id "
        "JOIN sys.schemas s ON s.schema_id = t.schema_id WHERE s.name='ord' AND i.type=2"
    ).fetchval()
    print(f"nonclustered indexes: {idx}")
    users = cur.execute(
        "SELECT name, type_desc FROM sys.database_principals "
        "WHERE type IN ('E','X') AND name NOT LIKE '##%'"
    ).fetchall()
    print("external users:", [u[0] for u in users])
    print("SQL_BOOTSTRAP_OK")


if __name__ == "__main__":
    main()
