"""Documentation integrity check.

Verifies that every relative link in every committed markdown file resolves, and
that the Mermaid diagrams parse well enough to render. Run before sharing.

Exits non-zero on failure so it can gate CI.

    python tools/check_docs.py
"""

from __future__ import annotations

import argparse
import io
import pathlib
import re
import sys

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules"}


def markdown_files(root: pathlib.Path) -> list[pathlib.Path]:
    return [
        p for p in root.rglob("*.md")
        if not SKIP_DIRS & set(p.parts)
    ]


def check_links(root: pathlib.Path) -> tuple[int, list[tuple[str, str]]]:
    broken: list[tuple[str, str]] = []
    checked = 0
    for f in markdown_files(root):
        text = io.open(f, encoding="utf-8", errors="ignore").read()
        # [label](target) or [label](target#anchor); skip absolute URLs.
        for m in re.finditer(r"\[([^\]]+)\]\(([^)#\s]+?)(#[^)]*)?\)", text):
            target = m.group(2).strip()
            if target.startswith(("http://", "https://", "mailto:", "tel:")):
                continue
            checked += 1
            if not (f.parent / target).resolve().exists():
                broken.append((str(f), target))
    return checked, broken


# Node shapes Mermaid uses; we only check bracket balance and that every
# diagram declares a type.
DIAGRAM_TYPES = ("flowchart", "graph", "sequenceDiagram", "erDiagram",
                 "classDiagram", "stateDiagram", "gantt", "pie", "journey")


def check_mermaid(root: pathlib.Path) -> list[tuple[str, str]]:
    problems: list[tuple[str, str]] = []
    for f in sorted((root / "diagrams").glob("*.mmd")) if (root / "diagrams").exists() else []:
        text = io.open(f, encoding="utf-8", errors="ignore").read()
        body = "\n".join(
            ln for ln in text.splitlines() if not ln.strip().startswith("%%")
        ).strip()
        if not body:
            problems.append((str(f), "file is empty"))
            continue
        if not any(body.startswith(t) or f"\n{t}" in body for t in DIAGRAM_TYPES):
            problems.append((str(f), "no diagram type declaration"))
        for open_c, close_c in (("[", "]"), ("(", ")"), ("{", "}")):
            if body.count(open_c) != body.count(close_c):
                problems.append((
                    str(f),
                    f"unbalanced {open_c}{close_c}: "
                    f"{body.count(open_c)} vs {body.count(close_c)}",
                ))
        # A label containing an unescaped parenthesis inside a [] node breaks
        # the Mermaid parser; quoted labels are the fix and we use them, so
        # flag any [..(..)..] that is not quoted.
        for m in re.finditer(r"\[(?!\")([^\]\"]*\([^\]\"]*\)[^\]\"]*)\]", body):
            problems.append((str(f), f"unquoted parentheses in node label: {m.group(1)[:40]!r}"))
    return problems


def check_mermaid_blocks(root: pathlib.Path) -> list[tuple[str, str]]:
    """Fenced ```mermaid blocks inside markdown."""
    problems: list[tuple[str, str]] = []
    for f in markdown_files(root):
        text = io.open(f, encoding="utf-8", errors="ignore").read()
        for i, block in enumerate(re.findall(r"```mermaid\n(.*?)```", text, re.DOTALL), 1):
            body = "\n".join(
                ln for ln in block.splitlines() if not ln.strip().startswith("%%")
            ).strip()
            if not any(body.startswith(t) for t in DIAGRAM_TYPES):
                problems.append((str(f), f"mermaid block {i}: no diagram type"))
            for open_c, close_c in (("[", "]"), ("(", ")"), ("{", "}")):
                if body.count(open_c) != body.count(close_c):
                    problems.append((str(f), f"mermaid block {i}: unbalanced {open_c}{close_c}"))
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".")
    args = ap.parse_args()
    root = pathlib.Path(args.root)

    failures = 0

    checked, broken = check_links(root)
    print(f"1. relative markdown links ({checked} checked)")
    if broken:
        failures += 1
        print(f"   FAIL - {len(broken)} broken:")
        for f, t in broken:
            print(f"        {f} -> {t}")
    else:
        print("   PASS")

    mm = check_mermaid(root)
    print(f"2. Mermaid diagram files")
    if mm:
        failures += 1
        for f, why in mm:
            print(f"   FAIL - {f}: {why}")
    else:
        print("   PASS")

    mb = check_mermaid_blocks(root)
    print("3. Mermaid blocks embedded in markdown")
    if mb:
        failures += 1
        for f, why in mb:
            print(f"   FAIL - {f}: {why}")
    else:
        print("   PASS")

    print()
    if failures:
        print(f"DOC_CHECK_FAILED ({failures} check(s))")
        return 1
    print("DOC_CHECK_PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
