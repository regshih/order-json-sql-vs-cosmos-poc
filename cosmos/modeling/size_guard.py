"""Cosmos DB item size guard.

Enforces a configurable serialized-UTF-8 budget *before* an item is sent to
Cosmos, and splits an oversized logical block using a strict escalation:

  1. Business-domain split   -- already performed by the block splitter
                                (TITLE -> COMMITMENTS / POLICIES / PRODUCTS / ...).
  2. Array-element split     -- chunk the largest array in the block by whole
                                elements, never mid-object.
  3. Refuse                  -- if a *single indivisible element* exceeds the
                                budget we raise, rather than silently writing
                                something Cosmos will reject.

The guard never splits serialized JSON text. Every chunk is itself a valid JSON
document and the set is reassembled by
``ingestion.parser.block_splitter.merge_chunks``.

The hard service maximum is read from config so it tracks the documented limit;
``COSMOS_ITEM_TARGET_BYTES`` is the conservative working budget below it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from ingestion.parser.block_splitter import Block

# Documented Cosmos DB for NoSQL hard maximum item size. Verified in
# docs/SOURCES.md; overridable so the guard tracks the product rather than
# this file.
COSMOS_HARD_MAX_BYTES = int(os.getenv("COSMOS_HARD_MAX_BYTES", 2 * 1024 * 1024))

# Conservative working budget. Leaves room for the item envelope Cosmos adds
# (_rid/_self/_etag/_attachments/_ts ≈ 400 B) plus our own routing metadata,
# and for UTF-8 expansion between our accounting and the service's.
COSMOS_ITEM_TARGET_BYTES = int(os.getenv("COSMOS_ITEM_TARGET_BYTES", 1_800_000))

# Bytes reserved for the item envelope we wrap around ``data``.
ENVELOPE_RESERVE_BYTES = 2_048


class ItemTooLargeError(ValueError):
    """A single indivisible element exceeds the item budget."""


@dataclass
class GuardStats:
    blocks_in: int = 0
    items_out: int = 0
    blocks_chunked: int = 0
    max_item_bytes: int = 0
    total_bytes: int = 0
    refusals: int = 0


def compact_bytes(obj: Any) -> int:
    return len(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def enforce(
    blocks: list[Block],
    budget: int | None = None,
    stats: GuardStats | None = None,
) -> list[Block]:
    """Return blocks guaranteed to serialize under ``budget``.

    Blocks already within budget pass through untouched (chunk_count == 1).
    """
    budget = budget or COSMOS_ITEM_TARGET_BYTES
    effective = budget - ENVELOPE_RESERVE_BYTES
    if effective <= 0:
        raise ValueError(f"budget {budget} is smaller than the envelope reserve")

    out: list[Block] = []
    for b in blocks:
        if stats:
            stats.blocks_in += 1
        if b.payload_bytes <= effective:
            out.append(b)
        else:
            chunks = _chunk_block_recursive(b, effective)
            if stats:
                stats.blocks_chunked += 1
            out.extend(chunks)

    if stats:
        stats.items_out += len(out)
        for b in out:
            stats.max_item_bytes = max(stats.max_item_bytes, b.payload_bytes)
            stats.total_bytes += b.payload_bytes
    return out


def _find_largest_array(node: Any, path: tuple[str, ...] = ()) -> tuple[tuple[str, ...], list] | None:
    """Locate the largest array anywhere in the payload.

    Real blocks nest one or two levels (e.g. OriginationChargeSection -> Lines),
    so a top-level-only search would wrongly conclude a block is indivisible.
    """
    best: tuple[tuple[str, ...], list] | None = None
    best_bytes = -1

    def walk(n: Any, p: tuple[str, ...]) -> None:
        nonlocal best, best_bytes
        if isinstance(n, dict):
            for k, v in n.items():
                walk(v, p + (k,))
        elif isinstance(n, list):
            if n:
                b = compact_bytes(n)
                if b > best_bytes:
                    best, best_bytes = (p, n), b
            # Arrays of objects may themselves contain larger arrays.
            for i, e in enumerate(n):
                if isinstance(e, (dict, list)):
                    walk(e, p + (f"[{i}]",))

    walk(node, path)
    return best


def _skeleton(path: tuple[str, ...], value: Any) -> dict[str, Any]:
    """Rebuild the nested dict shell down to ``path`` holding ``value``.

    Only used for index-free paths; a path containing an ``[i]`` segment means
    the array lives inside another array's element, which we do not chunk.
    """
    out: Any = value
    for key in reversed(path):
        out = {key: out}
    return out


def _replace_at(node: Any, path: tuple[str, ...], value: Any) -> Any:
    """Copy ``node`` with the array at ``path`` replaced by ``value``."""
    if not path:
        return value
    key, rest = path[0], path[1:]
    if isinstance(node, dict):
        return {k: (_replace_at(v, rest, value) if k == key else v) for k, v in node.items()}
    return node


def _chunk_block(block: Block, effective: int) -> list[Block]:
    """Split one oversized block by array elements.

    Chooses the largest array anywhere in the payload as the split axis and
    slices it by whole elements. Chunk 0 carries the rest of the block; later
    chunks carry only the nested shell plus their slice, so the deep merge in
    ``merge_chunks`` reconstructs the original exactly.
    """
    found = _find_largest_array(block.payload)
    if found is None:
        raise ItemTooLargeError(
            f"block {block.block_type}/{block.block_sub_type}/{block.sequence} is "
            f"{block.payload_bytes:,} B (budget {effective:,} B) and contains no "
            f"splittable array. Business-domain decomposition must be extended."
        )

    path, split_list = found
    if any(seg.startswith("[") for seg in path):
        raise ItemTooLargeError(
            f"block {block.block_type}/{block.block_sub_type}/{block.sequence} is "
            f"{block.payload_bytes:,} B and its largest array is nested inside "
            f"another array ({'.'.join(path)}). Business-domain decomposition "
            f"must be extended."
        )

    # Bytes of everything except the split array, measured on the real shape.
    without = _replace_at(block.payload, path, [])
    other_bytes = compact_bytes(without)
    shell_bytes = compact_bytes(_skeleton(path, []))

    chunks: list[list[Any]] = []
    current: list[Any] = []
    current_bytes = other_bytes
    for element in split_list:
        e_bytes = compact_bytes(element) + 1  # +1 for the separating comma
        if e_bytes + shell_bytes + 32 > effective:
            raise ItemTooLargeError(
                f"single element of {block.block_type}/{block.block_sub_type}."
                f"{'.'.join(path)} is {e_bytes:,} B and cannot fit the "
                f"{effective:,} B budget. Element-level decomposition required."
            )
        if current and current_bytes + e_bytes > effective:
            chunks.append(current)
            current, current_bytes = [], shell_bytes
        current.append(element)
        current_bytes += e_bytes
    if current:
        chunks.append(current)

    total = len(chunks)
    out: list[Block] = []
    for i, elements in enumerate(chunks):
        payload = _replace_at(block.payload, path, elements) if i == 0 else _skeleton(path, elements)
        out.append(
            Block(
                block_type=block.block_type,
                block_sub_type=block.block_sub_type,
                sequence=block.sequence,
                payload=payload,
                chunk_index=i,
                chunk_count=total,
                chunk_of_path=".".join(path),
            )
        )
    return out


def _chunk_block_recursive(block: Block, effective: int, depth: int = 0) -> list[Block]:
    """Chunk until every part fits.

    One pass splits on the single largest array. A block holding several large
    arrays (a CDF sub-block with more than one populated section, say) can leave
    the remainder still over budget, so each produced part is re-checked and
    split again on its own largest array.

    All leaf parts are renumbered into one flat 0..N-1 sequence. Reassembly is a
    deep merge in index order, which is order-correct at every level because a
    part only ever carries its own slice.
    """
    if depth > 6:
        raise ItemTooLargeError(
            f"block {block.block_type}/{block.block_sub_type}/{block.sequence} still "
            f"exceeds {effective:,} B after 6 rounds of array splitting"
        )

    parts = _chunk_block(block, effective)
    leaves: list[Block] = []
    for part in parts:
        if part.payload_bytes <= effective:
            leaves.append(part)
        else:
            leaves.extend(_chunk_block_recursive(part, effective, depth + 1))

    total = len(leaves)
    for i, leaf in enumerate(leaves):
        leaf.chunk_index = i
        leaf.chunk_count = total
        leaf._compact = None
    return leaves


def check_item(item: dict[str, Any], budget: int | None = None) -> int:
    """Final belt-and-braces check on the fully-formed Cosmos item.

    Returns the serialized size. Raises if it would exceed the hard service
    maximum - which should be unreachable if ``enforce`` was used.
    """
    budget = budget or COSMOS_ITEM_TARGET_BYTES
    n = compact_bytes(item)
    if n > COSMOS_HARD_MAX_BYTES:
        raise ItemTooLargeError(
            f"item {item.get('id')} is {n:,} B, above the Cosmos hard maximum of "
            f"{COSMOS_HARD_MAX_BYTES:,} B"
        )
    return n
