"""Pull a directory off a POC VM without SSH.

The benchmark VMs are reachable only through the Azure VM agent (outbound SSH is
blocked from the operator workstation), so files come back as base64 chunks of a
gzipped tarball through `az vm run-command` output, which is size-limited per
call.

    python scripts/fetch_vm_results.py vm-orderjsonpoc-load
    python scripts/fetch_vm_results.py vm-orderjsonpoc-api --remote artifacts
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import re
import subprocess
import sys
import time
import tarfile
import tempfile
from pathlib import Path


def run_command(rg: str, vm: str, script: str, timeout: int = 900,
                attempts: int = 4) -> str:
    """Invoke a shell script on the VM and return only its stdout section.

    Retries on transient transport failures - a long chunked fetch makes dozens
    of calls and an occasional connection reset is expected.
    """
    last = ""
    for attempt in range(1, attempts + 1):
        proc = None
        for exe in ("az", "az.cmd"):
            try:
                proc = subprocess.run(
                    [exe, "vm", "run-command", "invoke", "-g", rg, "-n", vm,
                     "--command-id", "RunShellScript", "--scripts", script,
                     "--query", "value[0].message", "-o", "tsv"],
                    capture_output=True, text=True, timeout=timeout,
                )
                break
            except FileNotFoundError:
                continue
            except subprocess.TimeoutExpired:
                last = "timeout"
                proc = None
                break
        if proc is None and last != "timeout":
            raise SystemExit("az CLI not found")
        if proc is not None and proc.returncode == 0:
            break
        last = (proc.stderr[-300:] if proc is not None else last)
        if attempt < attempts:
            wait = 5 * attempt
            print(f"    retry {attempt}/{attempts - 1} in {wait}s ({last.strip()[:120]})")
            time.sleep(wait)
    else:
        raise RuntimeError(f"run-command failed after {attempts} attempts: {last}")

    out = proc.stdout
    # Format: "Enable succeeded: \n[stdout]\n<content>\n[stderr]\n<errors>"
    m = re.search(r"\[stdout\]\n(.*?)(?:\n\[stderr\]|$)", out, re.DOTALL)
    return m.group(1) if m else out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("vm")
    ap.add_argument("--resource-group", default="rg-order-json-poc-westus3")
    ap.add_argument("--remote", default="results", help="path under /opt/poc to fetch")
    ap.add_argument("--root", default="/opt/poc")
    ap.add_argument("--dest", default=".", help="local directory to extract into")
    # az vm run-command truncates stdout at 4096 characters, and returns the
    # TAIL when it truncates - so an oversized chunk silently corrupts the
    # stream rather than failing loudly. Stay well under the limit.
    ap.add_argument("--chunk", type=int, default=3_800)
    args = ap.parse_args()

    print(f"packing {args.remote} on {args.vm} ...")
    size_out = run_command(
        args.resource_group, args.vm,
        f"cd {args.root} && tar czf /tmp/fetch.tgz {args.remote} 2>/dev/null; "
        f"base64 -w0 /tmp/fetch.tgz | wc -c",
    )
    digits = re.findall(r"\d+", size_out)
    if not digits:
        raise SystemExit(f"could not determine payload size; got: {size_out[:300]!r}")
    size = int(digits[-1])
    print(f"  base64 length: {size:,} bytes")

    parts: list[str] = []
    offset = 1
    while offset <= size:
        end = min(offset + args.chunk - 1, size)
        print(f"  fetching {offset:,}..{end:,} of {size:,}")
        chunk = run_command(
            args.resource_group, args.vm,
            f"base64 -w0 /tmp/fetch.tgz | cut -c{offset}-{end}",
        )
        # Keep only base64 alphabet characters.
        cleaned = re.sub(r"[^A-Za-z0-9+/=]", "", chunk)
        expected = end - offset + 1
        if len(cleaned) != expected:
            raise SystemExit(
                f"chunk {offset}..{end} returned {len(cleaned)} chars, expected "
                f"{expected} - the run-command output limit was exceeded"
            )
        parts.append(cleaned)
        offset = end + 1

    blob = "".join(parts)
    if len(blob) != size:
        print(f"  WARNING: assembled {len(blob):,} chars, expected {size:,}")
    data = base64.b64decode(blob)
    print(f"  tarball: {len(data):,} bytes")

    dest = Path(args.dest).resolve()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        members = tf.getmembers()
        tf.extractall(dest)
    print(f"  extracted {len(members)} entries into {dest}")
    for m in members:
        if m.isfile():
            print(f"    {m.name} ({m.size:,} B)")

    run_command(args.resource_group, args.vm, "rm -f /tmp/fetch.tgz")


if __name__ == "__main__":
    sys.exit(main())
