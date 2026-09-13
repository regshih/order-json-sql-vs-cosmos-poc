"""Split / reassemble must be lossless for every size profile and for the
real customer sample when it is present locally."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from generator.synthetic_order_generator import build_order, calibrated_profiles
from ingestion.parser.block_splitter import (
    Block,
    build_envelope,
    merge_chunks,
    parse_envelope,
    reassemble,
    split_object_data,
)

SAMPLE = Path("data/source/150159_Order.MASKED.json.json")
PROFILES = {p.name: p for p in calibrated_profiles(seed=42)}
ALL_PROFILE_NAMES = ["p500k", "p1m", "p1_5m", "p1_9m", "p3m", "p5m"]


def _object_data(profile: str, index: int = 0):
    doc = build_order(42, index, PROFILES[profile], "POC001")
    return parse_envelope(doc)["objectData"]


@pytest.mark.parametrize("profile", ALL_PROFILE_NAMES)
def test_split_reassemble_is_lossless(profile: str) -> None:
    od = _object_data(profile)
    assert reassemble(split_object_data(od)) == od


@pytest.mark.parametrize("profile", ALL_PROFILE_NAMES)
def test_no_logical_section_is_dropped(profile: str) -> None:
    """Regression guard (§30): reconstruction must not silently lose a section."""
    od = _object_data(profile)
    back = reassemble(split_object_data(od))
    assert set(back) == set(od), f"sections differ: {set(od) ^ set(back)}"


@pytest.mark.parametrize("profile", ALL_PROFILE_NAMES)
def test_blocks_use_business_boundaries(profile: str) -> None:
    blocks = split_object_data(_object_data(profile))
    types = {b.block_type for b in blocks}
    # The sections that dominate payload size must each be their own block.
    assert {"CDF", "TITLE", "PARTIES", "NOTES", "CHECKLIST", "ORDER"} <= types
    # No block may be an anonymous byte slice.
    assert all(b.block_sub_type and not b.block_sub_type.isdigit() for b in blocks)


def test_cdf_and_title_are_subdivided() -> None:
    blocks = split_object_data(_object_data("p1m"))
    cdf_subs = {b.block_sub_type for b in blocks if b.block_type == "CDF"}
    title_subs = {b.block_sub_type for b in blocks if b.block_type == "TITLE"}
    assert {"ORIGINATION", "SERVICES", "DUE_FROM_BUYER", "DISBURSEMENTS"} <= cdf_subs
    assert {"COMMITMENTS", "POLICIES", "PRODUCTS"} <= title_subs


def test_block_hash_and_size_are_consistent() -> None:
    for b in split_object_data(_object_data("p1m")):
        assert b.payload_bytes == len(
            json.dumps(b.payload, separators=(",", ":"), ensure_ascii=False).encode()
        )
        assert len(b.payload_hash) == 64


def test_merge_chunks_rejects_incomplete_sets() -> None:
    parts = [
        Block("TITLE", "COMMITMENTS", 0, {"Commitments": [1, 2]}, chunk_index=0, chunk_count=3),
        Block("TITLE", "COMMITMENTS", 0, {"Commitments": [3]}, chunk_index=1, chunk_count=3),
    ]
    with pytest.raises(ValueError, match="incomplete chunk set"):
        merge_chunks(parts)


def test_envelope_round_trip() -> None:
    doc = build_order(42, 3, PROFILES["p1m"], "POC007")
    env = parse_envelope(doc)
    rebuilt = build_envelope(
        env["customerId"], env["orderId"], env["orderVersion"],
        reassemble(split_object_data(env["objectData"])),
        extract_timestamp=env["extractTimestamp"],
        extract_server=env["extractServer"],
        extract_type=env["extractType"],
    )
    assert rebuilt == doc


def test_parse_envelope_rejects_unknown_shape() -> None:
    with pytest.raises(ValueError, match="unrecognised extract envelope"):
        parse_envelope({"NotAnExtract": True})


@pytest.mark.skipif(not SAMPLE.exists(), reason="customer sample not present locally")
def test_real_sample_round_trips() -> None:
    """The sample is git-ignored; this test runs only where it exists."""
    doc = json.loads(SAMPLE.read_text(encoding="utf-8-sig"))
    od = parse_envelope(doc)["objectData"]
    blocks = split_object_data(od)
    assert reassemble(blocks) == od
    assert len(blocks) >= 20
