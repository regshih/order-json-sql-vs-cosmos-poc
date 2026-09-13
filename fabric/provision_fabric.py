"""Provision the Fabric analytics environment via the Fabric REST API.

Creates (idempotently):
    workspace                -> assigned to the POC capacity
    lakehouse  lh_orders_poc -> OneLake landing + Delta tables + SQL endpoint
    warehouse  wh_orders_poc -> curated star schema over the lakehouse data

Runs as the signed-in user, not a service principal: the Fabric
MirroredDatabase API does not support service principals (see docs/SOURCES.md),
and the same token works for every other item type.

    python fabric/provision_fabric.py --capacity fabordjsonpoc915d \
        --workspace ws-order-json-poc
    python fabric/provision_fabric.py --show
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import urllib.error
import urllib.request

FABRIC_API = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com"
STATE_FILE = Path("artifacts/fabric-environment.json")


def get_token(scope: str = FABRIC_SCOPE) -> str:
    """Acquire a Fabric token for the signed-in user.

    Prefers azure-identity (works on Linux and Windows alike); falls back to the
    Azure CLI binary, whose name differs by platform.
    """
    try:
        from azure.identity import AzureCliCredential

        return AzureCliCredential().get_token(f"{scope}/.default").token
    except Exception:
        pass
    for exe in ("az", "az.cmd", "az.bat"):
        try:
            out = subprocess.run(
                [exe, "account", "get-access-token", "--resource", scope,
                 "--query", "accessToken", "-o", "tsv"],
                capture_output=True, text=True, check=True,
            )
            return out.stdout.strip()
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    raise SystemExit("could not acquire a Fabric token - run `az login` first")


class Fabric:
    def __init__(self, token: str) -> None:
        self.token = token

    def call(self, method: str, path: str, body: Any = None,
             expect: tuple[int, ...] = (200, 201, 202)) -> tuple[int, Any, dict[str, str]]:
        url = path if path.startswith("http") else f"{FABRIC_API}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                raw = r.read().decode() or "{}"
                headers = {k.lower(): v for k, v in r.headers.items()}
                return r.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw), headers
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = raw
            return e.code, parsed, {k.lower(): v for k, v in (e.headers or {}).items()}

    def wait_lro(self, headers: dict[str, str], timeout: float = 600) -> Any:
        """Follow a long-running-operation until it settles."""
        loc = headers.get("location") or headers.get("operation-location")
        if not loc:
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            status, body, h = self.call("GET", loc)
            state = (body or {}).get("status") if isinstance(body, dict) else None
            if state in ("Succeeded", "Completed"):
                res = h.get("location")
                if res and res != loc:
                    _, rbody, _ = self.call("GET", res)
                    return rbody
                return body
            if state in ("Failed", "Cancelled"):
                raise RuntimeError(f"Fabric operation {state}: {body}")
            time.sleep(float(h.get("retry-after", 5)))
        raise TimeoutError(f"Fabric operation did not settle within {timeout}s")

    # ---- workspaces ----------------------------------------------------
    def find_workspace(self, name: str) -> dict[str, Any] | None:
        status, body, _ = self.call("GET", "/workspaces")
        if status != 200:
            raise RuntimeError(f"list workspaces failed {status}: {body}")
        return next((w for w in body.get("value", []) if w.get("displayName") == name), None)

    def create_workspace(self, name: str, capacity_id: str, description: str) -> dict[str, Any]:
        existing = self.find_workspace(name)
        if existing:
            print(f"  workspace exists: {name} ({existing['id']})")
            ws = existing
        else:
            status, body, h = self.call(
                "POST", "/workspaces",
                {"displayName": name, "description": description, "capacityId": capacity_id},
            )
            if status not in (200, 201, 202):
                raise RuntimeError(f"create workspace failed {status}: {body}")
            ws = body if isinstance(body, dict) and body.get("id") else (self.wait_lro(h) or {})
            if not ws.get("id"):
                ws = self.find_workspace(name) or {}
            print(f"  workspace created: {name} ({ws.get('id')})")

        # Ensure it is on the right capacity even if it already existed.
        if ws.get("capacityId", "").lower() != capacity_id.lower():
            st, b, _ = self.call("POST", f"/workspaces/{ws['id']}/assignToCapacity",
                                 {"capacityId": capacity_id})
            print(f"  assignToCapacity -> {st} {b if st >= 400 else ''}")
        return ws

    # ---- items ---------------------------------------------------------
    def list_items(self, workspace_id: str, item_type: str | None = None) -> list[dict[str, Any]]:
        path = f"/workspaces/{workspace_id}/items"
        if item_type:
            path += f"?type={item_type}"
        status, body, _ = self.call("GET", path)
        return body.get("value", []) if status == 200 else []

    def create_item(self, workspace_id: str, name: str, item_type: str,
                    description: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
        existing = next((i for i in self.list_items(workspace_id, item_type)
                         if i.get("displayName") == name), None)
        if existing:
            print(f"  {item_type} exists: {name} ({existing['id']})")
            return existing
        payload: dict[str, Any] = {"displayName": name, "type": item_type}
        if description:
            payload["description"] = description
        if extra:
            payload.update(extra)
        status, body, h = self.call("POST", f"/workspaces/{workspace_id}/items", payload)
        if status == 202:
            body = self.wait_lro(h) or {}
        if status >= 400:
            raise RuntimeError(f"create {item_type} {name} failed {status}: {body}")
        item = body if isinstance(body, dict) and body.get("id") else next(
            (i for i in self.list_items(workspace_id, item_type) if i.get("displayName") == name), {})
        print(f"  {item_type} created: {name} ({item.get('id')})")
        return item

    def lakehouse_details(self, workspace_id: str, lakehouse_id: str) -> dict[str, Any]:
        status, body, _ = self.call("GET", f"/workspaces/{workspace_id}/lakehouses/{lakehouse_id}")
        return body if status == 200 else {}

    def warehouse_details(self, workspace_id: str, warehouse_id: str) -> dict[str, Any]:
        status, body, _ = self.call("GET", f"/workspaces/{workspace_id}/warehouses/{warehouse_id}")
        return body if status == 200 else {}

    def capacities(self) -> list[dict[str, Any]]:
        status, body, _ = self.call("GET", "/capacities")
        return body.get("value", []) if status == 200 else []


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capacity", default="fabordjsonpoc915d")
    ap.add_argument("--workspace", default="ws-order-json-poc")
    ap.add_argument("--lakehouse", default="lh_orders_poc")
    ap.add_argument("--warehouse", default="wh_orders_poc")
    ap.add_argument("--skip-warehouse", action="store_true")
    ap.add_argument("--show", action="store_true", help="print current state and exit")
    ap.add_argument("--state-out", default=str(STATE_FILE))
    args = ap.parse_args()

    fab = Fabric(get_token())

    caps = fab.capacities()
    print(f"visible capacities: {[(c.get('displayName'), c.get('sku'), c.get('state')) for c in caps]}")
    cap = next((c for c in caps if c.get("displayName") == args.capacity), None)
    if not cap:
        raise SystemExit(
            f"capacity {args.capacity!r} not visible to this identity. It may still be "
            f"propagating (retry in a minute), or the identity is not a capacity admin."
        )
    capacity_id = cap["id"]
    print(f"capacity: {args.capacity} sku={cap.get('sku')} state={cap.get('state')} id={capacity_id}")

    if args.show:
        ws = fab.find_workspace(args.workspace)
        if not ws:
            print("workspace not created yet")
            return
        print(f"workspace {ws['displayName']} {ws['id']}")
        for i in fab.list_items(ws["id"]):
            print(f"  {i['type']:<22} {i['displayName']:<26} {i['id']}")
        return

    print("provisioning:")
    ws = fab.create_workspace(
        args.workspace, capacity_id,
        "POC: Azure SQL hybrid vs Cosmos DB for large order JSON - analytics layer",
    )
    wsid = ws["id"]

    lh = fab.create_item(wsid, args.lakehouse, "Lakehouse",
                         "Bronze/silver Delta tables landed from both operational backends")
    lh_detail = fab.lakehouse_details(wsid, lh["id"])

    wh_detail: dict[str, Any] = {}
    wh: dict[str, Any] = {}
    if not args.skip_warehouse:
        try:
            wh = fab.create_item(wsid, args.warehouse, "Warehouse",
                                 "Curated star schema: FactOrder, FactLoan, FactOrderCharge + dims")
            wh_detail = fab.warehouse_details(wsid, wh["id"])
        except Exception as exc:
            print(f"  warehouse creation failed (continuing): {exc}")

    state = {
        "capacityName": args.capacity,
        "capacityId": capacity_id,
        "capacitySku": cap.get("sku"),
        "workspaceName": args.workspace,
        "workspaceId": wsid,
        "lakehouseName": args.lakehouse,
        "lakehouseId": lh["id"],
        "lakehouseProperties": lh_detail.get("properties", {}),
        "warehouseName": wh.get("displayName"),
        "warehouseId": wh.get("id"),
        "warehouseProperties": wh_detail.get("properties", {}),
        "oneLakeAbfss": f"abfss://{wsid}@onelake.dfs.fabric.microsoft.com/{lh['id']}",
        "items": [{"type": i["type"], "name": i["displayName"], "id": i["id"]}
                  for i in fab.list_items(wsid)],
    }
    p = Path(args.state_out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")

    print(f"\nworkspace id : {wsid}")
    print(f"lakehouse id : {lh['id']}")
    sqlep = (lh_detail.get("properties", {}).get("sqlEndpointProperties") or {})
    print(f"lakehouse SQL endpoint: {sqlep.get('connectionString')} db={sqlep.get('id')}")
    if wh_detail:
        print(f"warehouse SQL endpoint: {wh_detail.get('properties', {}).get('connectionString')}")
    print(f"OneLake path : {state['oneLakeAbfss']}")
    print(f"-> {p}")
    print("FABRIC_PROVISION_OK")


if __name__ == "__main__":
    sys.exit(main())
