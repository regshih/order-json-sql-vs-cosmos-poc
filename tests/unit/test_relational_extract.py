"""Relational projection: field selection, money parsing, key uniqueness."""

from __future__ import annotations

import pytest

from generator.synthetic_order_generator import build_order, calibrated_profiles
from ingestion.parser.block_splitter import parse_envelope
from ingestion.parser.relational_extract import (
    extract,
    summary_from_projection,
    to_datetime,
    to_decimal,
)

PROFILES = {p.name: p for p in calibrated_profiles(seed=42)}


def _proj(profile: str = "p1m", index: int = 0):
    return extract(parse_envelope(build_order(42, index, PROFILES[profile], "POC001")))


@pytest.mark.parametrize("profile", ["p500k", "p1m", "p3m", "p5m"])
def test_projection_has_all_entities(profile: str) -> None:
    p = _proj(profile)
    assert p["order"]["OrderId"]
    assert p["order"]["CustomerId"] == "POC001"
    assert p["properties"] and p["loans"] and p["parties"] and p["orderParties"]
    assert p["searchFields"]["state"]


@pytest.mark.parametrize("profile", ["p500k", "p1m", "p1_5m", "p1_9m", "p3m", "p5m"])
def test_primary_keys_are_unique_within_an_order(profile: str) -> None:
    """Source GUIDs are not guaranteed unique; the projection must still produce
    usable primary keys or SQL ingestion aborts on a PK violation."""
    p = _proj(profile)
    for entity, key in (("properties", "PropertyId"), ("loans", "LoanId"), ("parties", "PartyId")):
        ids = [r[key] for r in p[entity]]
        assert len(ids) == len(set(ids)), f"duplicate {key} in {entity} for {profile}"


def test_duplicate_source_guids_resolve_to_distinct_keys() -> None:
    """Two loans sharing one Guid must still yield two distinct LoanIds."""
    dup = "eba7af65-22ee-4d6d-8724-ad596fa3a8c9"
    envelope = {
        "customerId": "POC001",
        "orderId": "11111111-2222-3333-4444-555555555555",
        "orderVersion": 1,
        "extractTimestamp": None,
        "objectData": {
            "Loans": [
                {"Guid": dup, "Amount": "100.00", "Number": "A"},
                {"Guid": dup, "Amount": "200.00", "Number": "B"},
            ],
            "Properties": [
                {"Guid": dup, "Address": {}},
                {"Guid": dup, "Address": {}},
            ],
        },
    }
    p = extract(envelope)
    assert len({l["LoanId"] for l in p["loans"]}) == 2
    assert len({r["PropertyId"] for r in p["properties"]}) == 2
    # The first row keeps the natural key; only the collision is rewritten.
    assert p["loans"][0]["LoanId"] == dup


def test_money_is_quantised_to_cents() -> None:
    p = _proj("p3m")
    for key in ("maxLoanAmount", "totalLoanAmount"):
        v = p["searchFields"][key]
        if v is not None:
            assert round(v, 2) == v, f"{key} not quantised: {v!r}"


def test_to_decimal_distinguishes_absent_from_zero() -> None:
    assert to_decimal("") is None
    assert to_decimal(None) is None
    assert to_decimal("not money") is None
    assert to_decimal("0.00") == 0.0
    assert to_decimal("1,234.56") == 1234.56
    assert to_decimal("$1,234.56") == 1234.56
    assert to_decimal("-99.10") == -99.10


def test_to_datetime_handles_source_formats() -> None:
    assert to_datetime("2026-01-15T10:30:00").year == 2026
    assert to_datetime("2026-01-15").month == 1
    assert to_datetime("") is None
    assert to_datetime("garbage") is None


def test_summary_shape_is_stable() -> None:
    s = summary_from_projection(_proj("p1m"), payload_bytes=123)
    expected = {
        "orderId", "customerId", "orderVersion", "orderNumber", "orderType", "status",
        "project", "settlementType", "isCommercial", "isRush", "balance", "createdDate",
        "settlementDate", "disbursementDate", "property", "counts", "loanTotals", "roles",
        "payloadBytes",
    }
    assert set(s) == expected
    assert set(s["counts"]) == {"properties", "loans", "parties"}
    assert set(s["property"]) == {"address1", "city", "state", "county"}


def test_party_roles_are_derived_from_sections() -> None:
    p = _proj("p1m")
    roles = {op["Role"] for op in p["orderParties"]}
    assert {"BUYER", "SELLER", "LENDER", "TITLE_COMPANY"} <= roles
    # One party table with a role discriminator - not one table per role.
    assert all(set(x) == {"PartyId", "PartyType", "FirstName", "LastName",
                          "CompanyName", "DisplayName", "Email", "Phone"} for x in p["parties"])
