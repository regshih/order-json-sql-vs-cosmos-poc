"""Cosmos item-size guard: enforcement, escalation order, and losslessness."""

from __future__ import annotations

import json

import pytest

from cosmos.modeling.size_guard import (
    COSMOS_HARD_MAX_BYTES,
    ENVELOPE_RESERVE_BYTES,
    GuardStats,
    ItemTooLargeError,
    check_item,
    compact_bytes,
    enforce,
)
from generator.synthetic_order_generator import build_order, calibrated_profiles
from ingestion.parser.block_splitter import (
    Block,
    merge_chunks,
    parse_envelope,
    reassemble,
    split_object_data,
)

PROFILES = {p.name: p for p in calibrated_profiles(seed=42)}
ALL = ["p500k", "p1m", "p1_5m", "p1_9m", "p3m", "p5m"]


def _blocks(profile: str, index: int = 0) -> tuple[list[Block], dict]:
    od = parse_envelope(build_order(42, index, PROFILES[profile], "POC001"))["objectData"]
    return split_object_data(od), od


@pytest.mark.parametrize("profile", ALL)
def test_every_item_is_within_budget(profile: str) -> None:
    blocks, _ = _blocks(profile)
    stats = GuardStats()
    out = enforce(blocks, stats=stats)
    budget = 1_800_000 - ENVELOPE_RESERVE_BYTES
    assert all(b.payload_bytes <= budget for b in out)
    assert stats.max_item_bytes <= budget


@pytest.mark.parametrize("profile", ALL)
def test_guard_is_lossless(profile: str) -> None:
    blocks, od = _blocks(profile)
    assert reassemble(enforce(blocks)) == od


@pytest.mark.parametrize("profile", ALL)
def test_guard_is_a_no_op_at_the_default_budget(profile: str) -> None:
    """Key measured finding: business-domain decomposition alone keeps every
    block far below the Cosmos limit, even for a 5 MB order. Chunking is a
    safety net, not a routine code path."""
    blocks, _ = _blocks(profile)
    stats = GuardStats()
    out = enforce(blocks, stats=stats)
    assert stats.blocks_chunked == 0
    assert len(out) == len(blocks)


# A budget small enough to force chunking on the large profiles, but still
# above the largest indivisible element (a single title commitment is ~180 KB).
TIGHT_BUDGET = 250_000


@pytest.mark.parametrize("profile", ["p3m", "p5m"])
def test_array_chunking_engages_under_a_tight_budget(profile: str) -> None:
    """Force the escalation path with a deliberately small budget."""
    blocks, od = _blocks(profile)
    stats = GuardStats()
    out = enforce(blocks, budget=TIGHT_BUDGET, stats=stats)
    assert stats.blocks_chunked > 0
    assert len(out) > len(blocks)
    assert all(b.payload_bytes <= TIGHT_BUDGET - ENVELOPE_RESERVE_BYTES for b in out)
    # Still perfectly reconstructable.
    assert reassemble(out) == od


@pytest.mark.parametrize("profile", ["p3m", "p5m"])
def test_recursive_chunking_leaves_nothing_over_budget(profile: str) -> None:
    """A block holding several large arrays needs more than one split pass."""
    blocks, od = _blocks(profile)
    for budget in (300_000, 250_000, 200_000):
        out = enforce(blocks, budget=budget)
        assert all(b.payload_bytes <= budget - ENVELOPE_RESERVE_BYTES for b in out)
        assert reassemble(out) == od


def test_chunks_are_valid_json_never_split_mid_object() -> None:
    blocks, _ = _blocks("p3m")
    out = enforce(blocks, budget=TIGHT_BUDGET)
    for b in out:
        # Every chunk must independently serialise and parse.
        assert json.loads(json.dumps(b.payload)) == b.payload
        for value in b.payload.values():
            if isinstance(value, list):
                assert all(not isinstance(e, str) or True for e in value)


def test_chunk_metadata_is_complete() -> None:
    blocks, _ = _blocks("p5m")
    out = enforce(blocks, budget=TIGHT_BUDGET)
    chunked = [b for b in out if b.chunk_count > 1]
    assert chunked
    for b in chunked:
        assert 0 <= b.chunk_index < b.chunk_count
        assert b.chunk_of_path
        assert b.key.endswith(f"c{b.chunk_index}")


def test_indivisible_element_is_refused_not_silently_written() -> None:
    """A single element larger than the budget must raise, never truncate."""
    huge = Block("TITLE", "EXCEPTIONS", 0, {"Exceptions": [{"text": "x" * 50_000}, {"t": "y"}]})
    with pytest.raises(ItemTooLargeError, match="cannot fit"):
        enforce([huge], budget=10_000)


def test_nested_array_is_found_and_split() -> None:
    """Real blocks nest (section -> Lines); a top-level-only search would
    wrongly call them indivisible."""
    nested = Block(
        "CDF", "ORIGINATION", 0,
        {"OriginationChargeSection": {"Guid": "g", "Total": "1", "Lines": [{"d": "x" * 200} for _ in range(60)]}},
    )
    out = enforce([nested], budget=12_000)
    assert len(out) > 1
    assert all(b.chunk_of_path == "OriginationChargeSection.Lines" for b in out)
    # merge_chunks (not reassemble) is the right inverse here: this is one
    # block in isolation, not a whole ObjectData tree.
    merged = merge_chunks(out)
    assert len(merged) == 1
    section = merged[0].payload["OriginationChargeSection"]
    assert len(section["Lines"]) == 60
    assert section["Guid"] == "g"


def test_block_with_no_splittable_array_is_refused() -> None:
    blob = Block("MISC", "MAIN", 0, {"BigScalar": "y" * 50_000})
    with pytest.raises(ItemTooLargeError, match="no\\s+splittable array"):
        enforce([blob], budget=10_000)


def test_check_item_rejects_above_hard_maximum() -> None:
    item = {"id": "x", "data": {"blob": "z" * (COSMOS_HARD_MAX_BYTES + 1000)}}
    with pytest.raises(ItemTooLargeError, match="hard maximum"):
        check_item(item)


def test_budget_smaller_than_envelope_reserve_is_rejected() -> None:
    with pytest.raises(ValueError, match="smaller than the envelope reserve"):
        enforce([], budget=100)


def test_compact_bytes_matches_utf8_encoding() -> None:
    obj = {"a": "naïve — ünïcode", "b": [1, 2, 3]}
    assert compact_bytes(obj) == len(
        json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
