"""Lossless decomposition of an order's ObjectData into logical business blocks.

This module is the single source of truth for how an order is broken apart, and
it is shared by BOTH storage paths:

  * SQL   -> each block becomes one row in ``OrderJsonBlocks``
  * Cosmos-> each block becomes one (or, if oversized, several) Cosmos items

Blocks follow *business* boundaries taken from the measured profile
(docs/DATA_PROFILE.md), never arbitrary byte offsets. The split is lossless:

    reassemble(split(object_data)) == object_data

which tests/unit/test_block_splitter.py asserts on every size profile.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable

# --------------------------------------------------------------------------
# Block definitions
# --------------------------------------------------------------------------

# (BlockType, BlockSubType, [ObjectData keys])
#
# Sections not listed here fall through to MISC/MAIN, which keeps the split
# lossless even when a future extract adds properties we have never seen.
BLOCK_MAP: list[tuple[str, str, tuple[str, ...]]] = [
    ("PARTIES", "BUYERS", ("Buyers",)),
    ("PARTIES", "SELLERS", ("Sellers",)),
    ("PARTIES", "LENDERS", ("Lenders",)),
    ("PARTIES", "TITLE_COMPANIES", ("TitleCompanies",)),
    ("PARTIES", "OTHERS", ("Others",)),
    ("PROPERTIES", "MAIN", ("Properties",)),
    ("LOANS", "MAIN", ("Loans",)),
    ("CDF", "MAIN", ("CDFs",)),
    ("CDF", "AMOUNTS", ("CDFAmounts",)),
    ("TITLE", "MAIN", ("Title",)),
    ("NOTES", "GENERAL", ("GeneralNotes",)),
    ("NOTES", "ORDER", ("Notes",)),
    ("CHECKLIST", "TASKS", ("ChecklistTasks",)),
    ("REQUESTED_TASKS", "MAIN", ("RequestedTasks",)),
    ("LIENS", "EXISTING", ("ExistingLiens",)),
    ("INVOICES", "MAIN", ("Invoices",)),
]

# Composite sections that are further divided along their own natural
# sub-structure before any size-based chunking is considered.
#   parent ObjectData key -> (BlockType, {BlockSubType: [child keys]})
COMPOSITE_SPLITS: dict[str, tuple[str, dict[str, tuple[str, ...]]]] = {
    "CDFs": (
        "CDF",
        {
            "ORIGINATION": ("OriginationChargeSection",),
            "SERVICES": ("ServiceNotShoppedForSection", "ServiceShoppedForSection"),
            "OTHER_COSTS": (
                "TaxesAndGovernmentFeesSection",
                "PrepaidSection",
                "EscrowSection",
                "OtherCostSection",
            ),
            "DUE_FROM_BUYER": ("DueFromBuyerSection", "DueToBuyerSection"),
            "DUE_FROM_SELLER": ("DueFromSellerSection", "DueToSellerSection"),
            "TOTALS": (
                "BuyerCashToCloseSection",
                "SellerCashToCloseSection",
                "TotalClosingCostSection",
                "TotalLoanCostSection",
                "TotalOtherCostSection",
            ),
        },
    ),
    "CDFAmounts": (
        "CDF",
        {
            "DISBURSEMENTS": ("Disbursements",),
            "RECEIPTS": ("Receipts",),
        },
    ),
    "Title": (
        "TITLE",
        {
            "COMMITMENTS": ("Commitments",),
            "POLICIES": ("LoanPolicies", "OwnersPolicies"),
            "PRODUCTS": ("TitleProducts",),
            "ENDORSEMENTS": ("Endorsements",),
            "CHARGES": ("AdditionalCharges",),
        },
    ),
}

# Scalar header fields lifted into relational columns. They stay in the HEADER
# block as well so that reconstruction is exact and backend-independent.
RELATIONAL_HEADER_FIELDS = (
    "Number", "Status", "StatusComment", "Project", "TransactionType", "SettlementType",
    "CreatedDate", "ModifiedDate", "SettlementDate", "DisbursementDate", "ReceivedDate",
    "CompletedDate", "DueDate", "ConsummationDate", "Balance", "Source", "LockStatus",
    "IsCommercial", "IsConstruction", "IsCashSale", "IsRush", "Guid",
)


@dataclass
class Block:
    """One logical business block of an order."""

    block_type: str
    block_sub_type: str
    sequence: int
    payload: dict[str, Any]
    # Set only when a block was chunked by the Cosmos size guard.
    chunk_index: int = 0
    chunk_count: int = 1
    chunk_of_path: str | None = None

    _compact: bytes | None = field(default=None, repr=False, compare=False)

    @property
    def compact(self) -> bytes:
        if self._compact is None:
            self._compact = json.dumps(
                self.payload, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        return self._compact

    @property
    def payload_bytes(self) -> int:
        return len(self.compact)

    @property
    def payload_hash(self) -> str:
        return hashlib.sha256(self.compact).hexdigest()

    @property
    def key(self) -> str:
        """Stable within-order key. Used to build deterministic Cosmos ids and
        as the SQL natural key."""
        base = f"{self.block_type}.{self.block_sub_type}.{self.sequence}"
        return base if self.chunk_count == 1 else f"{base}.c{self.chunk_index}"

    def to_dict(self) -> dict[str, Any]:
        d = {
            "blockType": self.block_type,
            "blockSubType": self.block_sub_type,
            "sequence": self.sequence,
            "payloadBytes": self.payload_bytes,
            "payloadHash": self.payload_hash,
        }
        if self.chunk_count > 1:
            d |= {
                "chunkIndex": self.chunk_index,
                "chunkCount": self.chunk_count,
                "chunkOfPath": self.chunk_of_path,
            }
        return d


# --------------------------------------------------------------------------
# Split
# --------------------------------------------------------------------------


def split_object_data(object_data: dict[str, Any]) -> list[Block]:
    """Decompose ObjectData into logical blocks. Lossless and deterministic."""
    remaining = dict(object_data)
    blocks: list[Block] = []

    for block_type, sub_type, keys in BLOCK_MAP:
        present = {k: remaining.pop(k) for k in keys if k in remaining}
        if not present:
            continue

        # Composite sections are divided further along their own structure.
        composite_key = next((k for k in keys if k in COMPOSITE_SPLITS), None)
        if composite_key and composite_key in present:
            blocks.extend(_split_composite(composite_key, present[composite_key]))
            leftovers = {k: v for k, v in present.items() if k != composite_key}
            if leftovers:
                blocks.append(Block(block_type, sub_type, 0, leftovers))
            continue

        blocks.append(Block(block_type, sub_type, 0, present))

    # Everything that is left: scalar header fields plus the long tail of
    # small/empty sections. Splitting header from tail keeps the hot
    # summary read small.
    header = {k: remaining.pop(k) for k in RELATIONAL_HEADER_FIELDS if k in remaining}
    if header:
        blocks.append(Block("ORDER", "HEADER", 0, header))
    if remaining:
        blocks.append(Block("MISC", "MAIN", 0, remaining))

    return blocks


def _split_composite(parent_key: str, node: Any) -> list[Block]:
    """Split one composite section (CDFs / CDFAmounts / Title) along its
    documented sub-structure."""
    block_type, mapping = COMPOSITE_SPLITS[parent_key]
    blocks: list[Block] = []

    # CDFs is an array of CDF documents; each element gets its own sequence.
    elements: list[tuple[int, dict[str, Any]]]
    if isinstance(node, list):
        elements = [(i, e) for i, e in enumerate(node) if isinstance(e, dict)]
        non_dict = [e for e in node if not isinstance(e, dict)]
        if non_dict:
            # Preserve anything unexpected rather than dropping it.
            blocks.append(Block(block_type, f"{parent_key.upper()}_RAW", 0, {parent_key: non_dict}))
    else:
        elements = [(0, node)]

    for seq, element in elements:
        leftover = dict(element)
        for sub_type, child_keys in mapping.items():
            picked = {k: leftover.pop(k) for k in child_keys if k in leftover}
            if picked:
                blocks.append(Block(block_type, sub_type, seq, picked))
        # Whatever is left of this element is its own MAIN block. The
        # __container__ marker records how to put the array back together.
        main_sub = "MAIN" if parent_key != "CDFAmounts" else "AMOUNTS"
        leftover["__container__"] = {"parent": parent_key, "isArray": isinstance(node, list)}
        blocks.append(Block(block_type, main_sub, seq, leftover))

    return blocks


# --------------------------------------------------------------------------
# Reassemble
# --------------------------------------------------------------------------


def reassemble(blocks: Iterable[Block]) -> dict[str, Any]:
    """Inverse of :func:`split_object_data`. Chunked blocks must be merged
    with :func:`merge_chunks` first (or simply be passed in - this function
    handles them)."""
    blocks = merge_chunks(blocks)

    # parent key -> sequence -> merged element
    composites: dict[str, dict[int, dict[str, Any]]] = {}
    composite_is_array: dict[str, bool] = {}
    flat: dict[str, Any] = {}

    # Which BlockTypes belong to which composite parent.
    type_to_parents: dict[str, list[str]] = {}
    for parent, (btype, _) in COMPOSITE_SPLITS.items():
        type_to_parents.setdefault(btype, []).append(parent)

    for b in sorted(blocks, key=lambda x: (x.block_type, x.sequence, x.block_sub_type)):
        payload = dict(b.payload)
        container = payload.pop("__container__", None)

        if container:
            parent = container["parent"]
            composite_is_array[parent] = container["isArray"]
            composites.setdefault(parent, {}).setdefault(b.sequence, {}).update(payload)
            continue

        if b.block_type in type_to_parents:
            # A sub-block of a composite: attribute it to the right parent by
            # looking up which parent declares this sub-type.
            parent = _owner_of(b.block_type, b.block_sub_type, type_to_parents)
            if parent:
                composites.setdefault(parent, {}).setdefault(b.sequence, {}).update(payload)
                continue

        flat.update(payload)

    for parent, by_seq in composites.items():
        is_array = composite_is_array.get(parent, True)
        if is_array:
            flat[parent] = [by_seq[s] for s in sorted(by_seq)]
        else:
            merged: dict[str, Any] = {}
            for s in sorted(by_seq):
                merged.update(by_seq[s])
            flat[parent] = merged

    return flat


def _deep_merge(base: dict[str, Any], add: dict[str, Any]) -> None:
    """Merge a chunk into the accumulating block payload.

    Lists at the same path are *extended* (that is how a chunked array is put
    back together); nested dicts recurse; scalars keep the first value seen,
    which is chunk 0's - the chunk that carries the non-split remainder.
    """
    for k, v in add.items():
        if k not in base:
            base[k] = list(v) if isinstance(v, list) else v
        elif isinstance(base[k], list) and isinstance(v, list):
            base[k].extend(v)
        elif isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)


def _owner_of(block_type: str, sub_type: str, type_to_parents: dict[str, list[str]]) -> str | None:
    for parent in type_to_parents.get(block_type, []):
        if sub_type in COMPOSITE_SPLITS[parent][1]:
            return parent
    return None


# --------------------------------------------------------------------------
# Chunk merging (Cosmos size guard support)
# --------------------------------------------------------------------------


def merge_chunks(blocks: Iterable[Block]) -> list[Block]:
    """Merge array-element chunks produced by the Cosmos size guard back into
    whole logical blocks."""
    whole: list[Block] = []
    groups: dict[tuple[str, str, int], list[Block]] = {}

    for b in blocks:
        if b.chunk_count <= 1:
            whole.append(b)
        else:
            groups.setdefault((b.block_type, b.block_sub_type, b.sequence), []).append(b)

    for (btype, sub, seq), parts in groups.items():
        parts.sort(key=lambda p: p.chunk_index)
        expected = parts[0].chunk_count
        if len(parts) != expected:
            raise ValueError(
                f"incomplete chunk set for {btype}/{sub}/{seq}: "
                f"got {len(parts)} of {expected}"
            )
        merged: dict[str, Any] = {}
        for p in parts:
            _deep_merge(merged, p.payload)
        whole.append(Block(btype, sub, seq, merged))

    return whole


# --------------------------------------------------------------------------
# Envelope helpers
# --------------------------------------------------------------------------


def parse_envelope(doc: dict[str, Any]) -> dict[str, Any]:
    """Pull identity + payload out of the extract envelope.

    Raises ValueError on a shape we do not recognise, rather than silently
    producing a half-populated order.
    """
    try:
        details = doc["ExtractDetails"]
        obj = doc["ExtractData"]["ExtractObjects"][0]
        od = obj["ObjectDetails"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"unrecognised extract envelope: {exc}") from exc

    return {
        "customerId": details.get("CustomerSerialNumber", "UNKNOWN"),
        "extractTimestamp": details.get("ExtractDateTimeUTC"),
        "extractServer": details.get("ExtractServer"),
        "extractType": details.get("ExtractType"),
        "objectType": od.get("ObjectType"),
        "orderId": od["OrderID"],
        "orderVersion": int(od.get("OrderVersion", 1)),
        "objectData": obj["ObjectData"],
    }


def build_envelope(
    customer_id: str,
    order_id: str,
    order_version: int,
    object_data: dict[str, Any],
    extract_timestamp: str | None = None,
    extract_server: str | None = None,
    extract_type: str = "FULL",
) -> dict[str, Any]:
    """Rebuild the extract envelope around reassembled ObjectData, so an API
    full-order response is byte-comparable with the ingested source."""
    return {
        "ExtractDetails": {
            "CustomerSerialNumber": customer_id,
            "ExtractDateTimeUTC": extract_timestamp,
            "ExtractServer": extract_server,
            "ExtractType": extract_type,
        },
        "ExtractData": {
            "ExtractObjects": [
                {
                    "ObjectDetails": {
                        "ObjectType": "ORDER",
                        "OrderID": order_id,
                        "OrderVersion": order_version,
                    },
                    "ObjectData": object_data,
                }
            ]
        },
    }
