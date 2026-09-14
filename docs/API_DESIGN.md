# API design

Implementation: [`app/api/main.py`](../app/api/main.py) ·
Contract: [`app/repositories/base.py`](../app/repositories/base.py) ·
Equivalence tests: [`tests/contract/test_api_equivalence.py`](../tests/contract/test_api_equivalence.py)

---

## 1. One implementation, two backends

Endpoint logic is written **once**. The storage backend is chosen at startup:

```bash
STORAGE_BACKEND=sql    uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --workers 8
STORAGE_BACKEND=cosmos uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --workers 8
STORAGE_BACKEND=fabric uvicorn app.api.main:app ...   # §24 control only
```

Every endpoint calls the [`OrderRepository`](../app/repositories/base.py)
interface. No endpoint contains an `if backend == …`. That is what makes the
benchmark a comparison of *storage engines* rather than of two applications.

## 2. Endpoints

| Method | Path | Returns |
| --- | --- | --- |
| `GET` | `/health` | backend, liveness, pool/partition detail |
| `GET` | `/orders/{orderId}/summary` | header + counts + loan totals + roles |
| `GET` | `/orders/{orderId}` | the complete order, in the source extract-envelope shape |
| `GET` | `/orders/{orderId}/title` | the TITLE section, reassembled |
| `GET` | `/orders/{orderId}/cdf` | the CDF section, reassembled |
| `GET` | `/orders/{orderId}/notes` | NOTES |
| `GET` | `/orders/{orderId}/checklist` | CHECKLIST/TASKS |
| `GET` | `/orders/{orderId}/parties` | PARTIES |
| `GET` | `/orders?customerId=&status=&state=&minLoanAmount=&limit=` | list projection |

Benchmark-support endpoints, **not** part of the customer contract:

| `GET` | `/_bench/orders?limit=` | order ids + payload sizes for the load generator |
| `GET` | `/_bench/metrics` | server-side db / reconstruct / serialize split, Cosmos RU, SQL DMV utilisation |
| `POST` | `/_bench/metrics/reset` | clear the metric ring before a steady-state run |

## 3. Response shapes

### `/orders/{id}/summary`

```json
{
  "orderId": "…", "customerId": "POC001", "orderVersion": 1,
  "orderNumber": "…", "orderType": "Purchase", "status": "Closed",
  "project": "…", "settlementType": "Escrow",
  "isCommercial": false, "isRush": false, "balance": 12345.67,
  "createdDate": "…", "settlementDate": "…", "disbursementDate": "…",
  "property": { "address1": "…", "city": "…", "state": "CA", "county": "…" },
  "counts":   { "properties": 1, "loans": 1, "parties": 12 },
  "loanTotals": { "maxLoanAmount": 812345.67, "totalLoanAmount": 812345.67 },
  "roles": ["BUYER", "LENDER", "SELLER", "TITLE_COMPANY"],
  "payloadBytes": 1316797
}
```

Canonical builder: [`summary_from_projection`](../ingestion/parser/relational_extract.py).
Both repositories must produce exactly this shape — the contract tests compare
it field-by-field.

### `/orders/{id}/{block}`

```json
{
  "orderId": "…", "orderVersion": 1, "customerId": "POC001",
  "blockType": "TITLE",
  "parts": [{ "blockSubType": "COMMITMENTS", "sequence": 0, "payloadBytes": 183540 }, …],
  "data":  { "Commitments": [...], "LoanPolicies": [...], … }
}
```

`parts` is storage metadata (how the section was stored); `data` is the
business content. Keeping them separate is what lets Cosmos report chunking
without changing the business payload.

### `/orders/{id}`

The **source extract envelope**, rebuilt exactly:

```json
{ "ExtractDetails": { … },
  "ExtractData": { "ExtractObjects": [ { "ObjectDetails": {…}, "ObjectData": {…} } ] } }
```

The contract tests assert the returned `ObjectData` is byte-identical to what
was ingested, for both backends and every size profile — so neither backend can
be "equally wrong".

## 4. The two contract violations this design caught

The equivalence tests are not decorative. They found two real bugs that would
have shipped:

1. **GUID casing.** Azure SQL's `uniqueidentifier` renders uppercase; the source
   extract and Cosmos carry lowercase. The same order returned
   `34A19871-…` from SQL and `34a19871-…` from Cosmos. Fixed by canonicalising
   to lowercase at the SQL repository boundary.
2. **Money aggregation.** SQL computes `SUM(LoanAmount)` in `decimal(19,2)` and
   returns `1352717.08`; Cosmos summed the same values as Python floats and
   returned `1352717.0799999998`. Fixed by quantising monetary aggregates to
   cents in both paths.

Both are the kind of defect that only appears when two implementations are
compared directly — which is the argument for building the contract suite before
the benchmark, not after.

## 5. Reconstruction cost — measured, not assumed

Both designs decompose the order, so both must reassemble it. The API measures
the three phases separately (`db_ms`, `reconstruct_ms`, `serialize_ms`) and
reports them on `/_bench/metrics`.

Indicative split on a ~1 MB full order. These come from the API's in-process
telemetry, which is sampled from **one of eight uvicorn workers** - see the
sampling caveat in [BENCHMARK_METHOD.md](BENCHMARK_METHOD.md) section 6. The
*direction* is robust and reproducible; the exact milliseconds are not:

| Phase | SQL | Cosmos |
| --- | ---: | ---: |
| Database | ~13 ms | ~28 ms |
| Reconstruction | ~5.5 ms | ~0.1 ms |
| Serialization | ~1.0 ms | ~0.7 ms |

The asymmetry is informative. SQL returns JSON as **strings** that must be
`json.loads`-ed before reassembly; the Cosmos SDK has already parsed the item's
`data` object, so reassembly is a dictionary merge. Cosmos pays that cost inside
`db_ms` instead — it is moved, not avoided.

## 6. Serialization

Responses are encoded with `orjson` and returned as a pre-rendered `Response`,
not handed to the framework's default encoder. At 50 RPS a mean 1.3 MB payload
is ~65 MB/s of JSON encoding, so the encoder is a measured component rather than
a framework default. Serialization happens **inside** the measured window so it
appears in `serialize_ms`.

## 7. Errors

| Condition | Status |
| --- | --- |
| Unknown order | `404` |
| Order exists but has no such block | `404` |
| Backend unreachable | `503` from `/health`; the failure is recorded in telemetry with `success=false` |
| Invalid `limit` (outside 1–1000) | `422` from Pydantic validation |

## 8. What a production version should change

- **Put `customerId` in the route or the token.** `GET /orders/{orderId}` with no
  tenant scope forces Cosmos into a cross-partition lookup before every read
  (see [COSMOS_DESIGN.md](COSMOS_DESIGN.md) §5). It is also the natural place to
  enforce tenant isolation.
- **Add conditional requests.** `PayloadHash` is already stored per block; an
  `ETag` / `If-None-Match` pair would remove a large share of the multi-megabyte
  traffic for clients that poll.
- **Consider streaming the full-order response.** At the p99 payload (~5 MB) the
  whole document is currently materialised in memory before the first byte goes
  out.
