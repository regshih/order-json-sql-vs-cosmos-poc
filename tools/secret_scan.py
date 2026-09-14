"""Repository hygiene gate (§28). Run before sharing or publishing.

Two independent checks:

  1. CREDENTIAL SCAN  - credential-shaped strings, tokens, connection strings
                        with embedded keys, tracked .env files.
  2. SAMPLE LEAK SCAN - takes distinctive values out of the real customer sample
                        and proves none of them appear in any tracked file.
                        This is the check that actually matters: it verifies the
                        claim "only synthetic data is committed" against the
                        source data rather than trusting .gitignore.

Exits non-zero if anything fails, so it can gate CI.

    python tools/secret_scan.py
    python tools/secret_scan.py --sample data/source/150159_Order.MASKED.json.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from typing import Any

MAX_FILE_BYTES = 8_000_000

CREDENTIAL_PATTERNS = [
    (r"(?i)(password|pwd)\s*=\s*[\"'][^\"'{<$]", "password assignment"),
    (r"(?i)AccountKey\s*=\s*[A-Za-z0-9+/=]{20,}", "storage account key"),
    (r"(?i)SharedAccessSignature\s*=\s*\S+", "SAS token"),
    (r"(?i)client_secret\s*=\s*[\"'][^\"'{<$]", "client secret"),
    (r"(?i)(api[_-]?key|apikey)\s*=\s*[\"'][^\"'{<$]", "api key"),
    (r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}", "JWT"),
    (r"AccountEndpoint=.*AccountKey=", "Cosmos connection string with key"),
    (r"(?i)Server=.*(Password|Pwd)=", "SQL connection string with password"),
]

# Extensions that legitimately contain long opaque strings (results, docs).
CREDENTIAL_SKIP_SUFFIXES = {".json", ".csv", ".md", ".mmd"}


def tracked_files() -> list[pathlib.Path]:
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    return [pathlib.Path(p) for p in out.stdout.split("\n") if p.strip()]


def read(p: pathlib.Path) -> str | None:
    try:
        if not p.is_file() or p.stat().st_size > MAX_FILE_BYTES:
            return None
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def check_sample_not_tracked(files: list[pathlib.Path]) -> list[str]:
    bad = [
        str(f) for f in files
        if re.search(r"(?i)(MASKED|data/source/)", str(f).replace("\\", "/"))
    ]
    return bad


def check_env_not_tracked(files: list[pathlib.Path]) -> list[str]:
    return [
        str(f) for f in files
        if f.name.startswith(".env") and f.name != ".env.example"
    ]


def check_credentials(files: list[pathlib.Path]) -> list[tuple[str, str, str]]:
    hits = []
    for f in files:
        if f.suffix in CREDENTIAL_SKIP_SUFFIXES or f.name == ".env.example":
            continue
        text = read(f)
        if text is None:
            continue
        for pattern, label in CREDENTIAL_PATTERNS:
            m = re.search(pattern, text)
            if m:
                hits.append((str(f), label, m.group(0)[:60]))
    return hits


def sample_value_categories(sample: pathlib.Path) -> dict[str, list[str]]:
    """Extract distinctive, identifying values from the customer sample."""
    doc = json.loads(sample.read_text(encoding="utf-8-sig"))
    vals: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str) and node.strip():
            vals.add(node)

    walk(doc)
    guid = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
    return {
        "GUIDs": [v for v in vals if guid.fullmatch(v)],
        "emails": [v for v in vals if "@" in v and "." in v],
        "phone-like": [
            v for v in vals
            if re.fullmatch(r"[\d()\-+.\s]{9,20}", v) and sum(c.isdigit() for c in v) >= 9
        ],
        "money amounts": [
            v for v in vals if re.fullmatch(r"-?[\d,]+\.\d{2}", v) and len(v) >= 6
        ],
        "long free text": [
            v for v in vals if len(v) > 60 and " " in v and not v.startswith("{\\rtf")
        ],
    }


def check_sample_leak(files: list[pathlib.Path], sample: pathlib.Path) -> dict[str, list[tuple[str, str]]]:
    cats = sample_value_categories(sample)
    texts = {str(f): read(f) for f in files}
    texts = {k: v for k, v in texts.items() if v}
    results: dict[str, list[tuple[str, str]]] = {}
    for cat, values in cats.items():
        hits: list[tuple[str, str]] = []
        for v in values:
            for fname, text in texts.items():
                if v in text:
                    hits.append((fname, v[:60]))
                    break
        results[cat] = hits
    results["_counts"] = {c: len(v) for c, v in cats.items()}  # type: ignore[assignment]
    results["_scanned"] = len(texts)  # type: ignore[assignment]
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample", default="data/source/150159_Order.MASKED.json.json")
    args = ap.parse_args()

    files = tracked_files()
    failures = 0
    print(f"repository hygiene scan - {len(files)} tracked files\n")

    print("1. customer sample must not be tracked")
    bad = check_sample_not_tracked(files)
    if bad:
        failures += 1
        print(f"   FAIL - tracked: {bad}")
    else:
        print("   PASS")

    print("2. no real .env tracked")
    bad = check_env_not_tracked(files)
    if bad:
        failures += 1
        print(f"   FAIL - tracked: {bad}")
    else:
        print("   PASS")

    print("3. credential-shaped strings")
    hits = check_credentials(files)
    if hits:
        failures += 1
        for f, label, snippet in hits[:10]:
            print(f"   FAIL - {f}: {label}: {snippet!r}")
    else:
        print("   PASS")

    print("4. customer sample values must not appear in any tracked file")
    sample = pathlib.Path(args.sample)
    if not sample.exists():
        print(f"   SKIPPED - sample not present locally ({sample})")
    else:
        res = check_sample_leak(files, sample)
        counts = res.pop("_counts")
        scanned = res.pop("_scanned")
        print(f"   scanned {scanned} readable files")
        leaked = 0
        for cat, hits in res.items():
            n = counts[cat]  # type: ignore[index]
            if hits:
                leaked += len(hits)
                print(f"   FAIL - {cat}: {len(hits)} of {n} distinct values found")
                for f, v in hits[:5]:
                    print(f"          {f}: {v!r}")
            else:
                print(f"   PASS - {cat}: 0 of {n} distinct values found")
        if leaked:
            failures += 1

    print()
    if failures:
        print(f"SECRET_SCAN_FAILED ({failures} check(s))")
        return 1
    print("SECRET_SCAN_PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
