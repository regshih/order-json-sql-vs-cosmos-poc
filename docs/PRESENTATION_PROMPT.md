# Prompts for building slides and diagrams from this POC

Paste-ready prompts for an LLM (or a slide tool such as Copilot in PowerPoint,
Gamma, or a diagramming assistant). Each one is **self-contained**: the fact
sheet in [section 1](#1-the-fact-sheet--paste-this-with-every-prompt) travels
with the prompt so the model never has to guess a number.

**The one rule that matters.** Every prompt below ends with an instruction not to
invent figures. Keep it. The most likely failure mode is a model rounding 16.70 MB
to "about 20 MB", inventing a p99, or quietly turning "throttled at 6,000 RU/s"
into "Cosmos DB failed" — all three would misrepresent the work.

| Want to produce… | Use |
| --- | --- |
| a 12-slide executive deck | [§2](#2-prompt--executive-deck-12-slides) |
| a 20-slide technical deck | [§3](#3-prompt--technical-deck-20-slides) |
| one architecture diagram | [§4](#4-prompt--the-architecture-diagram) |
| the comparison diagram | [§5](#5-prompt--the-five-designs-comparison-diagram) |
| the measurement-setup diagram | [§6](#6-prompt--the-benchmark-topology-diagram) |
| a one-page written summary or email | [§7](#7-prompt--one-page-summary-or-customer-email) |
| speaker notes for an existing deck | [§8](#8-prompt--speaker-notes) |

Source of every figure: [PLAIN_LANGUAGE_SUMMARY.md](PLAIN_LANGUAGE_SUMMARY.md),
which in turn cites the generated result documents.

---

## 1. THE FACT SHEET — paste this with every prompt

```text
=== FACT SHEET: Order JSON storage POC. Every figure here is MEASURED on deployed
=== Azure resources unless marked DOCUMENTED (from Microsoft documentation) or
=== ESTIMATED (arithmetic from measured values). Do not add figures not listed.

THE WORKLOAD
- Source data: one title/escrow order per JSON file, 0.5-5 MB, deeply nested,
  106 top-level sections. Top 10 sections hold 87.9% of the bytes; 76 sections
  are under 1 KiB; 33% of all scalar values are empty strings.
- Operational requirement: ~50 reads/sec of COMPLETE orders via an API,
  ~2 year retention, must also feed Microsoft Fabric analytics.
- The customer's question: "architect the ideal solution for serving the full
  JSON within an operational workflow, such as an API. If Microsoft does not
  have a suitable solution within its stack, that is acceptable - we need to
  know that."

THREE TERMS THAT MUST NOT BE USED INTERCHANGEABLY
- LOGICAL ORDER  = one business order (0.5-5 MB of JSON)
- DATABASE ITEM  = one physical row / BSON document / Cosmos item
- API RESPONSE   = what GET /orders/{id} returns
All five designs return the complete order as the API RESPONSE. Only three store
it as ONE DATABASE ITEM.

THE FIVE DESIGNS (all behind the SAME API, one setting selects the store)
- A.  SQL Full JSON        Azure SQL, ONE ROW, JsonPayload nvarchar(max)+ISJSON
- A'. SQL native json      Azure SQL, ONE ROW, native binary json column type
- B.  Cosmos Mongo         Cosmos DB for MongoDB, ONE BSON DOCUMENT, 16MB capability
- C.  Cosmos NoSQL         Cosmos DB for NoSQL, ~34 DOCUMENTS per order (Part 1)
- D.  SQL hybrid           Azure SQL, 8 relational tables + ~34 JSON blocks (Part 1)

CAN ONE DATABASE ITEM HOLD A COMPLETE ORDER? (MEASURED by pushing until refusal)
- Azure SQL, one row, nvarchar(max): accepted to 16.70 MB, never refused
- Azure SQL, one row, native json:   accepted to 16.70 MB, never refused
- Cosmos DB for MongoDB, one doc:    accepted to 15.04 MB; 16.70 MB refused
                                     with DocumentTooLarge
- Cosmos DB for NoSQL, one item:     accepted to 1.78 MB; 2.01 MB refused with
                                     HTTP 413 (this is a COUNTERFACTUAL probe,
                                     not design C - design C splits the order)
- BSON encoding is 1.3%-1.55% LARGER than compact JSON for this data, so a
  document on the 16 MB line as JSON is over the line once encoded.

AT 50 REQUESTS/SEC, COMPLETE ORDERS, ALL SIZES MIXED (MEASURED, 46 runs)
  Design                        achieved  p50        p95        errors  MB/s
  A. SQL Full JSON              50.0      17.2 ms    63.1 ms    0.00%   73.9
  D. SQL hybrid (reassembled)   49.0      29.5 ms    92.1 ms    0.00%   63.3
  B. Cosmos Mongo (one doc)     49.9      66.9 ms    181.1 ms   0.00%   68.8
  A'. SQL native json           13.9      55,591 ms  111,533 ms 3.82%   19.4
  C. Cosmos NoSQL (reassembled) 12.4      65,169 ms  126,316 ms 0.00%   16.2

MANDATORY CAVEAT ON ROW C - do not omit this wherever row C appears:
  The Cosmos NoSQL container was provisioned at autoscale max 6,000 RU/s while
  the Mongo collection had 10,000 RU/s. NOT like-for-like. A whole-order read on
  design C costs ~515 RU, so 6,000 RU/s supports ~11.6 RPS - which is what the
  12.4 observation is. Part 1 held 50 RPS on the same design at 40,000 RU/s.
  Row C is THROTTLING, NOT INCAPABILITY. Never present it as "Cosmos DB failed".

AT 100 REQUESTS/SEC THE RANKING INVERTS (MEASURED)
  B. Cosmos Mongo   99.8 achieved, p50 73.1 ms
  A. SQL Full JSON  95.7 achieved, p50 1,532.5 ms

THE THROUGHPUT CEILING IS NOT THE NETWORK (MEASURED)
- Cosmos Mongo sustained 240.8 MB/s at 4.86 MB payloads, 49.6 RPS.
- Both SQL designs flattened at ~150 MB/s, achieved rate falling to ~31 RPS.
- Same two VMs, same API process => the SQL ceiling is the client driver's
  large-value fetch path, NOT the network. Do not attribute it to the network.

REASSEMBLY IS CHEAP (MEASURED, server-side median at 50 RPS)
  SQL hybrid reassembly: 3.55 ms at 0.50 MB, 12.79 ms at 1.97 MB, 15.06 ms at
  4.76 MB - against 8-37 ms of database time in the same runs. The cost of
  decomposition is round trips and request units, NOT reassembly CPU.

SINGLE-REQUEST LATENCY BY SIZE, POINT READ (MEASURED, ms)
  payload    A. nvarchar(max)   A'. native json   B. one BSON doc
  0.93 MB    11.3               42.6              48.0
  3.05 MB    23.5               127.3             163.1
  4.87 MB    36.0               203.6             247.2
  15.04 MB*  114.4              624.3             931.0
  16.70 MB*  133.2              680.3             not stored
  (* boundary/stress probes, above the customer's stated range)

INSERT, ONE COMPLETE ORDER (MEASURED, ms)
  4.87 MB:   A 802.8   A' 2,849.5   B 276.2
  15.04 MB*: A 1,502.5 A' 15,019.8  B 960.7
  Bulk ingest of 500 orders: Mongo 35.9 s (13.92/s), SQL nvarchar 161.4 s
  (3.10/s), SQL native json 224.1 s (2.23/s).

WHY THE NATIVE json TYPE LOSES - the mechanism, not a mystery
  A whole-document workload pays CAST(? AS json) to parse and binary-encode on
  write, then CAST(... AS nvarchar(max)) to serialise back on read, and NEVER
  queries the binary form in between. The native type is built for partial
  access (JSON_VALUE, JSON_QUERY, in-place modify); this workload uses none of
  it and pays all of its conversion cost. It is also unmirrorable to Fabric:
  "A table can't be mirrored if it has the json or vector data type"
  (DOCUMENTED). Three independent reasons against it, all established.
  CONSEQUENCE: the Fabric mirroring constraint costs NOTHING here - it forces
  the representation that was faster anyway. (This CORRECTS an earlier framing
  in the project that called nvarchar(max) a compromise.)

COSMOS MONGO WRITE COST TRACKS DOCUMENT SIZE, NOT CHANGE SIZE (MEASURED, RU)
  payload    read   insert    one scalar $set   one nested $set   full replace
  0.49 MB    7.9    373.6     410.5             328.2             283.2
  0.93 MB    13.1   1,250.4   1,396.3           955.9             810.0
  4.87 MB    57.2   4,269.0   4,767.5           3,956.1           3,457.6
  15.04 MB*  199.4  15,368.1  17,163.6          14,532.9          12,737.0
  => ~12 RU/MB to read, ~1,000 RU/MB to write. Setting ONE top-level scalar on a
  4.87 MB document costs MORE (4,767.5 RU) than replacing the whole document
  (3,457.6 RU), because $set forces a server-side read-modify-write. There is no
  cheap small edit to a large document.

REQUEST-UNIT RATIO FOR WHOLE-ORDER SERVING
  Design C (reassembled, ~34 items): ~515 RU per whole-order read (under-load
  derivation used for costing; the isolated Part 1 figure was 1,187.5 RU).
  Design B (one document): ~16 RU for an order of the same mean size (ESTIMATED,
  interpolated between measured 13.1 RU at 0.93 MB and 57.2 RU at 4.87 MB).
  => Whole-order serving from a decomposed design costs on the order of 30x more
  per request. This is the real price of the 2 MB item limit.

SECURITY AND GOVERNANCE - DOCUMENTED, ESTABLISHED BEFORE ANY CODE
  1. EnableMongo16MBDocumentSupport is INCOMPATIBLE with customer-managed keys
     (CMK) and CANNOT BE REMOVED once enabled. An account built for 16 MB
     documents can NEVER be brought under CMK. For a payload carrying SSNs, wire
     instructions and loan detail, this disqualifies design B wherever CMK is a
     stated control - regardless of performance. THIS IS A HEADLINE, NOT AN
     APPENDIX ITEM.
  2. The Cosmos DB for MongoDB (RU) API has NO Entra ID data-plane
     authentication. Account keys only. The documented mitigation - fetch the key
     with a managed identity (implemented in this POC) - requires a CONTROL-PLANE
     role able to list account keys, a strictly broader grant than data access,
     and it cannot be scoped per collection or made read-only.
  3. No native Fabric mirroring for the MongoDB API. Design B is the only one
     that cannot use the same analytics path as the others.
  4. Capabilities cannot be set via ARM/Bicep for MongoDB accounts (CLI or
     portal only), so that account is not fully declarable as IaC.
  5. The 16 MB limit applies ONLY to collections created AFTER the capability is
     enabled. Wrong provisioning order leaves a silent 2 MB ceiling.
  6. Fabric mirroring from Cosmos DB supports the NoSQL API only.

COST (rates pulled live from the Azure Retail Prices API, westus3, USD, 730h)
  Azure SQL, GP provisioned 2 vCore:       $222/month, FLAT - does not move with
                                           API request shape
  Cosmos DB, by what the API actually serves (provisioned, +50% headroom):
      summary + search only   ~5.8 RU/req  -> $23/month
      realistic mixture       ~97 RU/req   -> $426/month
      whole orders only       ~515 RU/req  -> $2,254/month
  => a 97x spread on identical data, driven purely by API design.
  Storage at 441 GB: Azure SQL $50.72, Cosmos $110.25. Two-year archive
  (1,935 GB): $32.70 hot, $19.35 cool. Fabric F2 24x7: $262.80.

RECOMMENDATION
  Scenario A - Azure SQL, one complete order per nvarchar(max) row - is the
  strongest design measured. It stores the complete order at every size tested,
  reads and writes it fastest at the stated workload, hits 50 RPS at p50 17.2 ms
  with zero errors, mirrors to Fabric, authenticates with Entra ID like every
  other component, and is declarable as IaC. There is no trade-off to justify.
  Design B works and is faster above 50 RPS, but would be chosen DESPITE three
  governance constraints rather than because of a measured advantage.

WHAT IS EXPLICITLY NOT ESTABLISHED - include when the audience is technical
  - Cosmos NoSQL vs Mongo provisioning was unequal (6,000 vs 10,000 RU/s).
  - Azure DocumentDB (the renamed Cosmos DB for MongoDB vCore) was NOT built or
    benchmarked. It would resolve constraints 1 and 2 above and is Microsoft's
    recommended destination for MongoDB-compatible workloads. Documented only.
  - Microsoft's own documentation now routes new work AWAY from Cosmos DB for
    MongoDB (RU) - toward Azure DocumentDB for MQL, or Cosmos DB for NoSQL for
    high scale. The 16 MB capability exists only on the API being steered away
    from.
  - The Mongo wildcard-index cost experiment is written but not yet run.
  - The live five-way API equivalence test has not been run against live
    resources.
  - Analytics freshness is not currently reproducible (the custom mirroring
    extractor stalled; rebuild pending a decision).
  - Single region, single replica, no failover testing. Two-year retention was
    modelled from measured per-order sizes, not accumulated.

HOW IT WAS MEASURED
  Two Azure VMs in the same VNet as the data stores: vm-orderjsonpoc-load
  (D4s_v5, 4 vCPU) generating load, vm-orderjsonpoc-api (D8s_v5, 8 vCPU) running
  FastAPI with 8 uvicorn workers. They are TEST EQUIPMENT, not part of the
  proposed architecture. Separate machines because 50 RPS x ~1.3 MB is ~65 MB/s
  sustained (unmeasurable over a consumer link), because tenant policy forced
  private-only endpoints so there was no public path to test, and because
  co-locating would send multi-MB responses over loopback and make the
  generator's CPU compete with the API's. Load is OPEN-MODEL (fixed arrival
  schedule, latency timed scheduled-to-complete) so queueing appears in the
  numbers instead of being hidden by coordinated omission. Every published figure
  is generated by script from machine-readable run files; none is typed by hand.

=== END FACT SHEET
```

---

## 2. PROMPT — Executive deck (12 slides)

```text
You are preparing a 12-slide executive briefing for a Microsoft account team and
their customer's architecture leadership. The customer is a title and escrow
business evaluating how to store and serve large order JSON documents on Azure.

Audience: senior technical decision-makers. They are comfortable with cloud
concepts but are NOT database specialists. They have limited time and will act on
the recommendation.

Produce exactly these 12 slides. For each, give a headline (a claim, not a
category label), 3-5 bullets of at most 12 words each, and a note on what visual
belongs there.

1.  The question, in the customer's own words, and the answer in one line.
2.  Three terms that were being confused: LOGICAL ORDER vs DATABASE ITEM vs API
    RESPONSE. This slide is why the earlier discussion went in circles.
3.  The five designs tested, one line each, with which ones store the order as
    one item.
4.  Can one database record hold a complete order? The size-limit table.
5.  Results at the stated 50 requests/sec. Include the row-C provisioning caveat
    ON THE SLIDE, not in a footnote.
6.  The trap: the native json column type collapses, and the three independent
    reasons against it.
7.  Cost shape: Azure SQL is flat, Cosmos DB follows the API design, 97x spread.
8.  The security finding: 16 MB Mongo documents mean no customer-managed keys,
    permanently and irreversibly. This slide must be blunt.
9.  The recommendation, with the six reasons it wins.
10. What the recommendation is NOT saying (four honest qualifications).
11. What we did not measure, and what would change the answer.
12. Next steps and the decisions the customer owns.

RULES
- Use ONLY the figures in the fact sheet below. Invent nothing - no extra
  percentiles, no rounded-up limits, no cost figures not listed.
- Never present design C (Cosmos NoSQL) as having "failed". It was throttled by
  deliberate under-provisioning. Slide 5 must say so.
- Do not soften slide 8. The customer-managed-key conflict is permanent and
  belongs in the main narrative.
- Mark anything not measured as documented, estimated or not established.
- Plain English. Expand every acronym on first use. No vendor superlatives.

[PASTE THE FACT SHEET FROM SECTION 1 HERE]
```

---

## 3. PROMPT — Technical deck (20 slides)

```text
You are preparing a 20-slide technical deep-dive for database and platform
engineers who will implement, review or challenge this design. They will ask how
each number was produced and will not accept an unsourced claim.

Produce 20 slides. Each needs a headline claim, up to 6 technical bullets, and -
where a figure appears - the measurement that produced it.

1.  Scope, and the two questions this POC answers (API-level vs storage-level).
2.  Terminology discipline: logical order / database item / API response.
3.  Source data profile: 106 sections, top 10 = 87.9% of bytes, 33% empty scalars.
4.  Test architecture: one API, five interchangeable storage backends, one
    contract.
5.  Measurement method: open-model load, scheduled-to-complete latency, why
    coordinated omission would have produced reassuring nonsense.
6.  The two-VM topology and why co-location would have invalidated the results.
7.  Scenario A schema: one row, nvarchar(max) + ISJSON, projection columns,
    indexes that never parse JSON.
8.  Scenario B: one BSON document, the 16 MB capability, provisioning order,
    BSON > JSON bytes.
9.  Document-size acceptance limits and exact failure modes per product.
10. Single-request latency by payload size, all three full-document variants.
11. The 50 RPS sweep, all five designs, with the provisioning caveat.
12. The 100 RPS inversion, and what it says about headroom at 50.
13. Locating the throughput ceiling: 240.8 vs ~150 MB/s on identical
    infrastructure, therefore client-side, not network.
14. Reassembly cost measured: 3.55-15.06 ms, versus round trips and request
    units. What decomposition actually costs.
15. Cosmos Mongo write economics: update cost tracks document size; one $set
    exceeds a full replace; the mechanism.
16. Why the native json type loses: cast-in on write, cast-out on read, no
    partial access in between - plus the Fabric mirroring prohibition.
17. Security and governance constraints, with the Microsoft documentation behind
    each.
18. Fabric analytics implications per design, including which one has no native
    mirroring.
19. Cost model: RU per request shape vs flat vCore, and the 97x spread.
20. Recommendation, open questions, and the reproduction commands.

RULES
- Use ONLY fact-sheet figures. Preserve units and significant figures exactly:
  16.70 MB, not "about 17 MB". 55,591 ms may be written as "55.6 s" but not
  rounded to "about a minute".
- Tag every claim MEASURED, DOCUMENTED, ESTIMATED or NOT ESTABLISHED.
- State mechanisms, not just outcomes. An engineer will ask "why" at slides 13,
  15 and 16; answer it on the slide.
- Include slide 11's provisioning disparity as a bullet, not a footnote.
- Do not editorialise about products. Every negative finding must be traceable to
  a measurement or a Microsoft document.

[PASTE THE FACT SHEET FROM SECTION 1 HERE]
```

---

## 4. PROMPT — The architecture diagram

```text
Draw one clear architecture diagram of the recommended design, suitable for a
slide. Output Mermaid (flowchart TB). If you cannot output Mermaid, output a
precise layout description instead.

CONTENT, top to bottom:
- Source: order JSON, 0.5-5 MB, deeply nested, one file per order.
- Two destinations from ingest, side by side:
  (a) immutable raw archive in ADLS Gen2, gzipped, two-year retention;
  (b) Azure SQL Database, table ord.OrderDocuments.
- Inside the Azure SQL table box, show ONE ROW containing:
  * key columns: OrderId (PK), CustomerId, OrderVersion
  * routing/index metadata columns: OrderNumber, Status, PrimaryState,
    MaxLoanAmount, TotalLoanAmount, PropertyCount, LoanCount, PartyCount,
    PayloadBytes, PayloadHash, UpdatedAt
  * SummaryJson (precomputed, so the hot endpoint never opens the payload)
  * JsonPayload nvarchar(max) - THE COMPLETE ORDER - with CHECK ISJSON = 1
  Emphasise JsonPayload visually: it is the point of the design.
- Nonclustered indexes covering only the projection columns, annotated "a
  filtered scan never parses JSON".
- Read paths out of the table: full order (SELECT JsonPayload, returned as raw
  bytes), single block (server-side JSON_QUERY), summary (SummaryJson), search
  (index seek on projection columns).
- The REST API in front, ~50 reads/sec, authenticating to SQL with Entra ID
  managed identity - no keys anywhere.
- Microsoft Fabric mirroring out of the table into OneLake, then Lakehouse,
  Warehouse and Power BI.
- A small annotation on the payload column: "nvarchar(max), not the native json
  type - measured 6x faster to read and 10x faster to write for a
  whole-document workload, and the native type cannot be mirrored to Fabric."

STYLE
- Left-to-right or top-to-bottom, no crossing edges.
- Quote every node label so parentheses and slashes do not break the parser.
- Use <br/> for line breaks inside labels. ASCII only.
- Green for the payload column, blue for metadata, grey for the archive.
- No figures beyond those given here.
```

---

## 5. PROMPT — The five-designs comparison diagram

```text
Draw a single diagram that makes ONE point: every design returns the same
complete-order API response, but only three of them physically store the
complete order as one database item. Output Mermaid (flowchart TD).

STRUCTURE
- Top: "FULL LOGICAL ORDER JSON, 1-5 MB, deeply nested".
- A decision node: "stored as ONE database item?"
- Branch YES to three boxes:
  * A. SQL Full JSON - Azure SQL, ONE ROW, nvarchar(max) + ISJSON, accepted to
    16.70 MB
  * A'. SQL native json - Azure SQL, ONE ROW, native json type, accepted to
    16.70 MB, but 13.9 RPS and p50 55.6 s at the target load
  * B. Cosmos Mongo - ONE BSON DOCUMENT, 16 MB capability required, accepted to
    15.04 MB
- Branch NO to a "decomposition along business boundaries" node, then:
  * D. SQL hybrid - 8 relational tables + ~34 JSON blocks
  * C. Cosmos NoSQL - ~34 items per order, 2 MB per-item ceiling (measured:
    1.78 MB accepted, 2.01 MB refused with HTTP 413)
- All five converge on ONE node: "SAME API CONTRACT - GET /orders/{id} returns
  the complete order - equivalence asserted by test".
- Annotate the two decomposed branches "+ reassembly on read, measured
  3.55-15.06 ms".
- Attach the 50 RPS result to each box as a short line: A 50.0 RPS / 17.2 ms;
  A' 13.9 RPS / 55.6 s / 3.82% errors; B 49.9 RPS / 66.9 ms; D 49.0 RPS /
  29.5 ms; C 12.4 RPS / 65.2 s, marked "throttled at 6,000 RU/s, not
  incapability".
- Mark A as the recommendation and A' with a clear warning treatment.

STYLE: quoted labels, <br/> line breaks, ASCII only, no crossing edges, green
for the recommendation, red for A', amber for the rest. Add no figures of your
own.
```

---

## 6. PROMPT — The benchmark topology diagram

```text
Draw the measurement setup, for a methodology slide that has to survive a
sceptical engineer. Output Mermaid (flowchart LR).

CONTENT
- Operator workstation, annotated "outbound SSH blocked by policy - every VM
  action runs through az vm run-command".
- One VNet box, same Azure region as every data store, containing two subnets:
  * snet-compute: vm-orderjsonpoc-load (D4s_v5, 4 vCPU, load generator) and
    vm-orderjsonpoc-api (D8s_v5, 8 vCPU, FastAPI with 8 uvicorn workers, one
    storage backend active at a time, /health asserted before every run).
  * snet-data: private endpoints only - Azure SQL, Cosmos DB for NoSQL, Cosmos DB
    for MongoDB, ADLS Gen2 archive.
- A thick edge from the load VM to the API VM labelled "HTTP over a real NIC,
  accelerated networking, multi-MB responses".
- Edges from the API VM to each data store.
- A side box titled "Why two VMs", with three points: 50 RPS x ~1.3 MB is
  ~65 MB/s sustained and unmeasurable over a consumer link; tenant policy forced
  private-only endpoints so there was no public path to test; co-location would
  send responses over loopback at memory speed and make the generator's CPU
  compete with the API's.
- A second side box titled "What the split proved": Cosmos Mongo sustained
  240.8 MB/s at 4.86 MB and 49.6 RPS while both SQL designs flattened at
  ~150 MB/s - same machines, same API process, therefore the SQL ceiling is the
  client driver's large-value path and NOT the network.
- A closing note: "the VMs are test equipment, not part of the proposed
  architecture".

STYLE: quoted labels, <br/> line breaks, ASCII only. Keep it readable at slide
size - no more than 14 nodes.
```

---

## 7. PROMPT — One-page summary or customer email

```text
Write a one-page summary of this POC for the customer's VP of Engineering, who
was not in any of the technical sessions.

Constraints:
- Open with the answer, not the background. The first sentence must state whether
  Microsoft can do what they asked.
- At most 500 words, plus one small table of your choosing.
- No acronym without its expansion. No product superlatives.
- Include, in the body and not as a footnote: the 16 MB / customer-managed-key
  conflict, and the fact that one Cosmos result reflects deliberate
  under-provisioning rather than a product limit.
- Close with the two or three decisions that are theirs to make: the real
  sustained request rate, whether orders are updated in place, and whether
  customer-managed keys are a required control.
- Use only fact-sheet figures.

[PASTE THE FACT SHEET FROM SECTION 1 HERE]
```

---

## 8. PROMPT — Speaker notes

```text
For each slide I give you, write speaker notes: 60-90 spoken words, in the first
person plural, that a presenter can read aloud without rehearsal.

For every slide carrying a number, the notes must say HOW it was measured in one
clause - for example "measured across 46 runs from a separate load VM in the same
VNet". For every slide carrying a limitation, the notes must say whether it came
from measurement or from Microsoft documentation.

Also write, for each slide, the single most likely challenge from the audience
and a one-sentence answer grounded in the fact sheet. Where the honest answer is
"we did not measure that", say exactly that and name what would be needed.

Never introduce a figure that is not in the fact sheet.

[PASTE THE FACT SHEET FROM SECTION 1 HERE]
[PASTE YOUR SLIDE LIST HERE]
```

---

## 9. If you would rather not use a prompt

Seven Mermaid diagrams are committed in [../diagrams/](../diagrams/) and render
directly in GitHub, VS Code and most Markdown tools:

| File | Shows |
| --- | --- |
| [terms.mmd](../diagrams/terms.mmd) | logical order vs database item vs API response |
| [api-backends.mmd](../diagrams/api-backends.mmd) | one API contract, five interchangeable stores |
| [sql-full-json.mmd](../diagrams/sql-full-json.mmd) | the recommended design in detail |
| [cosmos-mongo.mmd](../diagrams/cosmos-mongo.mmd) | the single-BSON-document design and its constraints |
| [full-document-comparison.mmd](../diagrams/full-document-comparison.mmd) | the four full-document scenarios side by side |
| [benchmark-topology.mmd](../diagrams/benchmark-topology.mmd) | the measurement setup and what the two VMs were for |
| [decision-tree.mmd](../diagrams/decision-tree.mmd) | which design, given which constraint |

Plus the Part 1 diagrams: [end-to-end.mmd](../diagrams/end-to-end.mmd),
[sql-hybrid.mmd](../diagrams/sql-hybrid.mmd),
[cosmos-aggregate.mmd](../diagrams/cosmos-aggregate.mmd),
[comparison.mmd](../diagrams/comparison.mmd),
[fabric-analytics.mmd](../diagrams/fabric-analytics.mmd) and
[fabric-direct-serving-control.mmd](../diagrams/fabric-direct-serving-control.mmd).
