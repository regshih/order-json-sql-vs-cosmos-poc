"""CONTRACT: API(SQL) and API(Cosmos) must be semantically equivalent.

The same synthetic logical order is ingested into both backends, then every
response is compared. Repositories are exercised in-process (rather than over
HTTP) so both backends can be compared inside one test run, and because the
endpoint layer above them is literally the same code either way.

Requires live Azure resources and must run from inside the POC VNet:

    pytest tests/contract -m contract -v
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from app.repositories.base import OrderSearchCriteria
from app.telemetry.metrics import RequestMetrics, measure
from generator.synthetic_order_generator import build_order, calibrated_profiles

pytestmark = [
    pytest.mark.contract,
    pytest.mark.skipif(
        not (os.getenv("SQL_SERVER") and os.getenv("COSMOS_ENDPOINT")),
        reason="needs SQL_SERVER and COSMOS_ENDPOINT (run inside the POC VNet)",
    ),
]

# One order per size profile, using indices well outside the benchmark dataset
# range so a contract run never overwrites benchmark data.
CONTRACT_BASE_INDEX = 900_000
PROFILE_NAMES = ["p500k", "p1m", "p1_5m", "p1_9m", "p3m", "p5m"]


@pytest.fixture(scope="module")
def repos():
    from app.repositories.cosmos_repository import CosmosOrderRepository
    from app.repositories.sql_repository import SqlOrderRepository

    sql = SqlOrderRepository()
    cosmos = CosmosOrderRepository()
    yield sql, cosmos
    sql.close()
    cosmos.close()


@pytest.fixture(scope="module")
def ingested(repos) -> dict[str, dict[str, Any]]:
    """Ingest one order per size profile into BOTH backends."""
    sql, cosmos = repos
    profiles = {p.name: p for p in calibrated_profiles(seed=42)}
    out: dict[str, dict[str, Any]] = {}
    for i, name in enumerate(PROFILE_NAMES):
        doc = build_order(42, CONTRACT_BASE_INDEX + i, profiles[name], "POCCT1")
        details = doc["ExtractData"]["ExtractObjects"][0]["ObjectDetails"]
        sql_res = sql.ingest_order(doc)
        cos_res = cosmos.ingest_order(doc)
        out[name] = {
            "orderId": details["OrderID"],
            "doc": doc,
            "sqlBlocks": sql_res.blocks_written,
            "cosmosItems": cos_res.items_written,
            "cosmosRu": cos_res.request_charge,
            "cosmosChunked": cos_res.chunked_blocks,
            "maxItemBytes": cos_res.max_item_bytes,
            "sourceBytes": len(json.dumps(doc, separators=(",", ":")).encode()),
        }
    return out


def _m() -> RequestMetrics:
    """A metrics object with working timers, outside a real request."""
    with measure("test", "test", "test") as m:
        return m


@pytest.mark.parametrize("profile", PROFILE_NAMES)
def test_summary_is_equivalent(repos, ingested, profile: str) -> None:
    sql, cosmos = repos
    oid = ingested[profile]["orderId"]
    a = sql.get_summary(oid, _m())
    b = cosmos.get_summary(oid, _m())
    assert a is not None and b is not None
    # payloadBytes is a storage-layer statistic, not business data: SQL sums
    # its JSON block rows, Cosmos sums its items. Compare everything else.
    a.pop("payloadBytes", None)
    b.pop("payloadBytes", None)
    assert a == b, f"summary differs for {profile}"


@pytest.mark.parametrize("profile", PROFILE_NAMES)
def test_full_order_is_equivalent(repos, ingested, profile: str) -> None:
    sql, cosmos = repos
    oid = ingested[profile]["orderId"]
    a = sql.get_full_order(oid, _m())
    b = cosmos.get_full_order(oid, _m())
    assert a is not None and b is not None
    assert a["ExtractData"] == b["ExtractData"], f"ObjectData differs for {profile}"


@pytest.mark.parametrize("profile", PROFILE_NAMES)
def test_full_order_round_trips_the_source(repos, ingested, profile: str) -> None:
    """Stronger than equivalence: each backend must return exactly what was
    ingested, so neither is 'equally wrong'."""
    sql, cosmos = repos
    rec = ingested[profile]
    source_od = rec["doc"]["ExtractData"]["ExtractObjects"][0]["ObjectData"]
    for repo in (sql, cosmos):
        got = repo.get_full_order(rec["orderId"], _m())
        od = got["ExtractData"]["ExtractObjects"][0]["ObjectData"]
        assert od == source_od, f"{repo.backend} lost data for {profile}"


@pytest.mark.parametrize("block", ["TITLE", "CDF", "NOTES", "PARTIES"])
def test_blocks_are_equivalent(repos, ingested, block: str) -> None:
    sql, cosmos = repos
    oid = ingested["p1m"]["orderId"]
    sub = "TASKS" if block == "CHECKLIST" else None
    a = sql.get_block(oid, block, _m(), sub)
    b = cosmos.get_block(oid, block, _m(), sub)
    assert a is not None and b is not None
    assert a["data"] == b["data"], f"{block} data differs"
    assert a["orderId"] == b["orderId"]
    assert a["orderVersion"] == b["orderVersion"]
    assert a["customerId"] == b["customerId"]
    # Part metadata may legitimately differ in count when Cosmos chunked a
    # block, but the union of sub-types must match.
    assert {p["blockSubType"] for p in a["parts"]} == {p["blockSubType"] for p in b["parts"]}


def test_search_is_equivalent(repos, ingested) -> None:
    sql, cosmos = repos
    for criteria in (
        OrderSearchCriteria(customer_id="POCCT1", limit=50),
        OrderSearchCriteria(customer_id="POCCT1", status="Closed", limit=50),
        OrderSearchCriteria(customer_id="POCCT1", min_loan_amount=100_000, limit=50),
    ):
        a = sql.search_orders(criteria, _m())
        b = cosmos.search_orders(criteria, _m())
        ka = sorted((r["orderId"], r["status"], r["state"]) for r in a)
        kb = sorted((r["orderId"], r["status"], r["state"]) for r in b)
        assert ka == kb, f"search результат differs for {criteria}"


@pytest.mark.parametrize("profile", PROFILE_NAMES)
def test_no_logical_section_is_lost_by_either_backend(repos, ingested, profile: str) -> None:
    """Regression test (§30): section set must be identical to the source."""
    sql, cosmos = repos
    rec = ingested[profile]
    expected = set(rec["doc"]["ExtractData"]["ExtractObjects"][0]["ObjectData"])
    for repo in (sql, cosmos):
        od = repo.get_full_order(rec["orderId"], _m())["ExtractData"]["ExtractObjects"][0]["ObjectData"]
        assert set(od) == expected, f"{repo.backend} section mismatch: {set(od) ^ expected}"


def test_size_profiles_span_the_required_range(ingested) -> None:
    """SIZE TESTS (§30): 500 KB .. 5 MB all ingest and read back."""
    sizes = {name: rec["sourceBytes"] for name, rec in ingested.items()}
    assert sizes["p500k"] < 700_000
    assert 2_800_000 < sizes["p3m"] < 3_600_000
    assert sizes["p5m"] > 4_500_000


def test_cosmos_items_stay_within_the_limit(ingested) -> None:
    for name, rec in ingested.items():
        assert rec["maxItemBytes"] < 2 * 1024 * 1024, f"{name} produced an over-limit item"
