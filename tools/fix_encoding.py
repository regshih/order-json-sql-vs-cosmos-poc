"""Detect and repair mojibake in generated documentation.

Mojibake here means UTF-8 text that was at some point decoded as cp1252 and
re-encoded as UTF-8, so an em dash (U+2014) turns into three characters. Repeated
read/write cycles with mismatched encodings can leave a file with a *mixture* of
correct UTF-8, mojibake, and stray raw cp1252 bytes.

    python tools/fix_encoding.py              # report only, non-zero if dirty
    python tools/fix_encoding.py --check      # same
    python tools/fix_encoding.py --fix

Deliberately scoped to prose files (.md, .csv, .mmd) and it never rewrites
itself or any .py file. Source code with a non-ASCII literal is far more likely
to be intentional than corrupt, and a byte-level rewrite of source is a much
worse failure than leaving a stray character in a document. This tool learned
that the hard way by mangling its own marker table.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules"}
PROSE_SUFFIXES = {".md", ".csv", ".mmd"}
SELF = pathlib.Path(__file__).name

# Suspect characters are built from codepoints rather than written as literals,
# so this file contains no mojibake-looking text of its own.
_SUSPECT_CODEPOINTS = (
    0x00C2, 0x00C3,          # Â Ã  - the leading byte of a mis-decoded sequence
    0x00E2,                  # â
    0x20AC, 0x201A, 0x0192,  # € ‚ ƒ
    0x201E, 0x2026, 0x2020,  # „ … †
    0x2021, 0x02C6, 0x2030,  # ‡ ˆ ‰
    0x0160, 0x2039, 0x0152,  # Š ‹ Œ
    0x017D, 0x02DC, 0x2122,  # Ž ˜ ™
    0x0161, 0x017E, 0x0178,  # š ž Ÿ
)
SUSPECT_CHARS = {chr(c) for c in _SUSPECT_CODEPOINTS}

# UTF-8 encodings of the cp1252 mis-reading of common UTF-8 punctuation.
MOJIBAKE_BYTES: dict[bytes, bytes] = {
    b"\xc3\xa2\xc2\x80\xc2\x94": b"\xe2\x80\x94",  # em dash
    b"\xc3\xa2\xc2\x80\xc2\x93": b"\xe2\x80\x93",  # en dash
    b"\xc3\xa2\xc2\x80\xc2\x98": b"\xe2\x80\x98",  # left single quote
    b"\xc3\xa2\xc2\x80\xc2\x99": b"\xe2\x80\x99",  # right single quote
    b"\xc3\xa2\xc2\x80\xc2\x9c": b"\xe2\x80\x9c",  # left double quote
    b"\xc3\xa2\xc2\x80\xc2\x9d": b"\xe2\x80\x9d",  # right double quote
    b"\xc3\xa2\xc2\x86\xc2\x92": b"\xe2\x86\x92",  # right arrow
    b"\xc3\xa2\xc2\x89\xc2\xa5": b"\xe2\x89\xa5",  # >=
    b"\xc3\xa2\xc2\x89\xc2\xa4": b"\xe2\x89\xa4",  # <=
    b"\xc3\x82\xc2\xa0": b" ",                     # nbsp
    b"\xc3\x82\xc2\xb7": b"\xc2\xb7",              # middle dot
    b"\xc3\x82\xc2\xa7": b"\xc2\xa7",              # section sign
    # Half-repaired forms, left behind by an earlier partial fix.
    b"\xe2\x82\xac\xe2\x80\x9d": b"\xe2\x80\x94",
    b"\xe2\x82\xac\xe2\x80\x9a": b"\xe2\x80\x93",
    b"\xe2\x82\xac\xe2\x80\x98": b"\xe2\x80\x98",
    b"\xe2\x82\xac\xe2\x80\x99": b"\xe2\x80\x99",
    b"\xe2\x82\xac\xc5\x93": b"\xe2\x80\x9c",
    b"\xe2\x86\x92": b"\xe2\x86\x92",
}

# Stray raw cp1252 bytes, invalid as UTF-8 on their own. Replaced with ASCII
# because the original intent is unrecoverable at this point.
STRAY_BYTES: dict[int, bytes] = {
    0x85: b"...", 0x91: b"'", 0x92: b"'", 0x93: b'"', 0x94: b'"',
    0x95: b"-", 0x96: b"-", 0x97: b"-", 0xa0: b" ",
}


def repair_bytes(data: bytes) -> bytes:
    """Fix known mojibake sequences, then drop any byte that is not valid UTF-8."""
    for _ in range(3):  # a few passes: fixing one form can expose another
        before = data
        for bad, good in MOJIBAKE_BYTES.items():
            if bad != good:
                data = data.replace(bad, good)
        if data == before:
            break

    out = bytearray()
    i = 0
    while i < len(data):
        for n in (1, 2, 3, 4):
            chunk = data[i : i + n]
            try:
                chunk.decode("utf-8")
            except UnicodeDecodeError:
                continue
            out += chunk
            i += n
            break
        else:
            out += STRAY_BYTES.get(data[i], b"-")
            i += 1
    return bytes(out)


def suspect_runs(text: str) -> list[str]:
    """Runs of two or more suspect characters - the shape mojibake takes."""
    found: list[str] = []
    run = ""
    for ch in text:
        if ch in SUSPECT_CHARS:
            run += ch
        else:
            if len(run) >= 2:
                found.append(run)
            run = ""
    if len(run) >= 2:
        found.append(run)
    return found


def candidate_files(root: pathlib.Path) -> list[pathlib.Path]:
    return [
        p for p in root.rglob("*")
        if p.is_file()
        and p.suffix in PROSE_SUFFIXES
        and p.name != SELF
        and not SKIP_DIRS & set(p.parts)
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".")
    ap.add_argument("--fix", action="store_true", help="rewrite the affected files")
    ap.add_argument("--check", action="store_true",
                    help="report only (the default; accepted for symmetry)")
    args = ap.parse_args()

    dirty: list[tuple[pathlib.Path, bool, list[str]]] = []
    still_dirty = 0

    for p in candidate_files(pathlib.Path(args.root)):
        raw = p.read_bytes()
        try:
            text = raw.decode("utf-8")
            decodes = True
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
            decodes = False
        runs = suspect_runs(text)
        if decodes and not runs:
            continue
        dirty.append((p, decodes, runs[:5]))
        if args.fix:
            fixed = repair_bytes(raw)
            p.write_bytes(fixed)
            left = suspect_runs(fixed.decode("utf-8", errors="replace"))
            still_dirty += 1 if left else 0
            print(f"  {p}: utf8={'ok' if decodes else 'was INVALID'}  "
                  f"suspect runs {len(runs)} -> {len(left)}"
                  + (f"  remaining: {left[:3]}" if left else ""))

    if not dirty:
        print("ENCODING_OK - prose files are clean UTF-8 with no mojibake")
        return 0

    if not args.fix:
        print(f"ENCODING_DIRTY - {len(dirty)} file(s):")
        for p, decodes, runs in dirty:
            print(f"  {p}  utf8={'ok' if decodes else 'INVALID'}  suspect runs: {runs}")
        print("\nre-run with --fix")
        return 1

    if still_dirty:
        print(f"\nENCODING_PARTIAL - {still_dirty} file(s) still contain suspect runs; "
              f"those sequences are not in the known-mojibake table and need "
              f"inspecting by hand")
        return 1
    print("\nENCODING_FIXED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
