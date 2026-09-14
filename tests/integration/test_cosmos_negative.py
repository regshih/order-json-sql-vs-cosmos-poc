"""COSMOS NEGATIVE TEST (§18).

Two halves, run against a live Cosmos container:

  A. Monolithic design  - attempt to store a complete 3 MB / 5 MB order as ONE
                          Cosmos item and record exactly how it fails.
  B. Aggregate design   - store the SAME order decomposed into semantic
                          business documents and show it succeeds.

The point is NOT that Cosmos fails. The point is that the *monolithic model*
fails and the *aggregate model* does not, so a fair Cosmos evaluation must be
made against the aggregate design. Results are written to
results/cosmos/negative-test.json for the report to quote.

    pytest tests/integration/test_cosmos_negative.py -m integration -v -s
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from cosmos.modeling.size_guard import (
    COSMOS_HARD_MAX_BYTES,
    GuardStats,
    compact_bytes,
    enforce,
)
from generator.synthetic_order_generator import build_order, calibrated_profiles
from ingestion.parser.block_splitter import parse_envelope, reassemble, split_object_data

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("COSMOS_ENDPOINT"),
        reason="needs COSMOS_ENDPOINT (run inside the POC VNet)",
    ),
]

RESULTS = Path("results/cosmos/negative-test.json")
NEGATIVE_PROFILES = ["p1_9m", "p3m", "p5m"]
NEGATIVE_BASE_INDEX = 800_000

_findings: dict[str, Any] = {
    "generatedUtc": None,
    "cosmosHardMaxBytes": COSMOS_HARD_MAX_BYTES,
    "monolithic": [],
    "aggregate": [],
}


@pytest.fixture(scope="module")
def repo():
    from app.repositories.cosmos_repository import CosmosOrderRepository

    r = CosmosOrderRepository()
    yield r
    _findings["generatedUtc"] = datetime.now(timezone.utc).isoformat()
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(_findings, indent=2), encoding="utf-8")
    print(f"\n-> {RESULTS}")


@pytest.fixture(scope="module")
def profiles():
    return {p.name: p for p in calibrated_profiles(seed=42)}


@pytest.mark.parametrize("profile", NEGATIVE_PROFILES)
def test_a_monolithic_item_is_rejected(repo, profiles, profile: str) -> None:
    """PART A: one complete order as a single Cosmos item."""
    idx = NEGATIVE_BASE_INDEX + NEGATIVE_PROFILES.index(profile)
    doc = build_order(42, idx, profiles[profile], "POCNEG")
    env = parse_envelope(doc)
    item = {
        "id": f"{env['orderId']}:MONOLITH",
        "docType": "orderMonolithic",
        "customerId": env["customerId"],
        "orderId": env["orderId"],
        "orderVersion": env["orderVersion"],
        "data": env["objectData"],
    }
    size = compact_bytes(item)

    record: dict[str, Any] = {
        "profile": profile,
        "itemBytes": size,
        "itemMiB": round(size / 1024**2, 3),
        "overHardMax": size > COSMOS_HARD_MAX_BYTES,
    }

    outcome = "unexpected-success"
    try:
        repo.container.upsert_item(item)
        record["requestCharge"] = float(
            repo.container.client_connection.last_response_headers.get("x-ms-request-charge", 0)
        )
        outcome = "accepted"
    except Exception as exc:
        record["errorType"] = type(exc).__name__
        record["statusCode"] = getattr(exc, "status_code", None)
        record["subStatus"] = getattr(exc, "sub_status", None)
        record["message"] = str(exc)[:400]
        outcome = "rejected"

    record["outcome"] = outcome
    _findings["monolithic"].append(record)
    print(f"\n  MONOLITHIC {profile}: {size:,} B "
          f"({record['itemMiB']} MiB) -> {outcome} "
          f"{record.get('statusCode', '')} {record.get('errorType', '')}")

    if size > COSMOS_HARD_MAX_BYTES:
        assert outcome == "rejected", (
            f"an item of {size:,} B exceeds the documented "
            f"{COSMOS_HARD_MAX_BYTES:,} B maximum but was accepted"
        )
    else:
        # Below the limit the monolith is *accepted* - which is itself a
        # finding: the limit, not the design, is what bites.
        assert outcome == "accepted", record.get("message")


@pytest.mark.parametrize("profile", NEGATIVE_PROFILES)
def test_b_aggregate_decomposition_succeeds(repo, profiles, profile: str) -> None:
    """PART B: the SAME order, decomposed into semantic business documents."""
    idx = NEGATIVE_BASE_INDEX + NEGATIVE_PROFILES.index(profile)
    doc = build_order(42, idx, profiles[profile], "POCNEG")
    env = parse_envelope(doc)
    source_bytes = compact_bytes(doc)

    blocks = split_object_data(env["objectData"])
    stats = GuardStats()
    guarded = enforce(blocks, budget=repo.item_budget, stats=stats)

    result = repo.ingest_order(doc)

    # Read it back and prove nothing was lost.
    from app.telemetry.metrics import measure

    with measure("cosmos", "negative", "full") as m:
        back = repo.get_full_order(env["orderId"], m)
    assert back is not None
    assert back["ExtractData"]["ExtractObjects"][0]["ObjectData"] == env["objectData"], (
        f"aggregate round-trip lost data for {profile}"
    )

    record = {
        "profile": profile,
        "sourceBytes": source_bytes,
        "sourceMiB": round(source_bytes / 1024**2, 3),
        "logicalBlocks": stats.blocks_in,
        "itemsWritten": result.items_written,
        "blocksChunked": stats.blocks_chunked,
        "maxItemBytes": stats.max_item_bytes,
        "maxItemMiB": round(stats.max_item_bytes / 1024**2, 3),
        "maxItemPctOfLimit": round(stats.max_item_bytes / COSMOS_HARD_MAX_BYTES * 100, 1),
        "writeRu": result.request_charge,
        "writeMs": round(result.duration_ms, 1),
        "readRu": round(m.request_charge, 2),
        "readMs": round(m.total_ms, 1),
        "roundTripLossless": True,
        "outcome": "succeeded",
    }
    _findings["aggregate"].append(record)
    print(f"\n  AGGREGATE  {profile}: {source_bytes:,} B -> {result.items_written} items, "
          f"largest {stats.max_item_bytes:,} B ({record['maxItemPctOfLimit']}% of limit), "
          f"write {result.request_charge:.0f} RU, read {m.request_charge:.1f} RU")

    assert stats.max_item_bytes < COSMOS_HARD_MAX_BYTES
    assert result.items_written > 1


@pytest.mark.parametrize("profile", ["p1m", "p1_9m"])
def test_b2_monolithic_point_read_is_cheaper_in_ru(repo, profiles, profile: str) -> None:
    """Quantify the RU price of decomposition, for orders that FIT in one item.

    The aggregate model is mandatory above the 2 MB limit, but below it the two
    models can be compared directly. A point read of one item is charged very
    differently from a query that returns the same bytes across ~32 items, and
    the difference decides whether the API should serve whole orders at all.
    """
    idx = 810_000 + ["p1m", "p1_9m"].index(profile)
    doc = build_order(42, idx, profiles[profile], "POCNEG2")
    env = parse_envelope(doc)
    source_bytes = compact_bytes(doc)

    mono = {
        "id": f"{env['orderId']}:MONO2",
        "docType": "orderMonolithic",
        "customerId": env["customerId"],
        "orderId": env["orderId"],
        "orderVersion": env["orderVersion"],
        "data": env["objectData"],
    }
    mono_bytes = compact_bytes(mono)
    if mono_bytes > COSMOS_HARD_MAX_BYTES:
        pytest.skip(f"{profile} is {mono_bytes:,} B - above the item limit, not comparable")

    repo.container.upsert_item(mono)
    write_ru = float(
        repo.container.client_connection.last_response_headers.get("x-ms-request-charge", 0))

    # Monolithic: one point read.
    t0 = time.time()
    repo.container.read_item(item=mono["id"], partition_key=[env["customerId"], env["orderId"]])
    mono_read_ms = (time.time() - t0) * 1000
    mono_read_ru = float(
        repo.container.client_connection.last_response_headers.get("x-ms-request-charge", 0))

    # Aggregate: the decomposed form, read back the way the API does it.
    agg = repo.ingest_order(doc)
    from app.telemetry.metrics import measure

    with measure("cosmos", "negative", "full") as m:
        back = repo.get_full_order(env["orderId"], m)
    assert back is not None

    record = {
        "profile": profile,
        "sourceBytes": source_bytes,
        "monolithicItemBytes": mono_bytes,
        "monolithicWriteRu": round(write_ru, 2),
        "monolithicPointReadRu": round(mono_read_ru, 2),
        "monolithicPointReadMs": round(mono_read_ms, 1),
        "aggregateItems": agg.items_written,
        "aggregateWriteRu": round(agg.request_charge, 2),
        "aggregateReadRu": round(m.request_charge, 2),
        "aggregateReadMs": round(m.total_ms, 1),
        "readRuRatio": round(m.request_charge / max(mono_read_ru, 0.01), 1),
        "writeRuRatio": round(agg.request_charge / max(write_ru, 0.01), 1),
    }
    _findings.setdefault("monolithicVsAggregateRu", []).append(record)
    print(f"\n  RU COMPARISON {profile} ({source_bytes:,} B):")
    print(f"    monolithic point read : {mono_read_ru:>9.1f} RU  {mono_read_ms:>7.1f} ms")
    print(f"    aggregate full read   : {m.request_charge:>9.1f} RU  {m.total_ms:>7.1f} ms "
          f"({record['readRuRatio']}x the RU)")
    print(f"    monolithic write      : {write_ru:>9.1f} RU")
    print(f"    aggregate write       : {agg.request_charge:>9.1f} RU "
          f"({record['writeRuRatio']}x the RU)")


def test_c_conclusion_is_recorded() -> None:
    """Assemble the finding the report quotes, from the measured records."""
    mono = _findings["monolithic"]
    agg = _findings["aggregate"]
    if not mono or not agg:
        pytest.skip("run the monolithic and aggregate tests first")

    rejected = [m for m in mono if m["outcome"] == "rejected"]
    accepted = [m for m in mono if m["outcome"] == "accepted"]
    _findings["conclusion"] = {
        "monolithicRejected": len(rejected),
        "monolithicAccepted": len(accepted),
        "monolithicRejectionThresholdBytes": COSMOS_HARD_MAX_BYTES,
        "aggregateAllSucceeded": all(a["outcome"] == "succeeded" for a in agg),
        "largestAggregateItemBytes": max(a["maxItemBytes"] for a in agg),
        "largestAggregateItemPctOfLimit": max(a["maxItemPctOfLimit"] for a in agg),
        "anyChunkingRequired": any(a["blocksChunked"] > 0 for a in agg),
        "statement": (
            "The monolithic single-item model fails above the documented item-size "
            "limit. The semantic aggregate model stores the same orders with the "
            "largest item at "
            f"{max(a['maxItemPctOfLimit'] for a in agg)}% of the limit and no "
            "array chunking required. The item-size limit is therefore a constraint "
            "on the DOCUMENT MODEL, not a disqualifier for Cosmos DB."
        ),
    }
    assert _findings["conclusion"]["aggregateAllSucceeded"]
