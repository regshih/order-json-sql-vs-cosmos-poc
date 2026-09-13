"""Structured per-request telemetry.

Every API request records a single flat record (§29):
backend, endpoint, logical operation, total / db / reconstruction /
serialization duration, response bytes, outcome - plus backend-specific
extensions (Cosmos RU, retries, 429s; SQL query time and pool state).

Full order payloads are never logged. Only sizes.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from statistics import fmean
from typing import Any, Iterator

logger = logging.getLogger("orderapi.telemetry")


@dataclass
class RequestMetrics:
    backend: str = ""
    endpoint: str = ""
    operation: str = ""
    order_id: str | None = None
    total_ms: float = 0.0
    db_ms: float = 0.0
    reconstruct_ms: float = 0.0
    serialize_ms: float = 0.0
    response_bytes: int = 0
    blocks_read: int = 0
    items_read: int = 0
    success: bool = True
    status_code: int = 200
    error: str | None = None
    # Cosmos
    request_charge: float = 0.0
    cosmos_requests: int = 0
    retries: int = 0
    throttled_429: int = 0
    # SQL
    sql_queries: int = 0
    sql_pool_checkout_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, 0, 0.0, "")} | {
            "backend": self.backend,
            "endpoint": self.endpoint,
            "operation": self.operation,
            "total_ms": round(self.total_ms, 3),
            "success": self.success,
        }


class _Timer:
    """Accumulating stopwatch that can be entered many times."""

    def __init__(self) -> None:
        self.elapsed_ms = 0.0

    @contextmanager
    def __call__(self) -> Iterator[None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.elapsed_ms += (time.perf_counter() - t0) * 1000


class Collector:
    """In-process metrics sink.

    Keeps a bounded ring of recent records for the /metrics endpoint (which the
    load-test harness scrapes) and emits one JSON line per request.
    """

    def __init__(self, capacity: int = 20_000) -> None:
        self.capacity = capacity
        self._records: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._log_requests = os.getenv("TELEMETRY_LOG_REQUESTS", "false").lower() == "true"

    def record(self, m: RequestMetrics) -> None:
        d = m.to_dict()
        with self._lock:
            self._records.append(d)
            if len(self._records) > self.capacity:
                del self._records[: len(self._records) - self.capacity]
        if self._log_requests:
            logger.info(json.dumps(d))

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._records)

    def reset(self) -> None:
        with self._lock:
            self._records.clear()

    def summary(self) -> dict[str, Any]:
        recs = self.snapshot()
        if not recs:
            return {"requests": 0}

        def pct(values: list[float], p: float) -> float:
            if not values:
                return 0.0
            s = sorted(values)
            k = (len(s) - 1) * p
            f = int(k)
            c = min(f + 1, len(s) - 1)
            return round(s[f] + (s[c] - s[f]) * (k - f), 3)

        by_op: dict[str, list[dict[str, Any]]] = {}
        for r in recs:
            by_op.setdefault(r.get("operation", "?"), []).append(r)

        def block(rs: list[dict[str, Any]]) -> dict[str, Any]:
            tot = [r["total_ms"] for r in rs]
            db = [r.get("db_ms", 0.0) for r in rs]
            rec = [r.get("reconstruct_ms", 0.0) for r in rs]
            ser = [r.get("serialize_ms", 0.0) for r in rs]
            ru = [r.get("request_charge", 0.0) for r in rs]
            byt = [r.get("response_bytes", 0) for r in rs]
            out = {
                "requests": len(rs),
                "errors": sum(1 for r in rs if not r.get("success", True)),
                "total_ms": {"p50": pct(tot, 0.5), "p95": pct(tot, 0.95), "p99": pct(tot, 0.99),
                             "mean": round(fmean(tot), 3), "max": round(max(tot), 3)},
                "db_ms": {"p50": pct(db, 0.5), "p95": pct(db, 0.95), "mean": round(fmean(db), 3)},
                "reconstruct_ms": {"p50": pct(rec, 0.5), "p95": pct(rec, 0.95), "mean": round(fmean(rec), 3)},
                "serialize_ms": {"p50": pct(ser, 0.5), "p95": pct(ser, 0.95), "mean": round(fmean(ser), 3)},
                "response_bytes": {"p50": int(pct([float(b) for b in byt], 0.5)),
                                   "p95": int(pct([float(b) for b in byt], 0.95)),
                                   "mean": int(fmean(byt)) if byt else 0,
                                   "max": max(byt) if byt else 0},
            }
            if any(ru):
                out["request_charge_ru"] = {
                    "p50": pct(ru, 0.5), "p95": pct(ru, 0.95), "p99": pct(ru, 0.99),
                    "mean": round(fmean(ru), 3), "max": round(max(ru), 3),
                    "total": round(sum(ru), 2),
                }
                out["throttled_429"] = sum(r.get("throttled_429", 0) for r in rs)
                out["retries"] = sum(r.get("retries", 0) for r in rs)
            return out

        return {
            "requests": len(recs),
            "overall": block(recs),
            "byOperation": {op: block(rs) for op, rs in sorted(by_op.items())},
        }


collector = Collector()


@contextmanager
def measure(backend: str, endpoint: str, operation: str, order_id: str | None = None) -> Iterator[RequestMetrics]:
    """Wrap one API request. Timers for db / reconstruct / serialize are
    attached to the yielded metrics object as ``m.db``, ``m.reconstruct``,
    ``m.serialize``."""
    m = RequestMetrics(backend=backend, endpoint=endpoint, operation=operation, order_id=order_id)
    m.db = _Timer()          # type: ignore[attr-defined]
    m.reconstruct = _Timer()  # type: ignore[attr-defined]
    m.serialize = _Timer()    # type: ignore[attr-defined]
    t0 = time.perf_counter()
    try:
        yield m
    except Exception as exc:
        m.success = False
        m.error = f"{type(exc).__name__}: {exc}"[:300]
        raise
    finally:
        m.total_ms = (time.perf_counter() - t0) * 1000
        m.db_ms = m.db.elapsed_ms          # type: ignore[attr-defined]
        m.reconstruct_ms = m.reconstruct.elapsed_ms  # type: ignore[attr-defined]
        m.serialize_ms = m.serialize.elapsed_ms      # type: ignore[attr-defined]
        collector.record(m)
