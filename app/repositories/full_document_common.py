"""Shared logic for the two FULL-DOCUMENT backends (Scenarios A and B).

Terminology, used strictly throughout this file and the reports:

    LOGICAL ORDER      one business order - the complete extract envelope
    DATABASE ITEM      one physical row (SQL) or BSON document (Mongo)
    API RESPONSE       what GET /orders/{id} returns

Scenarios A and B store **one LOGICAL ORDER per DATABASE ITEM**. Scenarios C and
D store one logical order across many database items and rebuild it on read.
All four return the same API RESPONSE.

Why this module exists. The four backends must return semantically equivalent
JSON, and the cheapest way to guarantee that is to derive the derived responses
from the *same* functions the decomposed backends already use, rather than
reimplementing the projection per backend and hoping the contract tests catch the
drift. `summary_from_projection` is the canonical summary shape; this module feeds
it from a stored envelope instead of from relational rows.

The derived projection is computed **once at ingest** and stored alongside the
payload, not recomputed per read. Running a full extract over a 5 MB document on
every summary request would make the cheap endpoint the expensive one - and the
whole point of Scenario A and B is that the payload is untouched unless the caller
actually asked for it.

Storing that small projection next to the payload is NOT decomposition: the
complete logical order remains in one database item, byte-for-byte. The
projection is routing and index metadata, which the brief explicitly permits.
"""

from __future__ import annotations

from typing import Any

from ingestion.parser.block_splitter import (
    BLOCK_MAP,
    RELATIONAL_HEADER_FIELDS,
    parse_envelope,
)
from ingestion.parser.relational_extract import extract, summary_from_projection

ENVELOPE_OBJECTDATA_PATH = "$.ExtractData.ExtractObjects[0].ObjectData"

# (BlockType, BlockSubType) -> the ObjectData keys that block carries.
BLOCK_KEYS: dict[tuple[str, str], tuple[str, ...]] = {
    (btype, sub): keys for btype, sub, keys in BLOCK_MAP
}

# Every ObjectData key that any named block claims.
NAMED_KEYS: frozenset[str] = frozenset(
    k for _, _, keys in BLOCK_MAP for k in keys
)

# The decomposed path lifts the scalar header fields into their own ORDER/HEADER
# block BEFORE the MISC fallthrough, so MISC is the complement of *both* sets.
# Getting this wrong is not cosmetic: MISC would then carry Status, Balance and
# the dates as well, and the full-document backends would return a different
# MISC body than the decomposed ones - a silent contract break that the
# equivalence test exists to catch.
HEADER_KEYS: frozenset[str] = frozenset(RELATIONAL_HEADER_FIELDS)
MISC_EXCLUDED: frozenset[str] = NAMED_KEYS | HEADER_KEYS


def object_data(envelope: dict[str, Any]) -> dict[str, Any]:
    """The ObjectData node of an extract envelope, or {} if the shape is wrong."""
    try:
        return envelope["ExtractData"]["ExtractObjects"][0]["ObjectData"] or {}
    except (KeyError, IndexError, TypeError):
        return {}


def object_details(envelope: dict[str, Any]) -> dict[str, Any]:
    try:
        return envelope["ExtractData"]["ExtractObjects"][0]["ObjectDetails"] or {}
    except (KeyError, IndexError, TypeError):
        return {}


def derive_projection(envelope: dict[str, Any], payload_bytes: int) -> dict[str, Any]:
    """Everything a full-document backend needs to store *outside* the payload.

    Computed once per ingest. `summary` is the canonical summary response body, so
    a full-document backend serves GET /summary without reading the payload at
    all, and returns exactly what the decomposed backends return.
    """
    # `extract` consumes the PARSED envelope (identity lifted out of the
    # extract wrapper), not the raw one - the same two-step the ingestion
    # pipeline uses, so the projection cannot drift from the decomposed path.
    proj = extract(parse_envelope(envelope))
    o = proj["order"]
    sf = proj["searchFields"]
    return {
        "orderId": o["OrderId"],
        "customerId": o["CustomerId"],
        "orderVersion": o["CurrentVersion"],
        "orderNumber": o["OrderNumber"],
        "status": o["Status"],
        "primaryState": sf.get("state"),
        "maxLoanAmount": sf.get("maxLoanAmount"),
        "totalLoanAmount": sf.get("totalLoanAmount"),
        "propertyCount": len(proj["properties"]),
        "loanCount": len(proj["loans"]),
        "partyCount": len(proj["parties"]),
        "payloadBytes": payload_bytes,
        "summary": summary_from_projection(proj, payload_bytes),
        "searchFields": sf,
    }


def sections_for_block(
    envelope: dict[str, Any], block_type: str, block_sub_type: str | None = None
) -> dict[str, Any]:
    """The ObjectData keys belonging to one logical block, pulled from the payload.

    Mirrors what `reassemble()` produces for the decomposed backends: a dict of
    ObjectData key -> original value. MISC is the complement of every named block,
    so a section the BLOCK_MAP has never seen still comes back rather than
    vanishing.
    """
    od = object_data(envelope)

    if block_type == "MISC":
        return {k: v for k, v in od.items() if k not in MISC_EXCLUDED}
    if block_type == "ORDER":
        return {k: od[k] for k in RELATIONAL_HEADER_FIELDS if k in od}

    wanted: list[str] = []
    for (btype, sub), keys in BLOCK_KEYS.items():
        if btype != block_type:
            continue
        if block_sub_type and sub != block_sub_type:
            continue
        wanted.extend(keys)

    return {k: od[k] for k in wanted if k in od}


def synthetic_parts(block_type: str, sections: dict[str, Any], payload_bytes: int) -> list[dict[str, Any]]:
    """`parts` describes PHYSICAL storage, so it legitimately differs here.

    A decomposed backend returns one entry per stored block. A full-document
    backend has exactly one stored item, so it returns one entry marked as such.
    Contract tests therefore compare `data` and the identity fields, never
    `parts` - the difference in `parts` IS the architectural difference under
    test, not a defect.
    """
    subs = sorted({
        sub for (btype, sub) in BLOCK_KEYS if btype == block_type
    }) or ["MAIN"]
    return [{
        "blockSubType": subs[0] if len(subs) == 1 else "ALL",
        "sequence": 0,
        "payloadBytes": payload_bytes,
        "storedAsSingleDocument": True,
    }]
