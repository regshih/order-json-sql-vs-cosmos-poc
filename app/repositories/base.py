"""Storage-agnostic repository contract.

The REST API talks only to this interface. Endpoint logic lives in the API layer
exactly once; SqlOrderRepository and CosmosOrderRepository differ only in how
they fetch and reassemble.

Response shapes are defined here (not per backend) so contract tests can assert
that API(SQL) and API(Cosmos) are semantically equivalent.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.telemetry.metrics import RequestMetrics


@dataclass
class OrderSearchCriteria:
    customer_id: str | None = None
    status: str | None = None
    state: str | None = None
    min_loan_amount: float | None = None
    limit: int = 50


@dataclass
class IngestResult:
    order_id: str
    order_version: int
    customer_id: str
    blocks_written: int
    items_written: int
    bytes_written: int
    archive_uri: str | None = None
    request_charge: float = 0.0
    duration_ms: float = 0.0
    chunked_blocks: int = 0
    max_item_bytes: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


class OrderRepository(ABC):
    """Read + write surface for one operational backend."""

    #: "sql" | "cosmos" | "fabric"
    backend: str = "unknown"

    # ---- lifecycle -------------------------------------------------------
    @abstractmethod
    def ping(self) -> dict[str, Any]:
        """Cheap liveness probe used by GET /health."""

    def close(self) -> None:  # pragma: no cover - optional
        pass

    # ---- reads -----------------------------------------------------------
    @abstractmethod
    def get_summary(self, order_id: str, m: RequestMetrics) -> dict[str, Any] | None:
        """Header + counts only. The hot path; must not touch large payloads."""

    @abstractmethod
    def get_full_order(self, order_id: str, m: RequestMetrics) -> dict[str, Any] | None:
        """Complete reconstructed order, in the source extract-envelope shape."""

    @abstractmethod
    def get_block(
        self, order_id: str, block_type: str, m: RequestMetrics, block_sub_type: str | None = None
    ) -> dict[str, Any] | None:
        """One logical business section (TITLE, CDF, NOTES, CHECKLIST, ...)."""

    @abstractmethod
    def search_orders(self, criteria: OrderSearchCriteria, m: RequestMetrics) -> list[dict[str, Any]]:
        """Filtered list projection."""

    @abstractmethod
    def list_order_ids(self, limit: int = 1000) -> list[dict[str, Any]]:
        """Order ids + sizes, for the load-test harness to pick targets from."""

    # ---- writes ----------------------------------------------------------
    @abstractmethod
    def ingest_order(
        self, envelope: dict[str, Any], archive_uri: str | None = None, payload_hash: str | None = None
    ) -> IngestResult:
        """Upsert one complete order version."""

    @abstractmethod
    def update_block(
        self, order_id: str, block_type: str, block_sub_type: str, sequence: int, payload: dict[str, Any]
    ) -> IngestResult:
        """Targeted update of a single business block (write benchmark)."""

    @abstractmethod
    def update_order_header(self, order_id: str, changes: dict[str, Any]) -> IngestResult:
        """Targeted update of scalar header fields (write benchmark)."""


# --------------------------------------------------------------------------
# Canonical response envelopes - shared by both backends.
# --------------------------------------------------------------------------

# Maps an API path segment to (BlockType, BlockSubType or None).
BLOCK_ENDPOINTS: dict[str, tuple[str, str | None]] = {
    "title": ("TITLE", None),
    "cdf": ("CDF", None),
    "notes": ("NOTES", None),
    "checklist": ("CHECKLIST", "TASKS"),
    "parties": ("PARTIES", None),
    "properties": ("PROPERTIES", "MAIN"),
    "loans": ("LOANS", "MAIN"),
    "tasks": ("REQUESTED_TASKS", "MAIN"),
}


def block_response(
    order_id: str,
    order_version: int,
    customer_id: str,
    block_type: str,
    sections: dict[str, Any],
    parts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Canonical shape for a single-section read. Identical from both backends."""
    return {
        "orderId": order_id,
        "orderVersion": order_version,
        "customerId": customer_id,
        "blockType": block_type,
        "parts": sorted(parts, key=lambda p: (p["blockSubType"], p["sequence"])),
        "data": sections,
    }
