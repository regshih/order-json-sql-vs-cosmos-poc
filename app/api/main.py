"""The SAME REST API over either storage backend.

Backend is chosen once, at startup, from STORAGE_BACKEND=sql|cosmos|fabric.
Endpoint logic is written exactly once here; the repositories differ only in
how they fetch and reassemble. This is what makes the contract tests - and the
benchmark comparison - meaningful.

Run:
    STORAGE_BACKEND=sql   uvicorn app.api.main:app --host 0.0.0.0 --port 8000
    STORAGE_BACKEND=cosmos uvicorn app.api.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import orjson
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from app.repositories.base import BLOCK_ENDPOINTS, OrderRepository, OrderSearchCriteria
from app.telemetry.metrics import collector, measure

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("orderapi")

repo: OrderRepository | None = None


def build_repository() -> OrderRepository:
    backend = os.getenv("STORAGE_BACKEND", "sql").lower()
    if backend == "sql":
        from app.repositories.sql_repository import SqlOrderRepository

        return SqlOrderRepository()
    if backend == "cosmos":
        from app.repositories.cosmos_repository import CosmosOrderRepository

        return CosmosOrderRepository()
    if backend == "fabric":
        from app.repositories.fabric_repository import FabricOrderRepository

        return FabricOrderRepository()
    raise ValueError(f"unknown STORAGE_BACKEND={backend!r}; expected sql|cosmos|fabric")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global repo
    repo = build_repository()
    log.info("storage backend=%s ready", repo.backend)
    yield
    if repo is not None:
        repo.close()


app = FastAPI(
    title="Order API - SQL vs Cosmos POC",
    version="1.0.0",
    description="One API contract, interchangeable operational storage backends.",
    lifespan=lifespan,
    default_response_class=JSONResponse,
)


class ORJSONResponse(Response):
    """orjson serialisation.

    Serialisation is a first-class measurement in this POC (a 5 MB response at
    50 RPS is ~250 MB/s of JSON encoding), so the encoder choice is explicit
    rather than inherited from the framework default.
    """

    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        return orjson.dumps(content)


def _repo() -> OrderRepository:
    if repo is None:  # pragma: no cover - lifespan guarantees this
        raise HTTPException(503, "repository not initialised")
    return repo


def _emit(m, body: Any) -> Response:
    """Serialise inside the measured window and record the byte count."""
    with m.serialize():
        payload = orjson.dumps(body)
    m.response_bytes = len(payload)
    return Response(content=payload, media_type="application/json")


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, Any]:
    r = _repo()
    try:
        detail = r.ping()
        return {"status": "ok", "backend": r.backend, "detail": detail}
    except Exception as exc:
        raise HTTPException(503, f"{r.backend} unhealthy: {type(exc).__name__}: {exc}")


@app.get("/orders/{order_id}/summary")
def get_summary(order_id: str) -> Response:
    r = _repo()
    with measure(r.backend, "/orders/{id}/summary", "summary", order_id) as m:
        body = r.get_summary(order_id, m)
        if body is None:
            m.status_code = 404
            raise HTTPException(404, f"order {order_id} not found")
        return _emit(m, body)


@app.get("/orders/{order_id}")
def get_order(order_id: str) -> Response:
    r = _repo()
    with measure(r.backend, "/orders/{id}", "full", order_id) as m:
        body = r.get_full_order(order_id, m)
        if body is None:
            m.status_code = 404
            raise HTTPException(404, f"order {order_id} not found")
        return _emit(m, body)


def _block_endpoint(order_id: str, name: str) -> Response:
    r = _repo()
    block_type, sub_type = BLOCK_ENDPOINTS[name]
    with measure(r.backend, f"/orders/{{id}}/{name}", name, order_id) as m:
        body = r.get_block(order_id, block_type, m, sub_type)
        if body is None:
            m.status_code = 404
            raise HTTPException(404, f"order {order_id} has no {block_type} block")
        return _emit(m, body)


@app.get("/orders/{order_id}/title")
def get_title(order_id: str) -> Response:
    return _block_endpoint(order_id, "title")


@app.get("/orders/{order_id}/cdf")
def get_cdf(order_id: str) -> Response:
    return _block_endpoint(order_id, "cdf")


@app.get("/orders/{order_id}/notes")
def get_notes(order_id: str) -> Response:
    return _block_endpoint(order_id, "notes")


@app.get("/orders/{order_id}/checklist")
def get_checklist(order_id: str) -> Response:
    return _block_endpoint(order_id, "checklist")


@app.get("/orders/{order_id}/parties")
def get_parties(order_id: str) -> Response:
    return _block_endpoint(order_id, "parties")


@app.get("/orders")
def search_orders(
    customerId: str | None = Query(None),
    status: str | None = Query(None),
    state: str | None = Query(None),
    minLoanAmount: float | None = Query(None),
    limit: int = Query(50, ge=1, le=1000),
) -> Response:
    r = _repo()
    with measure(r.backend, "/orders", "search") as m:
        criteria = OrderSearchCriteria(
            customer_id=customerId,
            status=status,
            state=state,
            min_loan_amount=minLoanAmount,
            limit=limit,
        )
        rows = r.search_orders(criteria, m)
        return _emit(m, {"count": len(rows), "criteria": criteria.__dict__, "orders": rows})


# --------------------------------------------------------------------------
# Benchmark support endpoints (not part of the customer-facing contract)
# --------------------------------------------------------------------------


@app.get("/_bench/orders")
def bench_orders(limit: int = Query(1000, ge=1, le=20000)) -> dict[str, Any]:
    """Order ids + payload sizes, so the load generator can target real data
    and bucket results by payload size."""
    r = _repo()
    return {"orders": r.list_order_ids(limit)}


@app.get("/_bench/metrics")
def bench_metrics() -> dict[str, Any]:
    """Server-side latency breakdown (db / reconstruct / serialize) and, for
    Cosmos, measured RU.

    IMPORTANT: the collector is in-process. With uvicorn --workers N > 1 each
    worker has its OWN ring, so this endpoint returns whichever worker answered,
    and /_bench/metrics/reset clears whichever worker answered - not the same
    one. Server-side figures are therefore a SAMPLE of one worker and can carry
    residue from a previous run.

    Client-side latency and throughput (measured by the load generator) are
    unaffected. For authoritative per-operation RU use
    cosmos/indexing/measure_index_impact.py, which runs single-process.
    """
    r = _repo()
    workers = int(os.getenv("API_WORKERS", "1"))
    out: dict[str, Any] = {"backend": r.backend, **collector.summary()}
    out["samplingWarning"] = {
        "workers": workers,
        "workerPid": os.getpid(),
        "singleWorkerSample": workers > 1,
        "note": (
            "server-side metrics come from ONE uvicorn worker of "
            f"{workers}; treat db/reconstruct/serialize/RU as indicative, not "
            "authoritative. Client-side latency is unaffected."
        ) if workers > 1 else "single worker - metrics are complete",
    }
    out["process"] = _proc_stats()
    if hasattr(r, "resource_stats"):
        try:
            out["sqlResourceStats"] = r.resource_stats()  # type: ignore[attr-defined]
        except Exception as exc:
            out["sqlResourceStats"] = {"error": str(exc)[:200]}
    if hasattr(r, "container_stats"):
        try:
            out["cosmosContainer"] = r.container_stats()  # type: ignore[attr-defined]
        except Exception as exc:
            out["cosmosContainer"] = {"error": str(exc)[:200]}
    return out


@app.post("/_bench/metrics/reset")
def bench_metrics_reset() -> dict[str, str]:
    collector.reset()
    return {"status": "reset"}


def _proc_stats() -> dict[str, Any]:
    """Application CPU and memory. psutil is optional; fall back to the stdlib
    so the endpoint never fails."""
    try:
        import psutil

        p = psutil.Process()
        return {
            "cpuPercent": p.cpu_percent(interval=0.1),
            "rssMb": round(p.memory_info().rss / 1024**2, 1),
            "numThreads": p.num_threads(),
            "systemCpuPercent": psutil.cpu_percent(interval=0.1),
            "systemMemPercent": psutil.virtual_memory().percent,
        }
    except ImportError:
        try:
            import resource

            ru = resource.getrusage(resource.RUSAGE_SELF)
            return {
                "userCpuSec": round(ru.ru_utime, 2),
                "sysCpuSec": round(ru.ru_stime, 2),
                "maxRssMb": round(ru.ru_maxrss / 1024, 1),
            }
        except Exception:
            return {}
