"""Set up the SQL -> Fabric and Cosmos -> Fabric analytics integrations.

Two paths are attempted, in the order the brief asks for ("prefer the simplest
supported pattern"), and whichever works is what the POC measures:

  1. NATIVE MIRRORING (simplest supported pattern)
     Fabric Mirroring for Azure SQL Database / Azure Cosmos DB. Fabric connects
     OUTBOUND to the source, so it needs the source to be reachable from the
     Fabric service. In this environment tenant policy forces
     publicNetworkAccess=Disabled on both, so this is attempted and the exact
     failure is recorded rather than assumed.

  2. OPEN MIRRORING (fallback that works under private-only networking)
     A Fabric "GenericMirror" MirroredDatabase exposes a OneLake landing zone.
     An extractor running INSIDE the POC VNet reads the operational store over
     its private endpoint and pushes Parquet outbound to that landing zone;
     Fabric's managed replicator merges it into Delta with insert/update/delete
     semantics. No inbound path to the databases is required.

    python fabric/setup_mirroring.py --try-native
    python fabric/setup_mirroring.py --open-mirroring
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fabric.provision_fabric import Fabric, get_token  # noqa: E402

STATE = Path("artifacts/fabric-environment.json")
FINDINGS = Path("artifacts/fabric-integration-findings.json")

# Tables mirrored from Azure SQL. OrderJsonBlocks is included deliberately:
# its JsonPayload is nvarchar(max), and whether multi-hundred-KB values survive
# mirroring is one of the questions the POC has to answer with evidence.
SQL_MIRROR_TABLES = [
    "Customers", "Orders", "OrderVersions", "Properties",
    "Parties", "OrderParties", "Loans", "OrderJsonBlocks",
]


def load_state() -> dict[str, Any]:
    if not STATE.exists():
        raise SystemExit(f"{STATE} not found - run fabric/provision_fabric.py first")
    return json.loads(STATE.read_text())


def record(findings: dict[str, Any], key: str, value: Any) -> None:
    findings[key] = value
    FINDINGS.parent.mkdir(parents=True, exist_ok=True)
    FINDINGS.write_text(json.dumps(findings, indent=2), encoding="utf-8")


def try_native_sql_mirroring(fab: Fabric, st: dict[str, Any], sql_server: str,
                             sql_database: str) -> dict[str, Any]:
    """Attempt Fabric Mirroring for Azure SQL Database and capture the outcome."""
    wsid = st["workspaceId"]
    payload = {
        "displayName": "mir_sql_orders_native",
        "description": "Native Fabric mirroring of Azure SQL OrderDb (POC attempt)",
        "definition": {
            "parts": [
                {
                    "path": "mirroring.json",
                    "payload": base64.b64encode(json.dumps({
                        "properties": {
                            "source": {
                                "type": "AzureSqlDatabase",
                                "typeProperties": {
                                    "endpoint": sql_server,
                                    "databaseName": sql_database,
                                },
                            },
                            "target": {
                                "type": "MountedRelationalDatabase",
                                "typeProperties": {"format": "Delta"},
                            },
                            "mountedTables": [
                                {"source": {"typeProperties": {"schemaName": "ord", "tableName": t}}}
                                for t in SQL_MIRROR_TABLES
                            ],
                        }
                    }).encode()).decode(),
                    "payloadType": "InlineBase64",
                }
            ]
        },
    }
    status, body, headers = fab.call("POST", f"/workspaces/{wsid}/mirroredDatabases", payload)
    if status == 202:
        try:
            body = fab.wait_lro(headers) or body
            status = 201
        except Exception as exc:
            return {"attempted": True, "succeeded": False, "httpStatus": 202,
                    "error": f"{type(exc).__name__}: {exc}"[:500]}
    out = {
        "attempted": True,
        "succeeded": status < 400,
        "httpStatus": status,
        "response": body if isinstance(body, (dict, str)) else str(body),
        "requiredTables": SQL_MIRROR_TABLES,
        "note": ("Fabric connects outbound to the source. With "
                 "publicNetworkAccess=Disabled it needs a Fabric managed private "
                 "endpoint or an on-premises/VNet data gateway."),
    }
    if status < 400 and isinstance(body, dict) and body.get("id"):
        out["mirroredDatabaseId"] = body["id"]
        s2, b2, _ = fab.call("POST", f"/workspaces/{wsid}/mirroredDatabases/{body['id']}/startMirroring")
        out["startMirroringStatus"] = s2
        out["startMirroringResponse"] = b2
        s3, b3, _ = fab.call("GET", f"/workspaces/{wsid}/mirroredDatabases/{body['id']}/getMirroringStatus")
        out["mirroringStatus"] = b3
    return out


def try_native_cosmos_mirroring(fab: Fabric, st: dict[str, Any],
                                cosmos_endpoint: str, database: str, container: str) -> dict[str, Any]:
    wsid = st["workspaceId"]
    payload = {
        "displayName": "mir_cosmos_orders_native",
        "description": "Native Fabric mirroring of Cosmos DB for NoSQL (POC attempt)",
        "definition": {
            "parts": [
                {
                    "path": "mirroring.json",
                    "payload": base64.b64encode(json.dumps({
                        "properties": {
                            "source": {
                                "type": "CosmosDb",
                                "typeProperties": {
                                    "endpoint": cosmos_endpoint,
                                    "databaseName": database,
                                },
                            },
                            "target": {
                                "type": "MountedRelationalDatabase",
                                "typeProperties": {"format": "Delta"},
                            },
                            "mountedTables": [
                                {"source": {"typeProperties": {"tableName": container}}}
                            ],
                        }
                    }).encode()).decode(),
                    "payloadType": "InlineBase64",
                }
            ]
        },
    }
    status, body, headers = fab.call("POST", f"/workspaces/{wsid}/mirroredDatabases", payload)
    if status == 202:
        try:
            body = fab.wait_lro(headers) or body
            status = 201
        except Exception as exc:
            return {"attempted": True, "succeeded": False, "httpStatus": 202,
                    "error": f"{type(exc).__name__}: {exc}"[:500]}
    return {
        "attempted": True,
        "succeeded": status < 400,
        "httpStatus": status,
        "response": body if isinstance(body, (dict, str)) else str(body),
        "note": "Same outbound-connectivity requirement as the SQL mirroring path.",
    }


def create_open_mirror(fab: Fabric, st: dict[str, Any], name: str, description: str) -> dict[str, Any]:
    """Create (or find) a GenericMirror MirroredDatabase and return its landing zone."""
    wsid = st["workspaceId"]
    existing = next((i for i in fab.list_items(wsid, "MirroredDatabase")
                     if i.get("displayName") == name), None)
    if existing:
        item = existing
        print(f"  MirroredDatabase exists: {name} ({item['id']})")
    else:
        payload = {
            "displayName": name,
            "description": description,
            "definition": {
                "parts": [
                    {
                        "path": "mirroring.json",
                        "payload": base64.b64encode(json.dumps({
                            "properties": {
                                "source": {"type": "GenericMirror", "typeProperties": {}},
                                "target": {
                                    "type": "MountedRelationalDatabase",
                                    "typeProperties": {"format": "Delta"},
                                },
                            }
                        }).encode()).decode(),
                        "payloadType": "InlineBase64",
                    }
                ]
            },
        }
        status, body, headers = fab.call("POST", f"/workspaces/{wsid}/mirroredDatabases", payload)
        if status == 202:
            body = fab.wait_lro(headers) or {}
        if status >= 400:
            raise RuntimeError(f"create open mirror {name} failed {status}: {body}")
        item = body if isinstance(body, dict) and body.get("id") else next(
            (i for i in fab.list_items(wsid, "MirroredDatabase") if i.get("displayName") == name), {})
        print(f"  MirroredDatabase created: {name} ({item.get('id')})")

    mid = item["id"]
    # Start the replicator so it picks up landing-zone files.
    s, b, _ = fab.call("POST", f"/workspaces/{wsid}/mirroredDatabases/{mid}/startMirroring")
    started = s < 400 or (isinstance(b, dict) and "already" in json.dumps(b).lower())
    print(f"  startMirroring -> {s}")

    s2, detail, _ = fab.call("GET", f"/workspaces/{wsid}/mirroredDatabases/{mid}")
    props = detail.get("properties", {}) if isinstance(detail, dict) else {}
    landing = props.get("oneLakeTablesPath", "")
    # The landing zone sits next to the Tables path.
    landing_zone = landing.replace("/Tables", "/Files/LandingZone") if landing else (
        f"https://onelake.dfs.fabric.microsoft.com/{wsid}/{mid}/Files/LandingZone")

    s3, status_body, _ = fab.call("GET", f"/workspaces/{wsid}/mirroredDatabases/{mid}/getMirroringStatus")

    return {
        "name": name,
        "id": mid,
        "workspaceId": wsid,
        "startMirroringHttp": s,
        "started": started,
        "oneLakeTablesPath": landing,
        "landingZoneUrl": landing_zone,
        "landingZoneDfsPath": f"{mid}/Files/LandingZone",
        "sqlEndpoint": props.get("sqlEndpointProperties", {}),
        "mirroringStatus": status_body,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--try-native", action="store_true",
                    help="attempt native Fabric mirroring and record the outcome")
    ap.add_argument("--open-mirroring", action="store_true",
                    help="create the Open Mirroring landing zones (works under private-only networking)")
    ap.add_argument("--sql-server", default="")
    ap.add_argument("--sql-database", default="OrderDb")
    ap.add_argument("--cosmos-endpoint", default="")
    ap.add_argument("--cosmos-database", default="orderdb")
    ap.add_argument("--cosmos-container", default="orders")
    args = ap.parse_args()

    st = load_state()
    fab = Fabric(get_token())
    findings: dict[str, Any] = json.loads(FINDINGS.read_text()) if FINDINGS.exists() else {}
    findings["generatedUtc"] = datetime.now(timezone.utc).isoformat()
    findings["workspaceId"] = st["workspaceId"]

    if args.try_native:
        print("attempting NATIVE Fabric mirroring (path 1, simplest supported pattern)")
        sql_server = args.sql_server or _from_deployment("sqlServerFqdn")
        cosmos_ep = args.cosmos_endpoint or _from_deployment("cosmosEndpoint")
        r1 = try_native_sql_mirroring(fab, st, sql_server, args.sql_database)
        print(f"  SQL   native mirroring: http={r1['httpStatus']} succeeded={r1['succeeded']}")
        if not r1["succeeded"]:
            print(f"    {json.dumps(r1.get('response') or r1.get('error'))[:400]}")
        record(findings, "nativeSqlMirroring", r1)

        r2 = try_native_cosmos_mirroring(fab, st, cosmos_ep, args.cosmos_database, args.cosmos_container)
        print(f"  Cosmos native mirroring: http={r2['httpStatus']} succeeded={r2['succeeded']}")
        if not r2["succeeded"]:
            print(f"    {json.dumps(r2.get('response') or r2.get('error'))[:400]}")
        record(findings, "nativeCosmosMirroring", r2)

    if args.open_mirroring:
        print("creating OPEN MIRRORING landing zones (path 2)")
        sql_mirror = create_open_mirror(
            fab, st, "mir_sql_orders",
            "Open Mirroring target for the Azure SQL hybrid model (PATH A)")
        cosmos_mirror = create_open_mirror(
            fab, st, "mir_cosmos_orders",
            "Open Mirroring target for the Cosmos DB aggregate model (PATH B)")
        record(findings, "openMirroring", {"sql": sql_mirror, "cosmos": cosmos_mirror})

        st["mirrors"] = {"sql": sql_mirror, "cosmos": cosmos_mirror}
        STATE.write_text(json.dumps(st, indent=2), encoding="utf-8")
        print(f"\n  SQL    landing zone: {sql_mirror['landingZoneDfsPath']}")
        print(f"  Cosmos landing zone: {cosmos_mirror['landingZoneDfsPath']}")
        print(f"  SQL mirror SQL endpoint: {sql_mirror['sqlEndpoint'].get('connectionString')}")

    print(f"-> {FINDINGS}")
    print("MIRRORING_SETUP_DONE")


def _from_deployment(key: str) -> str:
    p = Path("artifacts/deployment-outputs.json")
    if not p.exists():
        raise SystemExit(f"{p} not found; pass the value explicitly")
    return json.loads(p.read_text())[key]


if __name__ == "__main__":
    main()
