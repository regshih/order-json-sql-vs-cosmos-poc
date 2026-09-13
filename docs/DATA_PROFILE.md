# Source Data Profile

Generated `2026-09-13T18:01:32.931584+00:00` by [tools/profile_source.py](../tools/profile_source.py).

> **Structure only.** This document and `artifacts/data-profile.json` contain
> no customer values — every string is reduced to a byte length, every GUID to an
> occurrence count. The source sample itself is git-ignored and never committed.

## 1. Envelope

The file is not a bare order. It is an *extract envelope*:

```
ExtractDetails { CustomerSerialNumber, ExtractDateTimeUTC, ExtractServer, ExtractType }
ExtractData
  └── ExtractObjects[1]
        ├── ObjectDetails { ObjectType, OrderID, OrderVersion }
        └── ObjectData    { 106 properties }
```

**Design consequence:** the envelope carries the version/identity metadata that both
storage paths need for keys, and `ObjectData` is the actual payload to model.

## 2. Measured size

| Measure | Value |
| --- | --- |
| Raw file (as supplied, formatted) | 881,010 bytes (860.4 KiB) |
| Compact UTF-8 (no whitespace) | 551,747 bytes (538.8 KiB) |
| Whitespace overhead | 37.4% |
| `ObjectData` compact | 551,440 bytes |

**Compact bytes is the number that matters** — it is what a Cosmos item is measured
against, what travels over the API, and what `nvarchar(max)` stores.

## 3. Shape and sparsity

| Measure | Value |
| --- | --- |
| Max nesting depth | 16 |
| Objects | 4,552 |
| Arrays | 872 |
| Scalars | 18,000 |
| Keys | 22,558 |
| Empty strings | 5,911 (32.8% of scalars) |
| Empty objects | 1,556 |
| Empty arrays | 457 |
| Nulls | 0 |

## 4. Top-level sections by size

| # | Section | Compact bytes | % of ObjectData | Kind | Array len | Treatment |
| --- | --- | ---: | ---: | --- | ---: | --- |
| 1 | `CDFs` | 199,337 | 36.15% | array | 1 | json-block-large-split-candidate |
| 2 | `Title` | 163,859 | 29.71% | object |  | json-block-large-split-candidate |
| 3 | `ChecklistTasks` | 23,313 | 4.23% | array | 48 | json-block-large-split-candidate |
| 4 | `RequestedTasks` | 18,666 | 3.38% | array | 22 | json-block |
| 5 | `CDFAmounts` | 17,136 | 3.11% | object |  | json-block |
| 6 | `Properties` | 14,597 | 2.65% | array | 1 | json-block |
| 7 | `Loans` | 13,412 | 2.43% | array | 1 | json-block |
| 8 | `GeneralNotes` | 12,036 | 2.18% | array | 13 | json-block |
| 9 | `Notes` | 12,036 | 2.18% | array | 13 | json-block |
| 10 | `Others` | 10,108 | 1.83% | array | 6 | json-block |
| 11 | `Buyers` | 7,752 | 1.41% | array | 1 | json-block |
| 12 | `TitleCompanies` | 6,601 | 1.20% | array | 3 | json-block |
| 13 | `Invoices` | 4,046 | 0.73% | array | 3 | json-block |
| 14 | `Lenders` | 3,883 | 0.70% | array | 1 | json-block |
| 15 | `Sellers` | 3,640 | 0.66% | array | 1 | json-block |
| 16 | `ExistingLiens` | 3,441 | 0.62% | array | 1 | json-block |
| 17 | `SettlementAgents` | 3,405 | 0.62% | array | 1 | json-block |
| 18 | `Deeds` | 3,368 | 0.61% | array | 1 | json-block |
| 19 | `ListingAgentBrokers` | 2,909 | 0.53% | array | 1 | json-block |
| 20 | `Governments` | 2,898 | 0.53% | array | 2 | json-block |
| 21 | `Underwriters` | 2,866 | 0.52% | array | 1 | json-block |
| 22 | `RecordingAndTransferTaxes` | 2,569 | 0.47% | object |  | json-block |
| 23 | `MilestoneTasks` | 2,405 | 0.44% | array | 4 | json-block |
| 24 | `PayoffLenders` | 1,711 | 0.31% | array | 1 | json-block-small |
| 25 | `Escrow` | 1,576 | 0.29% | object |  | relational-candidate-small |
| 26 | `Attorneys` | 1,521 | 0.28% | array | 1 | json-block-small |
| 27 | `SellingAgentBrokers` | 1,489 | 0.27% | array | 1 | json-block-small |
| 28 | `HazardInsuranceAgents` | 1,460 | 0.26% | array | 1 | json-block-small |
| 29 | `SettlementLocation` | 1,285 | 0.23% | object |  | relational-candidate-small |
| 30 | `SalesContract` | 1,067 | 0.19% | object |  | relational-candidate-small |

The top 10 sections hold **87.9%** of `ObjectData` (484,500 bytes). 76 of 106 sections are under 1 KiB.

**Design consequence:** size is highly concentrated. A small number of sections drive
nearly all bytes, which is exactly what makes a *block* model (SQL JSON blocks / Cosmos
aggregate items) viable — split the few big ones, leave the long tail alone.

## 5. Largest array cardinalities

| Path | Occurrences | Min | Max | Total elements |
| --- | ---: | ---: | ---: | ---: |
| `$.ExtractData.ExtractObjects[].ObjectData.ChecklistTasks` | 1 | 48 | 48 | 48 |
| `$.ExtractData.ExtractObjects[].ObjectData.Title.TitleProducts[].Exceptions` | 3 | 0 | 21 | 30 |
| `$.ExtractData.ExtractObjects[].ObjectData.RequestedTasks` | 1 | 22 | 22 | 22 |
| `$.ExtractData.ExtractObjects[].ObjectData.Title.Commitments[].Exceptions` | 1 | 21 | 21 | 21 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFs[].ServiceNotShoppedForSection.Lines[].Charges[].Payors` | 10 | 2 | 2 | 20 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFAmounts.Disbursements[].CDFPayees` | 8 | 1 | 11 | 19 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFAmounts.Disbursements[].Payees` | 8 | 1 | 11 | 19 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFs[].DueFromSellerSection.Lines` | 1 | 19 | 19 | 19 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFs[].DueFromSellerSection.Lines[].Charges` | 19 | 1 | 1 | 19 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFs[].DueToBuyerSection.Lines` | 1 | 17 | 17 | 17 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFs[].DueToBuyerSection.Lines[].Charges` | 17 | 1 | 1 | 17 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFs[].DueToSellerSection.Lines` | 1 | 16 | 16 | 16 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFs[].DueToSellerSection.Lines[].Charges` | 16 | 1 | 1 | 16 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFs[].EscrowSection.Lines[].Charges[].Payors` | 8 | 2 | 2 | 16 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFs[].OriginationChargeSection.Lines[].Charges[].Payors` | 8 | 2 | 2 | 16 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFs[].OtherCostSection.Lines[].Charges[].Payors` | 8 | 2 | 2 | 16 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFs[].ServiceShoppedForSection.Lines[].Charges[].Payees` | 8 | 1 | 3 | 16 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFs[].ServiceShoppedForSection.Lines[].Charges[].Payors` | 8 | 2 | 2 | 16 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFs[].DueFromBuyerSection.Lines` | 1 | 15 | 15 | 15 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFs[].DueFromBuyerSection.Lines[].Charges` | 15 | 1 | 1 | 15 |

**Design consequence:** these are the arrays that grow. Document growth in a real order
comes from more elements in these arrays, not from wider objects — which is how the
synthetic generator must scale payloads, and which arrays the Cosmos splitter must
be able to chunk.

## 6. Large text / RTF fields

- Strings ≥ 512 bytes: **0**, totalling 0 bytes (0.0% of ObjectData); largest single value 0 bytes.
- RTF-encoded fields (`{\rtf…`): **95**, totalling 54,912 bytes; largest 1,706 bytes.

**Design consequence:** large free-text/RTF is pure pass-through. It is never filtered,
joined or sorted on, so it belongs in a JSON block (SQL) or a payload item (Cosmos) and
should be excluded from Cosmos indexing.

## 7. GUID reference structure

| Measure | Value |
| --- | ---: |
| Distinct GUID values | 1,194 |
| Total GUID occurrences | 2,133 |
| GUIDs appearing more than once | 209 |
| Max occurrences of one GUID | 129 |

Top GUID-bearing paths:

| Path | Count |
| --- | ---: |
| `$.ExtractData.ExtractObjects[].ObjectData.ChecklistTasks[].Guid` | 48 |
| `$.ExtractData.ExtractObjects[].ObjectData.Title.TitleProducts[].Exceptions[].Guid` | 30 |
| `$.ExtractData.ExtractObjects[].ObjectData.RequestedTasks[].Guid` | 22 |
| `$.ExtractData.ExtractObjects[].ObjectData.Title.Commitments[].Exceptions[].Guid` | 21 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFs[].ServiceNotShoppedForSection.Lines[].Charges[].Payors[].Contact.Guid` | 20 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFs[].ServiceNotShoppedForSection.Lines[].Charges[].Payors[].Guid` | 20 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFs[].ServiceNotShoppedForSection.Lines[].Charges[].Payors[].OnBehalfOf.Guid` | 20 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFAmounts.Disbursements[].CDFPayees[].CDFAmount.Guid` | 19 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFAmounts.Disbursements[].CDFPayees[].Contact.Guid` | 19 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFAmounts.Disbursements[].CDFPayees[].Guid` | 19 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFAmounts.Disbursements[].CDFPayees[].PayTo.Guid` | 19 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFAmounts.Disbursements[].Payees[].Contact.Guid` | 19 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFAmounts.Disbursements[].Payees[].Guid` | 19 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFs[].DueFromSellerSection.Lines[].Charges[].Calculation.Guid` | 19 |
| `$.ExtractData.ExtractObjects[].ObjectData.CDFs[].DueFromSellerSection.Lines[].Charges[].Guid` | 19 |

**Design consequence:** repeated GUIDs are intra-document foreign keys — the same party
or property referenced from several sections. They are the natural join keys for the
relational extraction and the natural `id` seeds for deterministic Cosmos item ids.

## 8. Candidate aggregate boundaries

| Block type | Compact bytes | Needs sub-split | Natural sub-blocks |
| --- | ---: | --- | --- |
| `CDFS` | 199,337 | yes | — |
| `TITLE` | 163,859 | yes | TitleProducts, Commitments, LoanPolicies, AdditionalCharges, Endorsements, OwnersPolicies |
| `CHECKLISTTASKS` | 23,313 | no | — |
| `REQUESTEDTASKS` | 18,666 | no | — |
| `CDFAMOUNTS` | 17,136 | no | Disbursements, Receipts, Guid, TotalDisbursements, TotalReceipts |
| `PROPERTIES` | 14,597 | no | — |
| `LOANS` | 13,412 | no | — |
| `GENERALNOTES` | 12,036 | no | — |
| `NOTES` | 12,036 | no | — |
| `OTHERS` | 10,108 | no | — |
| `BUYERS` | 7,752 | no | — |
| `TITLECOMPANIES` | 6,601 | no | — |

## 9. Modelling conclusions

**Relational (searched / joined / filtered / sorted / secured):**

- Order identity and lifecycle: order id, version, type, status, project, dates
- Customer / tenant key
- Property address components (state and county drive reporting)
- Parties with a role discriminator — one party table, not one per role
- Loan amount, type and number

**JSON blocks (deep, sparse, variable, pass-through):**

- `CDFs` (199,337 bytes, 36.15%) — *split candidate*
- `Title` (163,859 bytes, 29.71%) — *split candidate*
- `ChecklistTasks` (23,313 bytes, 4.23%) — *split candidate*
- `RequestedTasks` (18,666 bytes, 3.38%)
- `CDFAmounts` (17,136 bytes, 3.11%)
- `Properties` (14,597 bytes, 2.65%)
- `Loans` (13,412 bytes, 2.43%)
- `GeneralNotes` (12,036 bytes, 2.18%)
- `Notes` (12,036 bytes, 2.18%)
- `Others` (10,108 bytes, 1.83%)

**Why not fully normalise:** max nesting depth is 16 and there are 4,552 objects across 106 top-level sections. A faithful relational decomposition would run to well over a hundred tables for data that is almost entirely read back as-is. See [SQL_DESIGN.md](SQL_DESIGN.md) §control.

**Why not one blob:** the fields in the relational list above appear in every search, filter and report. Leaving them inside a multi-megabyte blob forces a full scan plus JSON parse per row for queries that should be an index seek.

