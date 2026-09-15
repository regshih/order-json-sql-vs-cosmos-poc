"""Unit tests for the archive layer, retention model, cost model, generator and
API response models - the parts that must be correct without any Azure access."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from generator.profiles import GrowthDials, SIZE_PROFILES
from generator.synthetic_order_generator import (
    build_order,
    calibrated_profiles,
    compact_bytes,
    percentile,
)
from ingestion.archive.raw_archive import LocalRawArchive, archive_path, build_archive
from ingestion.parser.block_splitter import parse_envelope

PROFILES = {p.name: p for p in calibrated_profiles(seed=42)}


# --------------------------------------------------------------------------
# Archive layer
# --------------------------------------------------------------------------


def test_archive_round_trip(tmp_path: Path) -> None:
    a = LocalRawArchive(root=tmp_path, compress=True)
    doc = build_order(42, 0, PROFILES["p500k"], "POC001")
    res = a.put("POC001", "order-1", 3, doc)
    assert res.payload_bytes > 400_000
    assert res.stored_bytes < res.payload_bytes  # gzip actually compressed
    assert len(res.payload_hash) == 64
    assert a.get("POC001", "order-1", 3) == doc


def test_archive_is_write_once(tmp_path: Path) -> None:
    a = LocalRawArchive(root=tmp_path)
    a.put("POC001", "order-1", 1, {"a": 1})
    with pytest.raises(FileExistsError, match="write-once"):
        a.put("POC001", "order-1", 1, {"a": 2})
    # An explicit override is allowed, for a deliberate replay fix.
    a.put("POC001", "order-1", 1, {"a": 2}, overwrite=True)
    assert a.get("POC001", "order-1", 1) == {"a": 2}


def test_archive_path_layout() -> None:
    assert archive_path("C1", "O1", 7, True) == "C1/O1/7/order.json.gz"
    assert archive_path("C1", "O1", 7, False) == "C1/O1/7/order.json"


def test_archive_lists_versions(tmp_path: Path) -> None:
    a = LocalRawArchive(root=tmp_path)
    for v in (1, 2, 5, 59):
        a.put("POC001", "order-1", v, {"v": v})
    assert a.list_versions("POC001", "order-1") == [1, 2, 5, 59]
    assert a.list_versions("POC001", "missing") == []


def test_archive_compression_ratio_is_what_the_cost_model_assumes(tmp_path: Path) -> None:
    """The retention model prices the archive with a ~9% gzip ratio. If the
    real ratio drifts far from that, the cost analysis is wrong."""
    a = LocalRawArchive(root=tmp_path, compress=True)
    doc = build_order(42, 1, PROFILES["p1m"], "POC001")
    res = a.put("POC001", "o", 1, doc)
    ratio = res.stored_bytes / res.payload_bytes
    assert 0.03 < ratio < 0.20, f"gzip ratio {ratio:.3f} is outside the modelled range"


def test_build_archive_defaults_to_local(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ARCHIVE_BACKEND", raising=False)
    monkeypatch.setenv("ARCHIVE_LOCAL_ROOT", str(tmp_path))
    assert isinstance(build_archive(), LocalRawArchive)


def test_build_archive_adls_requires_account(monkeypatch) -> None:
    monkeypatch.setenv("ARCHIVE_BACKEND", "adls")
    monkeypatch.delenv("ARCHIVE_ACCOUNT_NAME", raising=False)
    with pytest.raises(ValueError, match="ARCHIVE_ACCOUNT_NAME"):
        build_archive()


# --------------------------------------------------------------------------
# Generator
# --------------------------------------------------------------------------


def test_generator_is_deterministic() -> None:
    a = build_order(42, 17, PROFILES["p1m"], "POC003")
    b = build_order(42, 17, PROFILES["p1m"], "POC003")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_generator_varies_by_index_and_seed() -> None:
    a = build_order(42, 17, PROFILES["p1m"], "POC003")
    b = build_order(42, 18, PROFILES["p1m"], "POC003")
    c = build_order(43, 17, PROFILES["p1m"], "POC003")
    assert a != b and a != c


def test_order_ids_are_stable_across_content_changes() -> None:
    """Order ids derive from (seed, index), not the RNG stream, so regenerating
    after a generator change upserts instead of duplicating."""
    a = build_order(42, 5, PROFILES["p500k"], "POC001")
    b = build_order(42, 5, PROFILES["p5m"], "POC001")
    ida = a["ExtractData"]["ExtractObjects"][0]["ObjectDetails"]["OrderID"]
    idb = b["ExtractData"]["ExtractObjects"][0]["ObjectDetails"]["OrderID"]
    assert ida == idb


@pytest.mark.parametrize("name,lo,hi", [
    ("p500k", 400_000, 700_000),
    ("p1m", 850_000, 1_250_000),
    ("p1_5m", 1_350_000, 1_800_000),
    ("p1_9m", 1_750_000, 2_300_000),
    ("p3m", 2_700_000, 3_600_000),
    ("p5m", 4_400_000, 5_800_000),
])
def test_size_profiles_hit_their_targets(name: str, lo: int, hi: int) -> None:
    n = compact_bytes(build_order(42, 0, PROFILES[name], "POC001"))
    assert lo <= n <= hi, f"{name} produced {n:,} bytes, outside {lo:,}..{hi:,}"


def test_growth_comes_from_business_structure_not_padding() -> None:
    """A larger profile must have MORE array elements, not longer strings."""
    small = parse_envelope(build_order(42, 0, PROFILES["p500k"], "POC001"))["objectData"]
    large = parse_envelope(build_order(42, 0, PROFILES["p5m"], "POC001"))["objectData"]
    for section in ("ChecklistTasks", "RequestedTasks", "Notes", "GeneralNotes"):
        assert len(large[section]) > len(small[section]), f"{section} did not grow"
    # Title exceptions grow through the commitment structure.
    assert (len(large["Title"]["Commitments"][0]["Exceptions"])
            > len(small["Title"]["Commitments"][0]["Exceptions"]))
    # And individual strings do NOT balloon.
    def max_str(node, best=0):
        if isinstance(node, dict):
            for v in node.values():
                best = max_str(v, best)
        elif isinstance(node, list):
            for v in node:
                best = max_str(v, best)
        elif isinstance(node, str):
            best = max(best, len(node))
        return best
    assert max_str(large) < max_str(small) * 3


def test_generator_reproduces_substantial_sparsity() -> None:
    """The real sample is 32.8% empty strings; the generator produces ~15%.

    Sparsity is a core argument against full normalisation, so the generator has
    to exhibit it - but it is deliberately DENSER than the real document, which
    makes the benchmark conservative: more real content per byte transferred.
    The gap is recorded in docs/BENCHMARK_METHOD.md rather than tuned away,
    because changing it would invalidate the calibrated size profiles."""
    od = parse_envelope(build_order(42, 0, PROFILES["p1m"], "POC001"))["objectData"]
    empty = total = 0

    def walk(n):
        nonlocal empty, total
        if isinstance(n, dict):
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
        elif isinstance(n, str):
            total += 1
            if n == "":
                empty += 1

    walk(od)
    pct = empty / total * 100
    assert 10 < pct < 45, (
        f"empty-string rate {pct:.1f}% - expected roughly 15% (generator) to "
        f"32.8% (real sample)"
    )


def test_growth_dials_scale_only_growable_fields() -> None:
    base = GrowthDials()
    scaled = base.scaled(2.0)
    assert scaled.checklist_tasks == base.checklist_tasks * 2
    assert scaled.notes == base.notes * 2
    assert scaled.cdf_count == base.cdf_count  # non-scaling


def test_percentile_helper() -> None:
    v = list(range(1, 101))
    assert percentile(v, 0.5) == 50
    assert percentile(v, 0.95) == 95
    assert percentile([], 0.5) == 0


def test_size_profile_weights_sum_to_one() -> None:
    assert abs(sum(p.weight for p in SIZE_PROFILES) - 1.0) < 0.001


# --------------------------------------------------------------------------
# Retention model
# --------------------------------------------------------------------------


def test_retention_model_scales_linearly_with_volume() -> None:
    from tools.retention_model import Inputs, model

    def make(opd: float) -> Inputs:
        return Inputs(orders_per_day=opd, average_versions_per_order=10,
                      average_updates_per_order_per_day=2, retention_days=730,
                      average_payload_bytes=1_300_000, hot_retention_days=180,
                      blocks_per_order=34, cosmos_items_per_order=34, customers=8)

    a, b = model(make(1000)), model(make(2000))
    assert b.total_orders == pytest.approx(a.total_orders * 2)
    assert b.archive_bytes == pytest.approx(a.archive_bytes * 2)


def test_hierarchical_partition_key_is_bounded_by_one_order() -> None:
    """The core partitioning claim: the logical partition does not grow with
    tenant size or with elapsed time."""
    from tools.retention_model import Inputs, model

    small = model(Inputs(500, 4, 0.5, 730, 1_300_000, 180, 34, 34, 8))
    huge = model(Inputs(50_000, 100, 10, 730, 1_300_000, 180, 34, 34, 8))
    assert small.cosmos_partition["bytesPerLogicalPartition"] == \
           huge.cosmos_partition["bytesPerLogicalPartition"]
    assert huge.cosmos_partition["pctOfLimit"] < 1.0


def test_single_level_customer_key_exceeds_the_limit() -> None:
    from tools.retention_model import Inputs, model

    r = model(Inputs(2000, 12, 2, 730, 1_300_000, 180, 34, 34, 8))
    assert r.cosmos_partition["singleLevelCustomerKeyWouldExceed"] is True
    assert r.cosmos_partition["singleLevelCustomerKeyPctOfLimit"] > 100


def test_hot_retention_bounds_the_operational_store() -> None:
    from tools.retention_model import Inputs, model

    full = model(Inputs(2000, 12, 2, 730, 1_300_000, 730, 34, 34, 8))
    hot = model(Inputs(2000, 12, 2, 730, 1_300_000, 180, 34, 34, 8))
    assert hot.sql_hot_bytes < full.sql_hot_bytes
    assert hot.archive_bytes == full.archive_bytes  # archive keeps everything


# --------------------------------------------------------------------------
# Cost model
# --------------------------------------------------------------------------


def test_cost_model_refuses_to_estimate_missing_ru() -> None:
    """If no RU was measured, the model must say so rather than invent one."""
    from tools.cost_model import cosmos_cost

    snapshot = {"meters": {"cosmos_provisioned_ru": {"retailPrice": 0.008}}}
    out = cosmos_cost(snapshot, {"byOperation": {}}, 50, {"summary": 1.0}, autoscale=False)
    assert "error" in out and "no measured RU" in out["error"]


def test_cost_model_uses_measured_ru() -> None:
    from tools.cost_model import cosmos_cost

    snapshot = {"meters": {"cosmos_provisioned_ru": {"retailPrice": 0.008}}}
    ru = {"byOperation": {"summary": {"mean": 4.0, "p50": 3.9, "p95": 4.2, "samples": 100}}}
    out = cosmos_cost(snapshot, ru, 50, {"summary": 1.0}, autoscale=False)
    assert out["measuredRuPerRequest"] == 4.0
    assert out["ruPerSecondRequired"] == 200.0
    assert out["provisionedRuPerSec"] >= 400  # honours the documented minimum
    assert out["monthlyThroughputUsd"] > 0


def test_cost_model_autoscale_costs_more_than_provisioned() -> None:
    from tools.cost_model import cosmos_cost

    snapshot = {"meters": {"cosmos_provisioned_ru": {"retailPrice": 0.008},
                           "cosmos_autoscale_ru": {"retailPrice": 0.012}}}
    ru = {"byOperation": {"summary": {"mean": 10.0, "p50": 10, "p95": 11, "samples": 10}}}
    p = cosmos_cost(snapshot, ru, 50, {"summary": 1.0}, autoscale=False)
    a = cosmos_cost(snapshot, ru, 50, {"summary": 1.0}, autoscale=True)
    assert a["monthlyThroughputUsd"] > p["monthlyThroughputUsd"]


def test_sql_cost_includes_storage() -> None:
    from tools.cost_model import sql_cost

    snapshot = {"meters": {"sql_gp_provisioned_vcore": {"retailPrice": 0.152217},
                           "sql_gp_storage": {"retailPrice": 0.115}}}
    out = sql_cost(snapshot, 4, "gp_provisioned", 100)
    # The model rounds to cents, as a cost report should.
    assert out["monthlyComputeUsd"] == pytest.approx(round(4 * 0.152217 * 730, 2), abs=0.01)
    assert out["monthlyStorageUsd"] == pytest.approx(11.5, abs=0.01)
    assert out["monthlyTotalUsd"] == pytest.approx(out["monthlyComputeUsd"] + 11.5, abs=0.01)


# --------------------------------------------------------------------------
# API response model
# --------------------------------------------------------------------------


def test_block_response_shape_is_stable() -> None:
    from app.repositories.base import block_response

    r = block_response("o1", 3, "C1", "TITLE", {"Commitments": []},
                       [{"blockSubType": "POLICIES", "sequence": 0, "payloadBytes": 10},
                        {"blockSubType": "COMMITMENTS", "sequence": 0, "payloadBytes": 20}])
    assert set(r) == {"orderId", "orderVersion", "customerId", "blockType", "parts", "data"}
    # parts are sorted deterministically so both backends agree
    assert [p["blockSubType"] for p in r["parts"]] == ["COMMITMENTS", "POLICIES"]


def test_block_endpoints_cover_the_documented_api() -> None:
    from app.repositories.base import BLOCK_ENDPOINTS

    assert {"title", "cdf", "notes", "checklist"} <= set(BLOCK_ENDPOINTS)
    for name, (btype, sub) in BLOCK_ENDPOINTS.items():
        assert btype.isupper()
        assert sub is None or sub.isupper()


# --------------------------------------------------------------------------
# Fabric extractor schema stability
# --------------------------------------------------------------------------


def test_cosmos_parquet_schema_is_stable_for_a_header_only_batch() -> None:
    """An incremental push often contains only header items, whose block columns
    are all None. Without an explicit schema pyarrow infers those as `null`
    type, the Parquet no longer matches the Delta table, and Fabric's replicator
    silently ignores the file - the push reports success and the change never
    appears. This is the regression guard for that."""
    import datetime

    import pandas as pd
    import pyarrow as pa

    from ingestion.fabric.push_to_onelake import COSMOS_SCHEMA

    header_only = {
        "id": "x", "docType": "orderHeader", "customerId": "C", "orderId": "o",
        "orderVersion": 1,
        "blockType": None, "blockSubType": None, "sequence": None,
        "chunkIndex": None, "chunkCount": None, "payloadBytes": None,
        "searchJson": "{}", "summaryJson": "{}", "modifiedUtc": "z", "sourceTs": 1,
        "__rowMarker__": 4, "_extractedUtc": datetime.datetime(2026, 1, 1),
    }
    table = pa.Table.from_pandas(
        pd.DataFrame([header_only]), schema=COSMOS_SCHEMA, preserve_index=False
    )
    null_typed = [f.name for f in table.schema if str(f.type) == "null"]
    assert not null_typed, f"columns inferred as null type: {null_typed}"
    assert str(table.schema.field("blockType").type) == "string"
    # double, NOT int64: pandas represents a nullable integer column as float64,
    # so the Delta table created by the first full push has double columns.
    # Pinning int64 afterwards is a *different* schema conflict, rejected just as
    # silently. See COSMOS_SCHEMA.
    assert str(table.schema.field("sequence").type) == "double"


def test_pinned_cosmos_schema_is_identical_for_full_and_partial_batches() -> None:
    """The property that matters: a full snapshot and a header-only incremental,
    written through the pinned schema, must produce byte-identical column types.
    The Delta table is created from the first push, so if the two ever differ the
    replicator silently rejects the incremental.

    Note this deliberately does NOT compare against raw pyarrow inference - the
    whole point of pinning is to override inference, which is unstable across
    batches."""
    import datetime

    import pandas as pd
    import pyarrow as pa

    from ingestion.fabric.push_to_onelake import COSMOS_SCHEMA

    def row(header: bool) -> dict:
        return {
            "id": "x", "docType": "orderHeader" if header else "orderBlock",
            "customerId": "C", "orderId": "o", "orderVersion": 1,
            "blockType": None if header else "TITLE",
            "blockSubType": None if header else "COMMITMENTS",
            "sequence": None if header else 0,
            "chunkIndex": None if header else 0,
            "chunkCount": None if header else 1,
            "payloadBytes": None if header else 9,
            "searchJson": "{}", "summaryJson": "{}", "modifiedUtc": "z", "sourceTs": 1,
            "__rowMarker__": 4, "_extractedUtc": datetime.datetime(2026, 1, 1),
        }

    full = pa.Table.from_pandas(
        pd.DataFrame([row(True), row(False)]), schema=COSMOS_SCHEMA, preserve_index=False
    ).schema
    partial = pa.Table.from_pandas(
        pd.DataFrame([row(True)]), schema=COSMOS_SCHEMA, preserve_index=False
    ).schema
    assert full == partial, "full and header-only batches produced different schemas"
    assert not [f.name for f in full if str(f.type) == "null"]
    # And the extractor's own _mark() must agree with the pinned schema.
    from ingestion.fabric.push_to_onelake import _mark

    marked = _mark(pd.DataFrame([{k: v for k, v in row(True).items()
                                  if k not in ("__rowMarker__", "_extractedUtc")}]))
    marked_schema = pa.Table.from_pandas(
        marked, schema=COSMOS_SCHEMA, preserve_index=False
    ).schema
    assert marked_schema == full


def test_cosmos_schema_matches_the_rows_the_extractor_builds() -> None:
    """The declared schema must cover exactly the columns the extractor emits -
    a drift in either direction breaks the mirror."""
    from ingestion.fabric.push_to_onelake import COSMOS_SCHEMA

    emitted = {
        "id", "docType", "customerId", "orderId", "orderVersion", "blockType",
        "blockSubType", "sequence", "chunkIndex", "chunkCount", "payloadBytes",
        "searchJson", "summaryJson", "modifiedUtc", "sourceTs",
        # added by _mark()
        "__rowMarker__", "_extractedUtc",
    }
    assert set(COSMOS_SCHEMA.names) == emitted


def test_sql_mirror_schema_is_pinned_from_the_full_snapshot() -> None:
    """A full snapshot and a sparse incremental must produce ONE schema.

    This is the SQL-side twin of the Cosmos schema-drift bug, and it is the
    defect that actually halted the SQL mirror: `pd.read_sql` types each batch
    independently, so an incremental where a nullable column happens to be all
    NULL - or happens to contain no NULLs at all - yields a different Parquet
    schema than the seed did, and the replicator drops the file in silence.
    """
    import pandas as pd
    import pyarrow as pa

    from ingestion.fabric.push_to_onelake import _mark, pinned_schema

    # A full snapshot: the nullable integer column has a NULL, so pandas makes
    # it float64, and the optional text column has a value.
    full = pd.DataFrame([
        {"OrderId": "a", "Sequence": 1, "BlockSubType": "Primary"},
        {"OrderId": "b", "Sequence": None, "BlockSubType": None},
    ])
    registry: dict[str, object] = {}
    schema, pinned_now = pinned_schema(registry, "lz", "OrderJsonBlocks", _mark(full))
    assert pinned_now is True
    assert registry, "the pinned schema must be persisted for later pushes"

    # An incremental that would infer int64 for Sequence and null for
    # BlockSubType if left to itself.
    incr = pd.DataFrame([{"OrderId": "c", "Sequence": 7, "BlockSubType": None}])
    inferred = pa.Table.from_pandas(_mark(incr), preserve_index=False).schema
    assert inferred != schema, "precondition: inference really does drift here"

    # Reusing the registry must return the ORIGINAL schema, not re-pin it...
    again, pinned_now = pinned_schema(registry, "lz", "OrderJsonBlocks", _mark(incr))
    assert pinned_now is False
    assert again == schema

    # ...and the incremental batch must serialise byte-compatibly against it.
    tbl = pa.Table.from_pandas(_mark(incr), schema=again, preserve_index=False)
    assert tbl.schema == schema


def test_pinning_refuses_a_schema_built_from_an_all_null_column() -> None:
    """Pinning from a sparse batch would make the silent failure permanent."""
    import pandas as pd
    import pytest

    from ingestion.fabric.push_to_onelake import pinned_schema

    header_only = pd.DataFrame([{"OrderId": "a", "BlockSubType": None}])
    with pytest.raises(SystemExit, match="inferred as null type"):
        pinned_schema({}, "lz", "OrderJsonBlocks", header_only)


def test_full_document_sections_match_the_decomposed_path() -> None:
    """Scenarios A/B must derive the SAME sections the decomposed backends rebuild.

    This is the semantic-equivalence proof that does not need a live backend. For
    every named block, pulling the section straight out of a stored envelope must
    equal what `split_object_data` -> `reassemble` produces for that block type in
    the hybrid and NoSQL paths. If these ever diverge, the four backends stop
    returning the same API response and the whole comparison is void.
    """
    from generator.synthetic_order_generator import build_order
    from generator.profiles import PROFILES_BY_NAME
    from ingestion.parser.block_splitter import (
        BLOCK_MAP,
        reassemble,
        split_object_data,
    )
    from app.repositories.full_document_common import object_data, sections_for_block

    env = build_order(seed=7, order_index=1, profile=PROFILES_BY_NAME["p500k"],
                      customer_id="CUST-TEST")
    od = object_data(env)
    assert od, "generator must produce ObjectData"

    blocks = split_object_data(od)
    by_type: dict[str, list] = {}
    for b in blocks:
        by_type.setdefault(b.block_type, []).append(b)

    checked = 0
    for block_type in sorted(by_type):
        expected = reassemble(by_type[block_type])
        actual = sections_for_block(env, block_type)
        assert actual == expected, (
            f"{block_type}: full-document extraction diverged from reassembly\n"
            f"  keys expected: {sorted(expected)}\n  keys actual:   {sorted(actual)}"
        )
        checked += 1
    assert checked >= 5, f"only {checked} block types exercised; fixture too thin"


def test_full_document_summary_matches_the_canonical_projection() -> None:
    """The stored projection must BE the canonical summary body, not a lookalike."""
    from generator.synthetic_order_generator import build_order
    from generator.profiles import PROFILES_BY_NAME
    from ingestion.parser.block_splitter import parse_envelope
    from ingestion.parser.relational_extract import extract, summary_from_projection
    from app.repositories.full_document_common import derive_projection

    env = build_order(seed=11, order_index=2, profile=PROFILES_BY_NAME["p1m"],
                      customer_id="CUST-TEST")
    payload_bytes = 1234
    derived = derive_projection(env, payload_bytes)
    canonical = summary_from_projection(extract(parse_envelope(env)), payload_bytes)
    assert derived["summary"] == canonical
    # The indexed scalars must agree with the summary they are derived from.
    assert derived["status"] == canonical["status"]
    assert derived["orderVersion"] == canonical["orderVersion"]
    assert derived["payloadBytes"] == payload_bytes


def test_stress_profiles_are_excluded_from_the_corpus() -> None:
    """A 17 MB boundary probe must never leak into a mixed corpus or a cost model."""
    from generator.profiles import CORPUS_PROFILES, SIZE_PROFILES, STRESS_PROFILES

    assert {p.name for p in STRESS_PROFILES} == {"p10m", "p15m", "p17m"}
    assert all(not p.stress for p in CORPUS_PROFILES)
    assert all(p.weight == 0.0 for p in STRESS_PROFILES)
    assert abs(sum(p.weight for p in CORPUS_PROFILES) - 1.0) < 1e-6
    # p2_1m is a real size but deliberately unweighted: it exists to straddle the
    # Cosmos NoSQL 2 MB ceiling, not to describe the customer's corpus.
    p21 = next(p for p in SIZE_PROFILES if p.name == "p2_1m")
    assert p21.weight == 0.0 and not p21.stress
