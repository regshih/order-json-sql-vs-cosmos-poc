"""CONTRACT: all FOUR storage designs must return the same order.

The point of the extension is that physical storage differs while the API
response does not. That claim is only worth anything if it is enforced, so this
ingests one order per size profile into every backend and compares the responses
field by field:

    sql-hybrid            relational rows + JSON blocks   -> reassembled
    cosmos-nosql          many documents                  -> reassembled
    sql-full-json         ONE row, nvarchar(max)          -> stored whole
    cosmos-mongo          ONE BSON document               -> stored whole

sql-hybrid is the reference. Every other backend is compared against it.

What is deliberately NOT compared:

  * `parts` on a block response. It describes PHYSICAL storage - one entry per
    stored block for a decomposed backend, one entry for a full-document one.
    That difference IS the architecture under test, not a defect.
  * `payloadBytes`, for the same reason: a decomposed backend counts its blocks,
    a full-document backend counts one payload.

Requires live Azure resources and the POC VNet:

    pytest tests/contract/test_four_backend_equivalence.py -v
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from app.repositories.base import OrderSearchCriteria
from app.telemetry.metrics import RequestMetrics
from generator.synthetic_order_generator import build_order, calibrated_profiles

pytestmark = [
    pytest.mark.contract,
    pytest.mark.skipif(
        not (os.getenv("SQL_SERVER") and os.getenv("COSMOS_ENDPOINT")
             and os.getenv("MONGO_ACCOUNT")),
        reason="needs SQL_SERVER, COSMOS_ENDPOINT and MONGO_ACCOUNT (POC VNet)",
    ),
]

# Well outside the benchmark dataset range so a contract run never clobbers it.
BASE_INDEX = 940_000

# Every profile here must fit a single Cosmos NoSQL item too, because
# cosmos-nosql stores blocks and each block must clear 2 MB. p5m is included
# because the DECOMPOSED design handles it fine - it is the monolithic
# counterfactual that cannot.
PROFILE_NAMES = ["p500k", "p1m", "p1_5m", "p3m", "p5m"]

REFERENCE = "sql-hybrid"
BACKENDS = ["sql-hybrid", "cosmos-nosql", "sql-full-json", "cosmos-mongo"]


class _Null:
    def __enter__(self): return None
    def __exit__(self, *a): return False


def metrics() -> RequestMetrics:
    m = RequestMetrics(backend="contract", endpoint="/orders/{id}", operation="full")
    m.db = _Null          # type: ignore[assignment]
    m.reconstruct = _Null  # type: ignore[assignment]
    m.serialize = _Null    # type: ignore[assignment]
    return m


def make_repo(backend: str):
    if backend == "sql-hybrid":
        from app.repositories.sql_repository import SqlOrderRepository
        return SqlOrderRepository()
    if backend == "cosmos-nosql":
        from app.repositories.cosmos_repository import CosmosOrderRepository
        return CosmosOrderRepository()
    if backend == "sql-full-json":
        from app.repositories.sql_full_json_repository import SqlFullJsonRepository
        return SqlFullJsonRepository(native=False)
    if backend == "cosmos-mongo":
        from app.repositories.mongo_repository import MongoOrderRepository
        return MongoOrderRepository()
    raise AssertionError(backend)


@pytest.fixture(scope="module")
def repos():
    made = {b: make_repo(b) for b in BACKENDS}
    yield made
    for r in made.values():
        try:
            r.close()
        except Exception:
            pass


@pytest.fixture(scope="module")
def ingested(repos) -> dict[str, str]:
    """One order per profile into every backend. Returns profile -> orderId."""
    profiles = {p.name: p for p in calibrated_profiles(seed=42)}
    out: dict[str, str] = {}
    for i, name in enumerate(PROFILE_NAMES):
        doc = build_order(42, BASE_INDEX + i, profiles[name], "POCCT4")
        details = doc["ExtractData"]["ExtractObjects"][0]["ObjectDetails"]
        for b, repo in repos.items():
            repo.ingest_order(doc)
        out[name] = str(details["OrderID"]).lower()
    return out


def as_envelope(value: Any) -> dict[str, Any]:
    """Full-order responses may arrive as raw bytes (Scenario A) or a dict."""
    import orjson

    if isinstance(value, (bytes, bytearray)):
        return orjson.loads(value)
    return value


@pytest.mark.parametrize("profile", PROFILE_NAMES)
def test_full_order_is_identical_across_all_backends(repos, ingested, profile):
    """The complete logical order must come back the same from all four."""
    oid = ingested[profile]
    ref = as_envelope(repos[REFERENCE].get_full_order(oid, metrics()))
    assert ref, f"{REFERENCE} returned nothing for {profile}"

    for b in BACKENDS:
        if b == REFERENCE:
            continue
        got = as_envelope(repos[b].get_full_order(oid, metrics()))
        assert got is not None, f"{b} returned nothing for {profile}"
        assert got == ref, (
            f"{b} full order differs from {REFERENCE} for {profile}. "
            f"top-level keys ref={sorted(ref)} got={sorted(got)}"
        )


@pytest.mark.parametrize("profile", PROFILE_NAMES)
def test_summary_is_identical_across_all_backends(repos, ingested, profile):
    oid = ingested[profile]
    ref = repos[REFERENCE].get_summary(oid, metrics())
    assert ref, f"{REFERENCE} returned no summary for {profile}"
    for b in BACKENDS:
        if b == REFERENCE:
            continue
        got = repos[b].get_summary(oid, metrics())
        assert got is not None, f"{b} returned no summary for {profile}"
        # payloadBytes legitimately differs: a decomposed backend sums its
        # blocks, a full-document backend measures one payload.
        a = {k: v for k, v in ref.items() if k != "payloadBytes"}
        c = {k: v for k, v in got.items() if k != "payloadBytes"}
        assert c == a, f"{b} summary differs from {REFERENCE} for {profile}"


@pytest.mark.parametrize("block", ["TITLE", "CDF"])
def test_block_data_is_identical_across_all_backends(repos, ingested, block):
    """`data` must match. `parts` must NOT be compared - see the module docstring."""
    oid = ingested["p1m"]
    sub = "MAIN"
    ref = repos[REFERENCE].get_block(oid, block, metrics(), sub)
    assert ref, f"{REFERENCE} returned no {block} block"
    for b in BACKENDS:
        if b == REFERENCE:
            continue
        got = repos[b].get_block(oid, block, metrics(), sub)
        assert got is not None, f"{b} returned no {block} block"
        assert got["data"] == ref["data"], f"{b} {block} data differs from {REFERENCE}"
        assert got["orderId"] == ref["orderId"]
        assert got["blockType"] == ref["blockType"]


def test_full_document_backends_really_store_one_item(repos, ingested):
    """Scenario A and B must store ONE item, and say so.

    Guards the central claim of the extension. If a future change quietly split
    a full-document backend, every equivalence test above would still pass while
    the architecture under test had silently become a fifth decomposed design.
    """
    oid = ingested["p1m"]
    for b in ("sql-full-json", "cosmos-mongo"):
        blk = repos[b].get_block(oid, "TITLE", metrics(), "MAIN")
        parts = blk["parts"]
        assert len(parts) == 1, f"{b} reported {len(parts)} parts; expected exactly 1"
        assert parts[0].get("storedAsSingleDocument") is True, (
            f"{b} did not mark its response as single-document storage")

    # And the decomposed reference must NOT claim that.
    ref_parts = repos[REFERENCE].get_block(oid, "TITLE", metrics(), "MAIN")["parts"]
    assert not any(p.get("storedAsSingleDocument") for p in ref_parts)


def test_search_agrees_on_the_orders_it_finds(repos, ingested):
    """Search is backed by different indexes; the ORDER IDS must still agree."""
    crit = OrderSearchCriteria(customer_id="POCCT4", limit=50)
    ref_ids = {r["orderId"] for r in repos[REFERENCE].search_orders(crit, metrics())}
    assert ref_ids, "reference search returned nothing"
    for b in BACKENDS:
        if b == REFERENCE:
            continue
        got = {r["orderId"] for r in repos[b].search_orders(crit, metrics())}
        missing = ref_ids - got
        assert not missing, f"{b} search missed {len(missing)} order(s) the reference found"
