"""Repair an Open Mirroring table whose replication has halted.

Why this exists. The Open Mirroring landing zone is an **append-only** protocol:
file names must increase monotonically, and Fabric will not re-process a sequence
number it has already consumed. Because the replicator *deletes* files once it
has merged them, a naive "next sequence = highest file on disk + 1" resets to 1
and **overwrites a sequence Fabric already processed**. That does not merely skip
one update - it halts replication for the table, and every subsequent file sits
in the landing zone unconsumed.

That is exactly what happened here, and it is not self-healing: fixing the
sequence logic stops it recurring but does not restart the table.

Repair sequence:
    1. stopMirroring
    2. delete every file in the table's landing-zone folder
    3. startMirroring
    4. clear the local sequence watermark for that table
    5. re-seed with a full push

    python fabric/repair_open_mirror.py --mirror cosmos
    python fabric/repair_open_mirror.py --mirror cosmos --list-only
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fabric.provision_fabric import Fabric, get_token  # noqa: E402

STATE = Path("artifacts/fabric-environment.json")
WATERMARKS = Path("artifacts/fabric-push-watermarks.json")
ONELAKE = "https://onelake.dfs.fabric.microsoft.com"


def onelake_fs(workspace_id: str):
    from azure.identity import DefaultAzureCredential
    from azure.storage.filedatalake import DataLakeServiceClient

    return DataLakeServiceClient(
        account_url=ONELAKE, credential=DefaultAzureCredential()
    ).get_file_system_client(workspace_id)


def list_landing_zone(fs, lz: str) -> list[tuple[str, int]]:
    out = []
    try:
        for p in fs.get_paths(path=lz, recursive=True):
            if not p.is_directory:
                out.append((p.name, getattr(p, "content_length", 0) or 0))
    except Exception as exc:
        print(f"  could not list {lz}: {exc}")
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mirror", choices=["sql", "cosmos"], required=True)
    ap.add_argument("--list-only", action="store_true")
    ap.add_argument("--keep-metadata", action="store_true",
                    help="keep _metadata.json (it is rewritten by the next push anyway)")
    args = ap.parse_args()

    st = json.loads(STATE.read_text())
    ws = st["workspaceId"]
    mirror = st["mirrors"][args.mirror]
    mid, lz = mirror["id"], mirror["landingZoneDfsPath"]

    fab = Fabric(get_token())
    fs = onelake_fs(ws)

    print(f"mirror : {args.mirror} ({mid})")
    print(f"landing: {lz}\n")

    before = list_landing_zone(fs, lz)
    print(f"landing zone currently holds {len(before)} files:")
    for name, size in before:
        print(f"   {name.split('LandingZone/')[-1]:<58} {size:,}")

    parquet = [n for n, _ in before if n.endswith(".parquet")]
    print(f"\nunconsumed .parquet files: {len(parquet)}")
    if len(parquet) > 1:
        print("  >1 unconsumed file is the signature of halted replication:")
        print("  the replicator deletes what it merges, so files should not pile up.")

    if args.list_only:
        return 0

    print("\n1. stopMirroring")
    s, b, _ = fab.call("POST", f"/workspaces/{ws}/mirroredDatabases/{mid}/stopMirroring")
    print(f"   -> {s} {json.dumps(b)[:160] if s >= 400 else 'ok'}")
    time.sleep(10)

    print("2. clearing the landing zone")
    removed = 0
    for name, _ in before:
        leaf = name.rsplit("/", 1)[-1]
        if args.keep_metadata and leaf == "_metadata.json":
            continue
        if leaf.startswith("_FilesReadyToDelete"):
            continue
        try:
            fs.get_file_client(name).delete_file()
            removed += 1
        except Exception as exc:
            print(f"   could not delete {leaf}: {str(exc)[:120]}")
    print(f"   removed {removed} files")

    print("3. startMirroring")
    s, b, _ = fab.call("POST", f"/workspaces/{ws}/mirroredDatabases/{mid}/startMirroring")
    print(f"   -> {s} {json.dumps(b)[:160] if s >= 400 else 'ok'}")
    time.sleep(10)

    print("4. clearing the local sequence watermark")
    if WATERMARKS.exists():
        wm = json.loads(WATERMARKS.read_text())
        seqs = wm.get("_sequences", {})
        cleared = [k for k in list(seqs) if k.startswith(lz)]
        for k in cleared:
            del seqs[k]
        # Force the next push to be a full re-seed.
        if args.mirror == "cosmos":
            wm.pop("cosmos", None)
        else:
            wm.pop("sql", None)
        WATERMARKS.write_text(json.dumps(wm, indent=2), encoding="utf-8")
        print(f"   cleared {len(cleared)} sequence entries and the extraction watermark")
    else:
        print("   no watermark file present")

    after = list_landing_zone(fs, lz)
    print(f"\nlanding zone now holds {len(after)} files")
    print("\nREPAIR_DONE - next step:")
    print(f"   python -m ingestion.fabric.push_to_onelake --backend {args.mirror} --mode full")
    return 0


if __name__ == "__main__":
    sys.exit(main())
