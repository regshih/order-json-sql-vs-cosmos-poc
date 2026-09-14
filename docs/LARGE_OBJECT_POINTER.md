# Optional experiment — large-object pointer pattern (§25)

Store **metadata in the operational database, payload in blob storage**, and
have the API dereference the pointer on read.

```
SQL row / Cosmos item  ──pointer──▶  ADLS Gen2  raw/{customer}/{order}/{version}/order.json.gz
   (searchable fields)                 (the payload)
```

Status: **evaluated analytically against measured data; not benchmarked as a
serving path.** The reason is given in §4 — the measurements already taken make
the outcome predictable, and the POC's remaining time was better spent
completing the two primary paths.

---

## 1. Why the idea is attractive here

The measured profile makes a strong prima facie case:

- Payload is **highly concentrated**: the top 10 of 106 sections hold 87.9% of
  the bytes ([DATA_PROFILE.md](DATA_PROFILE.md)).
- The payload is **pass-through**: 33% of scalars are empty strings and the big
  sections are never query predicates.
- The archive **already exists** and already holds every version, gzipped to
  ~9% of original size — the pointer target is built and populated.
- Blob storage is **~7x cheaper per GB** than Cosmos storage and ~15x cheaper
  than Azure SQL storage at the rates in [COST_ANALYSIS.md](COST_ANALYSIS.md).

## 2. What the POC already tells us about it

Three measurements bear directly on this design, and none required a new
benchmark:

| Measurement | Value | Implication for the pointer pattern |
| --- | --- | --- |
| Archive write latency (ADLS, gzip, in-VNet) | p95 **104 ms** during the 500-order ingest | A blob round trip is ~an order of magnitude slower than the SQL block read it would replace |
| SQL full-order read, db time | **~13 ms** p50 | The thing being replaced is already fast |
| SQL block read (`/title`, ~300 KB) | **p50 10.9 ms / p95 19.2 ms** at 50 RPS | Same |
| gzip ratio | ~9% | Storage saving is real and large |

The archive figure is the decisive one: **the pointer dereference costs more
than the database read it removes**, for hot data.

## 3. Where it is still the right answer

The pattern is not wrong — it is wrong *for the hot path*. It is the right answer
for exactly the cases the retention model identifies:

- **Cold history.** [RETENTION_ANALYSIS.md](RETENTION_ANALYSIS.md) shows the
  operational store only needs the *current* version of *recent* orders; under
  the medium scenario that is 441 GiB hot versus 1.89 TiB of archived versions.
  Older versions are already pointer-addressed by `OrderVersions.SourceArchiveUri`.
- **Very large blocks.** If a future extract produced a single indivisible
  element above the Cosmos item budget, the size guard raises rather than
  truncating ([`size_guard.py`](../cosmos/modeling/size_guard.py)). A pointer is
  the natural escape hatch for that element — and it was never reached in this
  dataset.
- **Infrequently accessed sections.** Sections that are large but rarely read
  could be pointer-addressed per block, because `OrderJsonBlocks` is already
  block-granular. This is the variant most worth a follow-up measurement.

## 4. Why it was not benchmarked as a serving path

Being explicit rather than quietly dropping it:

1. The two primary paths both **already meet the stated 50 RPS** for the read
   shapes that matter, so the pointer pattern is not solving a performance
   problem.
2. The measured archive latency (p95 104 ms) is **worse than the database reads
   it would replace**, so a hot-path benchmark would confirm a known result.
3. It introduces a **second consistency domain** — a metadata row and a blob
   that can disagree — for which this POC has no correctness story.
4. It adds a **failure mode on read**: the order becomes unreadable if the blob
   is missing or its lifecycle policy has tiered it to Archive, where first-byte
   latency is measured in hours.

## 5. Caching, which is the cheaper version of the same idea

If the goal is to reduce cost and load rather than to change the storage model,
the measured data points at caching first:

- `PayloadHash` is **already stored per block** in both backends, so `ETag` /
  `If-None-Match` is a small change that would eliminate a large share of
  multi-megabyte traffic for polling clients.
- Block-granular endpoints already let a client fetch only what changed —
  and [COSMOS_DESIGN.md](COSMOS_DESIGN.md) shows a TITLE block read costs
  **336 RU against 1,149 RU** for the whole order, so block-level caching is
  worth roughly 3.4x on the Cosmos side before any blob is involved.

## 6. If it were to be measured

The experiment worth running, stated precisely so it can be picked up:

1. Add a `PayloadUri` column to `OrderJsonBlocks` and a `payloadUri` field to the
   Cosmos block item; populate it for blocks above a configurable byte threshold
   and null the inline payload.
2. Extend both repositories to dereference the pointer on read, concurrently
   across blocks.
3. Run **Workload B (block read)** and **Workload C (full order)** at 10/25/50
   RPS against threshold values of 0 (everything pointer-addressed), 100 KB and
   ∞ (the current design).
4. Report p50/p95/p99, bytes/sec, the added blob request cost, and the storage
   saved in each operational store.

The interesting output is the **threshold curve**: the block size above which
pointer dereference becomes cheaper than inline storage. Everything needed to
run it — the archive layer, the block granularity, the load harness and the
size-bucketed reporting — is already in this repository.
