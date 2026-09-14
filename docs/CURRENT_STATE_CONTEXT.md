# Current-state context and environment constraints

What was known going in, what the environment actually allowed, and which
decisions those constraints drove. Recorded so a reader can tell the difference
between "this is the right architecture" and "this is what the sandbox permitted".

---

## 1. Customer context (as stated in the brief)

| Fact | Value | Status |
| --- | --- | --- |
| Source | large, deeply nested order JSON from a title/escrow platform | STATED |
| Typical payload | ~1–2 MB | STATED |
| Large payload | up to ~5 MB | STATED |
| Workload | primarily READ | STATED |
| Request rate | ~50 requests/sec | STATED |
| Retention horizon | ~2 years | STATED |
| Analytics direction | Microsoft Fabric, future | STATED |
| Consumer expectation | JSON API responses | STATED |
| Storage must be JSON-native | **No** — only the API response must be JSON | STATED |
| Current pain point | Cosmos DB **logical partition growth** | STATED |
| Update/version frequency | **unknown** | NOT STATED — deliberately not invented; see [RETENTION_ANALYSIS.md](RETENTION_ANALYSIS.md) |

## 2. What the source sample actually contained

Recomputed from the file rather than trusted from the brief — see
[DATA_PROFILE.md](DATA_PROFILE.md). The brief's prior figures proved accurate:

| Measure | Brief said | Measured |
| --- | --- | --- |
| Formatted bytes | ~881 KB | 881,010 |
| Compact bytes | ~552 KB | 551,747 |
| `ObjectData` properties | ~106 | 106 |
| `CDFs` | ~199 KB | 199,337 |
| `Title` | ~164 KB | 163,859 |

Findings the brief did **not** mention, which changed the design:

- **Max nesting depth 16**, 4,552 objects, 872 arrays, 18,000 scalars.
- **32.8% of all scalars are empty strings** (5,911 of them), plus 1,556 empty
  objects and 457 empty arrays. The document is extremely sparse — which is a
  direct argument against full normalisation.
- **95 RTF-encoded fields** totalling 54,912 bytes; the largest is only 1,706
  bytes, so there is no single giant text field to special-case.
- **1,194 distinct GUIDs across 2,133 occurrences**; 209 GUIDs appear more than
  once and one appears 129 times. These are intra-document foreign keys and
  become the natural join keys and deterministic item ids.
- **Size is highly concentrated**: the top 10 of 106 sections hold 87.9% of the
  bytes, and 76 sections are under 1 KiB. This is the single fact that makes a
  block model work.
- The file is an **extract envelope**, not a bare order: `ExtractDetails` +
  `ExtractData.ExtractObjects[].ObjectDetails/ObjectData`. Identity
  (`OrderID`, `OrderVersion`, `CustomerSerialNumber`) lives in the envelope, not
  the payload.
- The sample was at **`OrderVersion` 59**, which is the only real evidence about
  version churn — and it is a single observation, so it is used as a labelled
  scenario, never as a rate.

## 3. Environment constraints encountered

These are properties of the POC subscription, not of Azure or of the design.

### 3.1 Tenant policy forces private-only data planes

A management-group policy assignment (`MCAPSGovDenyPolicies` /
`DenyPublicEndpointEnabled`) forces `publicNetworkAccess=Disabled` on:

- `Microsoft.Sql/servers`
- `Microsoft.DocumentDB/databaseAccounts`
- `Microsoft.Storage/storageAccounts`

Setting `publicNetworkAccess: 'Enabled'` in Bicep is silently reverted, and
`az sql server update --enable-public-network true` has no effect. Creating
firewall rules then fails with `DenyPublicEndpointEnabled`.

**Consequences, all of which improved the POC:**

1. Private endpoints for SQL, Cosmos and ADLS, with private DNS zones
   ([`infra/bicep/modules/network.bicep`](../infra/bicep/modules/network.bicep)).
2. The API and the load generator run on **VMs inside the VNet**. This removes
   home-internet RTT and bandwidth from every measurement — the benchmark
   numbers are better-founded than a workstation-driven test would have been.
3. Fabric integration had to work **outbound from the VNet**. That led to the
   Open Mirroring design in [FABRIC_ANALYTICS.md](FABRIC_ANALYTICS.md), and to
   the discovery that Fabric **managed private endpoints are available on an F2
   capacity** (created successfully; approved on the SQL server).

### 3.2 Outbound SSH is blocked from the operator workstation

Every VM operation is driven through `az vm run-command invoke` rather than SSH.
That is why [`scripts/vm_bootstrap.sh`](../scripts/vm_bootstrap.sh),
[`scripts/vm_api.sh`](../scripts/vm_api.sh) and
[`scripts/run_benchmarks.sh`](../scripts/run_benchmarks.sh) exist as
self-contained scripts: they are executed by the VM agent, not typed into a shell.

Code reaches the VMs by `git clone`/`git pull` from the public GitHub repository,
which is also why the repository hygiene rules in §28 of the brief are enforced
rather than aspirational — the repository is genuinely public.

### 3.3 Benchmark VMs were deallocated mid-run, twice, with no activity-log entry

Both benchmark VMs went from `VM running` to `VM deallocated` while result files
were being retrieved, and `az monitor activity-log list` for the resource group
showed **no** `deallocate` or `powerOff` event in the preceding four hours (the
only caller recorded was the operator). The cause was not identified; the most
likely explanation is a subscription- or management-group-level cost-governance
automation that does not log at resource-group scope.

Practical consequences, which are reflected in
[RUNBOOK.md](RUNBOOK.md):

- **Deallocation is not data loss** - the managed disks survive, so
  `az vm start` followed by re-running
  [`scripts/fetch_vm_results.py`](../scripts/fetch_vm_results.py) recovers
  everything.
- **Retrieve results promptly.** Anything that lives only on a VM is at risk;
  benchmark result files should be pulled to the repository as soon as a run
  finishes rather than at the end of a session.
- A long chunked fetch can fail part-way through for this reason, so the fetcher
  retries and reports rather than silently truncating.

### 3.4 Fabric capacity

A dedicated **F2** capacity (`fabordjsonpoc915d`) was created for the POC rather
than reusing one of the pre-existing capacities in the subscription, so that
capacity contention could not confound the analytics measurements and so the
cleanup story is unambiguous. F2 is the smallest practical SKU, per the brief.

## 4. Prior related work in this subscription

A separate POC in the same subscription (`softpro-mirroring-poc`) already
demonstrated Azure SQL → Fabric Open Mirroring for the same customer domain,
consolidating multiple tenant databases into one Fabric mirrored database with
sub-30-second latency. That is why Open Mirroring was a known-good fallback here
rather than a speculative one, and why its landing-zone contract
(`_metadata.json` + monotonically-named Parquet + `__rowMarker__`) was
implemented directly rather than rediscovered.

## 5. What this POC deliberately did not do

- **No multi-region.** Adds RU cost and consistency trade-offs orthogonal to the
  SQL-vs-Cosmos question.
- **No production-grade auth on the API.** The API is unauthenticated inside the
  VNet; tenant isolation is a design note, not an implementation.
- **No Cosmos change feed processor.** The Fabric extractor uses a `_ts`
  watermark, which is sufficient to measure freshness and simpler to reason
  about. A production pipeline should use the change feed for delete semantics.
- **No autoscale tuning sweep.** Cosmos ran at a fixed autoscale ceiling; finding
  the minimum viable RU/s is a follow-up once the request mix is confirmed.
