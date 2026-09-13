"""Extract the relational projection of an order.

Only fields with demonstrated operational value are lifted out: the ones that
appear in search, filter, sort, join, security or reporting predicates. Everything
else stays in JSON blocks. See docs/SQL_DESIGN.md for the rationale per field.

The same projection is used by BOTH paths:
  * SQL    -> rows in Orders / Properties / Parties / OrderParties / Loans
  * Cosmos -> denormalised routing fields on the ORDER/HEADER item, so that the
              Cosmos search endpoint can filter without cross-partition fan-out
              into payload items.

This keeps the two backends semantically comparable instead of accidentally
comparing a rich SQL model against a thin Cosmos one.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

# Roles are derived from the ObjectData section a party appears in.
PARTY_SECTION_ROLES: dict[str, str] = {
    "Buyers": "BUYER",
    "Sellers": "SELLER",
    "Lenders": "LENDER",
    "TitleCompanies": "TITLE_COMPANY",
    "Others": "OTHER",
}

_MONEY_RE = re.compile(r"[^0-9.\-]")


def to_decimal(value: Any) -> float | None:
    """Money in the source arrives as a formatted string. Return None rather
    than 0.0 for unparseable/empty values so that 'absent' and 'zero' stay
    distinguishable in aggregates."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = _MONEY_RE.sub("", str(value))
    if cleaned in ("", "-", ".", "-."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def to_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(text[: len(fmt) + 6 if ".%f" in fmt else len(text)], fmt)
        except ValueError:
            continue
    return None


def _money(value: float | None) -> float | None:
    """Quantise a monetary amount to cents."""
    return None if value is None else round(value + 0.0, 2)


def _deterministic_id(*parts: str) -> str:
    """Stable surrogate key for a row that has no usable natural GUID."""
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


def _unique_id(
    natural: Any, seen: set[str], order_id: str, kind: str, index: int
) -> str:
    """Resolve a primary key, guaranteeing uniqueness within the order.

    Prefers the source GUID (it is the natural key and dedupes across orders).
    Falls back to a deterministic surrogate when the GUID is absent or already
    used by an earlier row of the same kind in this order - a real condition in
    the source data that would otherwise abort the whole ingest on a primary-key
    violation.
    """
    candidate = natural if isinstance(natural, str) and natural else None
    if candidate is None or candidate in seen:
        candidate = _deterministic_id(order_id, kind, str(index))
    seen.add(candidate)
    return candidate


def extract(envelope: dict[str, Any]) -> dict[str, Any]:
    """Return the full relational projection for one order version."""
    od = envelope["objectData"]
    order_id = envelope["orderId"]
    customer_id = envelope["customerId"]

    order = {
        "OrderId": order_id,
        "CustomerId": customer_id,
        "CurrentVersion": envelope["orderVersion"],
        "OrderNumber": od.get("Number") or None,
        "OrderType": od.get("TransactionType") or None,
        "Status": od.get("Status") or None,
        "Project": od.get("Project") or None,
        "SettlementType": od.get("SettlementType") or None,
        "IsCommercial": bool(od.get("IsCommercial")),
        "IsRush": bool(od.get("IsRush")),
        "Balance": to_decimal(od.get("Balance")),
        "CreatedDate": to_datetime(od.get("CreatedDate")),
        "SettlementDate": to_datetime(od.get("SettlementDate")),
        "DisbursementDate": to_datetime(od.get("DisbursementDate")),
        "CompletedDate": to_datetime(od.get("CompletedDate")),
        "ExtractDate": to_datetime(envelope.get("extractTimestamp")),
    }

    properties: list[dict[str, Any]] = []
    # Source GUIDs are not guaranteed unique within one order - the same Guid can
    # appear on two entries. These are primary keys in the relational model, so a
    # collision must resolve to a distinct surrogate rather than fail the ingest.
    seen_property_ids: set[str] = set()
    for i, p in enumerate(od.get("Properties") or []):
        if not isinstance(p, dict):
            continue
        addr = p.get("Address") or {}
        state = addr.get("State") or {}
        properties.append(
            {
                "PropertyId": _unique_id(
                    p.get("Guid"), seen_property_ids, order_id, "prop", i
                ),
                "OrderId": order_id,
                "Sequence": i,
                "Address1": (addr.get("Address1") or None),
                "Address2": (addr.get("Address2") or None),
                "City": (addr.get("City") or None),
                "State": (state.get("Code") if isinstance(state, dict) else None) or None,
                "Zip": (addr.get("Zip") or None),
                "County": (p.get("County") or addr.get("County") or None),
                "Acreage": to_decimal(p.get("Acreage")),
                "PropertyType": (p.get("PropertyType") or p.get("Description") or None),
            }
        )

    parties: dict[str, dict[str, Any]] = {}
    order_parties: list[dict[str, Any]] = []

    def add_party(node: dict[str, Any], role: str, seq: int) -> None:
        # The party's own Guid is the natural key; it is reused across orders
        # in the source, which is exactly the de-duplication we want.
        pid = node.get("Guid") or _deterministic_id(order_id, role, str(seq))
        people = node.get("People") or []
        main = next(
            (p for p in people if isinstance(p, dict) and p.get("IsMainPerson")),
            people[0] if people and isinstance(people[0], dict) else {},
        )
        is_company = (node.get("ContactType") or node.get("BuyerSellerType")) == "Company"
        parties.setdefault(
            pid,
            {
                "PartyId": pid,
                "PartyType": "COMPANY" if is_company else "PERSON",
                "FirstName": (main.get("FirstName") or None),
                "LastName": (main.get("LastName") or None),
                "CompanyName": (node.get("Name") if is_company else None),
                "DisplayName": (node.get("Name") or main.get("FullName") or None),
                "Email": (node.get("Email") or main.get("Email") or None),
                "Phone": (node.get("Phone") or main.get("Phone") or None),
            },
        )
        order_parties.append(
            {
                "OrderId": order_id,
                "PartyId": pid,
                "Role": role,
                "Sequence": seq,
            }
        )

    for section, role in PARTY_SECTION_ROLES.items():
        for i, node in enumerate(od.get(section) or []):
            if isinstance(node, dict):
                resolved = role
                if role == "OTHER":
                    desc = (node.get("BuyerSellerRole") or node.get("BuyerSellerOtherRoleTypeDescription") or "")
                    resolved = re.sub(r"[^A-Z_]", "", desc.upper().replace(" ", "_")) or "OTHER"
                add_party(node, resolved, i)

    loans: list[dict[str, Any]] = []
    seen_loan_ids: set[str] = set()
    for i, ln in enumerate(od.get("Loans") or []):
        if not isinstance(ln, dict):
            continue
        lender = ln.get("Lender") if isinstance(ln.get("Lender"), dict) else {}
        lender_pid = lender.get("Guid")
        if lender_pid and lender_pid not in parties:
            add_party(lender, "LENDER", 900 + i)
        terms = ln.get("Terms") if isinstance(ln.get("Terms"), dict) else {}
        amount = to_decimal(ln.get("Amount")) or to_decimal(terms.get("AmountFinanced"))
        loans.append(
            {
                "LoanId": _unique_id(ln.get("Guid"), seen_loan_ids, order_id, "loan", i),
                "OrderId": order_id,
                "Sequence": i,
                "LenderPartyId": lender_pid,
                "LoanAmount": amount,
                "LoanType": (ln.get("Type") or None),
                "LoanNumber": (ln.get("Number") or None),
                "InterestRate": to_decimal(terms.get("InterestRate")),
                "LoanTermMonths": terms.get("LoanTermMonths") if isinstance(terms.get("LoanTermMonths"), int) else None,
            }
        )

    # Denormalised routing/search fields. SQL gets these via joins; Cosmos
    # carries them on the header item so its search path is a single query.
    first_prop = properties[0] if properties else {}
    search_fields = {
        "state": first_prop.get("State"),
        "county": first_prop.get("County"),
        "city": first_prop.get("City"),
        # Money is quantised to cents. SQL aggregates LoanAmount in
        # decimal(19,2) and returns an exact value; summing the same numbers as
        # Python floats does not, so without rounding the two backends disagree
        # on totals in the last decimal places. Caught by the contract tests.
        "maxLoanAmount": _money(
            max((l["LoanAmount"] for l in loans if l["LoanAmount"] is not None), default=None)
        ),
        "totalLoanAmount": _money(
            sum((l["LoanAmount"] for l in loans if l["LoanAmount"] is not None), 0.0) or None
        ),
        "loanCount": len(loans),
        "propertyCount": len(properties),
        "partyCount": len(parties),
        "roles": sorted({op["Role"] for op in order_parties}),
    }

    return {
        "order": order,
        "properties": properties,
        "parties": list(parties.values()),
        "orderParties": order_parties,
        "loans": loans,
        "searchFields": search_fields,
    }


def summary_from_projection(proj: dict[str, Any], payload_bytes: int | None = None) -> dict[str, Any]:
    """The canonical /orders/{id}/summary response body.

    Both repositories must produce this exact shape - it is what the contract
    tests compare.
    """
    o = proj["order"]
    sf = proj["searchFields"]
    return {
        "orderId": o["OrderId"],
        "customerId": o["CustomerId"],
        "orderVersion": o["CurrentVersion"],
        "orderNumber": o["OrderNumber"],
        "orderType": o["OrderType"],
        "status": o["Status"],
        "project": o["Project"],
        "settlementType": o["SettlementType"],
        "isCommercial": o["IsCommercial"],
        "isRush": o["IsRush"],
        "balance": o["Balance"],
        "createdDate": o["CreatedDate"].isoformat() if o["CreatedDate"] else None,
        "settlementDate": o["SettlementDate"].isoformat() if o["SettlementDate"] else None,
        "disbursementDate": o["DisbursementDate"].isoformat() if o["DisbursementDate"] else None,
        "property": {
            "address1": sf.get("address1") or (proj["properties"][0]["Address1"] if proj["properties"] else None),
            "city": sf.get("city"),
            "state": sf.get("state"),
            "county": sf.get("county"),
        },
        "counts": {
            "properties": sf["propertyCount"],
            "loans": sf["loanCount"],
            "parties": sf["partyCount"],
        },
        "loanTotals": {
            "maxLoanAmount": sf["maxLoanAmount"],
            "totalLoanAmount": sf["totalLoanAmount"],
        },
        "roles": sf["roles"],
        "payloadBytes": payload_bytes,
    }
