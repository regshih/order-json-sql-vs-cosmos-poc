"""Deterministic synthetic order generator.

Reproduces the *structure* measured in docs/DATA_PROFILE.md - the extract
envelope, the ~100 ObjectData sections, the CDF section/line hierarchy, the
Title commitment/exception/policy hierarchy, the common party shape - using
entirely invented values.

Determinism: a given (seed, order_index) always produces byte-identical JSON.
Payload size is grown through real business structures (more CDF lines, more
title exceptions, more disbursements, more notes, more tasks), never padding.

Usage:
    python generator/synthetic_order_generator.py --orders 100 --seed 42 \
        --out data/generated/orders
    python generator/synthetic_order_generator.py --calibrate
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from generator.profiles import (  # noqa: E402
    CDF_SECTIONS,
    CHARGE_DESCRIPTIONS,
    CITIES,
    COMPANY_STEMS,
    COMPANY_SUFFIXES,
    COUNTIES,
    ENDORSEMENT_NAMES,
    FIRST_NAMES,
    GrowthDials,
    LAST_NAMES,
    LOAN_TYPES,
    NOTE_BODIES,
    ORDER_STATUS_WEIGHTS,
    ORDER_STATUSES,
    PROFILES_BY_NAME,
    PROJECTS,
    SETTLEMENT_TYPES,
    SIZE_PROFILES,
    STATES,
    STREET_NAMES,
    STREET_TYPES,
    TASK_NAMES,
    TITLE_EXCEPTION_TEXTS,
    TITLE_REQUIREMENT_TEXTS,
    TRANSACTION_TYPES,
    SizeProfile,
)

EPOCH = date(2024, 1, 1)

# Matches the measured sparsity of the sample: ~33% of scalars are "".
EMPTY_STRING_RATE = 0.33


def compact_bytes(obj: Any) -> int:
    return len(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


class OrderFactory:
    """Builds one synthetic order. All randomness flows from ``self.rng``."""

    def __init__(self, rng: random.Random, dials: GrowthDials) -> None:
        self.rng = rng
        self.d = dials
        self._guid_pool: list[str] = []

    # -- primitives ---------------------------------------------------------

    def guid(self, reuse_p: float = 0.0) -> str:
        """Deterministic GUID. With probability ``reuse_p`` returns a GUID that
        has already been issued, reproducing the intra-document foreign-key
        pattern measured in the sample (209 GUIDs appearing more than once)."""
        if self._guid_pool and self.rng.random() < reuse_p:
            return self.rng.choice(self._guid_pool)
        g = str(uuid.UUID(int=self.rng.getrandbits(128), version=4))
        self._guid_pool.append(g)
        return g

    def maybe(self, value: str) -> str:
        """Sparse string - empty at the measured rate."""
        return "" if self.rng.random() < EMPTY_STRING_RATE else value

    def money(self, lo: float = 50, hi: float = 25000) -> str:
        # The sample carries money as formatted strings, not numbers.
        return f"{self.rng.uniform(lo, hi):.2f}"

    def some_date(self, lo_days: int = 0, hi_days: int = 700) -> str:
        d = EPOCH + timedelta(days=self.rng.randint(lo_days, hi_days))
        return d.isoformat() + "T00:00:00"

    def phone(self) -> str:
        return f"555-{self.rng.randint(100, 999)}-{self.rng.randint(1000, 9999)}"

    def person_name(self) -> tuple[str, str]:
        return self.rng.choice(FIRST_NAMES), self.rng.choice(LAST_NAMES)

    def company_name(self) -> str:
        return f"{self.rng.choice(COMPANY_STEMS)} {self.rng.choice(COMPANY_SUFFIXES)}"

    def rtf(self, body: str) -> str:
        """RTF-wrapped text, mirroring the 95 RTF fields in the sample."""
        return (
            r"{\rtf1\ansi\ansicpg1252\deff0{\fonttbl{\f0\fnil\fcharset0 Segoe UI;}}"
            r"\viewkind4\uc1\pard\f0\fs18 " + body.replace("\\", "") + r"\par}"
        )

    # -- composite building blocks -----------------------------------------

    def address(self) -> dict[str, Any]:
        num = self.rng.randint(100, 9999)
        street = f"{self.rng.choice(STREET_NAMES)} {self.rng.choice(STREET_TYPES)}"
        city = self.rng.choice(CITIES)
        code, desc = self.rng.choice(STATES)
        zip_ = f"{self.rng.randint(10000, 99999)}"
        county = self.rng.choice(COUNTIES)
        a1 = f"{num} {street}"
        return {
            "Address1": a1,
            "Address1Address2": a1,
            "Address2": self.maybe(f"Unit {self.rng.randint(1, 40)}"),
            "City": city,
            "CityStateZip": f"{city}, {code} {zip_}",
            "Country": {},
            "County": county,
            "ForeignAddress": "",
            "FullAddress": f"{a1}, {city}, {code} {zip_}",
            "SiteFullAddress": f"{a1}, {city}, {code} {zip_}",
            "SiteName": self.maybe(street),
            "SiteNumber": str(num),
            "SiteNumberSiteName": a1,
            "State": {"Code": code, "Description": desc, "GLC": self.maybe(code)},
            "UseForeignAddress": False,
            "Zip": zip_,
        }

    def person(self) -> dict[str, Any]:
        first, last = self.person_name()
        return {
            "Name": f"{first} {last}",
            "Address": self.address(),
            "AKA": self.maybe(f"{first[0]}. {last}"),
            "Cell": self.maybe(self.phone()),
            "CourtesyTitle": self.maybe(self.rng.choice(["Mr.", "Ms.", "Dr."])),
            "Email": f"{first.lower()}.{last.lower()}@example.invalid",
            "Fax": self.maybe(self.phone()),
            "FirstName": first,
            "FullName": f"{first} {last}",
            "Gender": "",
            "Guid": self.guid(0.05),
            "Home": self.maybe(self.phone()),
            "IsMainPerson": True,
            "LastName": last,
            "License": {},
            "LicenseNumber": self.maybe(f"LIC{self.rng.randint(100000, 999999)}"),
            "LookupCode": self.maybe(f"{last[:4].upper()}{self.rng.randint(10, 99)}"),
            "MiddleName": self.maybe(self.rng.choice(FIRST_NAMES)[0]),
            "NationwideMortgageLicenseID": self.maybe(str(self.rng.randint(100000, 999999))),
            "Note": self.maybe(self.rng.choice(NOTE_BODIES)),
            "Pager": "",
            "Phone": self.phone(),
            "PhoneExtension": self.maybe(str(self.rng.randint(100, 999))),
            "Suffix": "",
            "Title": self.maybe(self.rng.choice(["Escrow Officer", "Loan Officer", "Attorney"])),
        }

    def party(self, role: str, company: bool = False) -> dict[str, Any]:
        name = self.company_name() if company else " and ".join(
            f"{f} {l}" for f, l in (self.person_name() for _ in range(self.rng.randint(1, 2)))
        )
        people = [self.person() for _ in range(self.d.people_per_party)]
        return {
            "Name": name,
            "Address": self.address(),
            "AddressIsPropertyAddress": role == "BUYER",
            "AllPeopleWithCommas": ", ".join(p["FullName"] for p in people),
            "BuyerSellerOtherRoleTypeDescription": role.title(),
            "BuyerSellerRole": role.title(),
            "BuyerSellerType": "Company" if company else "Individual",
            "Code": self.maybe(f"{role[:3]}{self.rng.randint(100, 999)}"),
            "Confidential": False,
            "ContactType": "Company" if company else "Person",
            "DisbursementType": self.rng.choice(["Check", "Wire", "ACH"]),
            "DivertProceedsTo": "",
            "Email": f"contact@{name.split()[0].lower()}.example.invalid",
            "Fax": self.maybe(self.phone()),
            "ForwardingAddress": self.address() if self.rng.random() > 0.6 else {},
            "ForwardingAddressType": "",
            "Guid": self.guid(0.08),
            "IncludeOnRevenueReports": True,
            "IncludeSignature": self.rng.random() > 0.5,
            "Individual1": people[0]["FullName"] if people else "",
            "Individual2": people[1]["FullName"] if len(people) > 1 else "",
            "InterestLanguage": self.maybe("an undivided one-half interest"),
            "InterestPercent": self.maybe("50.00"),
            "IsCommitmentRecipient": self.rng.random() > 0.5,
            "IsGroupContact": False,
            "IsMarketingSource": False,
            "LookupCode": self.maybe(f"{name[:4].upper()}{self.rng.randint(10, 99)}"),
            "MarketingRep": "",
            "MarketingRep2": "",
            "MarketingRep3": "",
            "NameLong": name,
            "NameShort": name.split()[0],
            "NameWithRelationship": f"{name}, {role.lower()}",
            "NameWithVesting": self.maybe(f"{name}, as joint tenants"),
            "Notes": [self.note() for _ in range(self.rng.randint(0, 2))],
            "PaymentInfo": {
                "AccountNumber": self.maybe(f"****{self.rng.randint(1000, 9999)}"),
                "BankName": self.maybe(self.company_name()),
                "Guid": self.guid(),
                "RoutingNumber": self.maybe(f"{self.rng.randint(100000000, 999999999)}"),
            },
            "PendingTransactionType": "",
            "People": people,
            "Phone": self.phone(),
            "QualifiedIntermediary": {},
        }

    def note(self) -> dict[str, Any]:
        body = self.rng.choice(NOTE_BODIES)
        return {
            "Category": self.rng.choice(["General", "Title", "Escrow", "Lender", "Closing"]),
            "CreatedBy": self.person_name()[0],
            "CreatedDate": self.some_date(),
            "Guid": self.guid(),
            "IsPinned": self.rng.random() > 0.85,
            "ModifiedBy": self.maybe(self.person_name()[0]),
            "ModifiedDate": self.some_date(),
            "Subject": body[:48],
            "Text": body,
            "TextRtf": self.rtf(body),
            "Type": self.rng.choice(["Note", "Call", "Email"]),
        }

    def cdf_line(self, section_number: str, index: int) -> dict[str, Any]:
        desc = self.rng.choice(CHARGE_DESCRIPTIONS)
        amount = self.money()
        return {
            "Amount": amount,
            "BuyerPaidAtClosing": amount if self.rng.random() > 0.4 else "",
            "BuyerPaidBeforeClosing": self.maybe(self.money(0, 500)),
            "Charges": [
                {
                    "Amount": self.money(),
                    "Description": self.rng.choice(CHARGE_DESCRIPTIONS),
                    "Guid": self.guid(),
                    "IsOptional": self.rng.random() > 0.8,
                    "PayeeName": self.company_name(),
                    "Reference": self.maybe(f"REF{self.rng.randint(10000, 99999)}"),
                }
                for _ in range(self.d.charges_per_line)
            ],
            "Contact": {},
            "ContactName": self.company_name(),
            "Description": desc,
            "DescriptionExtended": self.maybe(f"{desc} - to {self.company_name()}"),
            "Guid": self.guid(0.03),
            "IsAggregateAdjustment": "",
            "IsRestricted": False,
            "Number": index,
            "PaidByOthers": self.maybe(self.money(0, 2000)),
            "PaidByOthersAtClosing": "",
            "Reference": self.maybe(f"REF{self.rng.randint(10000, 99999)}"),
            "SectionNumber": f"{section_number}{index:02d}",
            "SellerPaidAtClosing": self.maybe(self.money()),
            "SellerPaidBeforeClosing": "",
        }

    def cdf(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "Guid": self.guid(),
            "Number": f"CDF-{self.rng.randint(1000, 9999)}",
            "Type": self.rng.choice(["Borrower", "Seller", "Combined"]),
            "IssuedDate": self.some_date(),
            "RevisionNumber": str(self.rng.randint(0, 6)),
            "DeliveryTracking": {
                "DeliveredDate": self.some_date(),
                "DeliveryMethod": self.rng.choice(["Email", "Mail", "Portal"]),
                "Guid": self.guid(),
                "ReceivedDate": self.some_date(),
                "Recipients": [
                    {"Email": "recipient@example.invalid", "Guid": self.guid(), "Name": f"{f} {l}"}
                    for f, l in (self.person_name() for _ in range(2))
                ],
            },
            "ClosingCostComparisons": [
                {
                    "Description": f"Comparison {i}",
                    "Guid": self.guid(),
                    "LoanEstimate": self.money(),
                    "Final": self.money(),
                    "DidThisChange": self.rng.choice(["YES", "NO"]),
                }
                for i in range(9)
            ],
        }
        for key, label in CDF_SECTIONS:
            lines = [
                self.cdf_line(label[0], i + 1)
                for i in range(self.d.cdf_lines_per_section)
            ]
            doc[key] = {
                "Guid": self.guid(),
                "Lines": lines,
                "Total": f"{sum(float(l['Amount']) for l in lines):.2f}",
                "Type": label,
            }
        for total_key in ("TotalLoanCostSection", "TotalOtherCostSection"):
            doc[total_key] = {
                "Guid": self.guid(),
                "Lines": [self.cdf_line("T", 1)],
                "Total": self.money(1000, 50000),
                "Type": total_key,
            }
        return doc

    def cdf_amounts(self) -> dict[str, Any]:
        disb = [
            {
                "Amount": self.money(500, 400000),
                "CheckNumber": self.maybe(str(self.rng.randint(10000, 99999))),
                "Charges": [
                    {
                        "Amount": self.money(),
                        "Description": self.rng.choice(CHARGE_DESCRIPTIONS),
                        "Guid": self.guid(),
                    }
                    for _ in range(self.rng.randint(1, 3))
                ],
                "DisbursementDate": self.some_date(),
                "Guid": self.guid(),
                "IsVoided": False,
                "PayeeName": self.company_name(),
                "Payees": [
                    {"Guid": self.guid(0.1), "Name": self.company_name(), "Share": self.money()}
                    for _ in range(self.rng.randint(1, 2))
                ],
                "Reference": self.maybe(f"DIS{self.rng.randint(10000, 99999)}"),
                "Status": self.rng.choice(["Pending", "Issued", "Cleared"]),
                "Type": self.rng.choice(["Check", "Wire", "ACH"]),
            }
            for _ in range(self.d.disbursements)
        ]
        rec = [
            {
                "Amount": self.money(1000, 500000),
                "Guid": self.guid(),
                "PayorName": self.company_name(),
                "ReceiptDate": self.some_date(),
                "Reference": self.maybe(f"RCP{self.rng.randint(10000, 99999)}"),
                "Type": self.rng.choice(["Wire", "Check", "ACH"]),
            }
            for _ in range(self.d.receipts)
        ]
        return {
            "Disbursements": disb,
            "Guid": self.guid(),
            "Receipts": rec,
            "TotalDisbursements": f"{sum(float(d['Amount']) for d in disb):.2f}",
            "TotalReceipts": f"{sum(float(r['Amount']) for r in rec):.2f}",
        }

    def exception(self, i: int) -> dict[str, Any]:
        text = self.rng.choice(TITLE_EXCEPTION_TEXTS).format(year=self.rng.randint(2024, 2026))
        return {
            "Code": f"EX{i:03d}",
            "Description": text,
            "DescriptionRtf": self.rtf(text),
            "Guid": self.guid(),
            "IsStandard": self.rng.random() > 0.5,
            "Number": i,
            "PhraseNumber": str(i),
            "Resolved": self.rng.random() > 0.7,
            "Type": self.rng.choice(["Standard", "Special", "Survey"]),
        }

    def requirement(self, i: int) -> dict[str, Any]:
        text = self.rng.choice(TITLE_REQUIREMENT_TEXTS)
        return {
            "Code": f"RQ{i:03d}",
            "Description": text,
            "DescriptionRtf": self.rtf(text),
            "Guid": self.guid(),
            "IsMet": self.rng.random() > 0.5,
            "Number": i,
            "Type": self.rng.choice(["Standard", "Special"]),
        }

    def endorsement(self, i: int) -> dict[str, Any]:
        name = self.rng.choice(ENDORSEMENT_NAMES)
        return {
            "Amount": self.money(25, 900),
            "Code": f"END{i:03d}",
            "Description": name,
            "Guid": self.guid(),
            "IsManual": self.rng.random() > 0.7,
            "Name": name,
            "Rate": f"{self.rng.uniform(0.1, 2.5):.3f}",
        }

    def policy(self, kind: str) -> dict[str, Any]:
        return {
            "Amount": self.money(100000, 2000000),
            "Endorsements": [self.endorsement(i) for i in range(self.d.endorsements_per_product)],
            "EffectiveDate": self.some_date(),
            "Exceptions": [self.exception(i) for i in range(max(1, self.d.exceptions_per_commitment // 2))],
            "Guid": self.guid(),
            "InsuredName": self.company_name() if kind == "Loan" else " and ".join(
                f"{f} {l}" for f, l in (self.person_name() for _ in range(2))
            ),
            "IssuedDate": self.some_date(),
            "Number": f"{kind[:1]}P-{self.rng.randint(100000, 999999)}",
            "Premium": self.money(300, 9000),
            "Type": kind,
            "UnderwriterName": self.company_name(),
        }

    def commitment(self) -> dict[str, Any]:
        return {
            "CompletedBy": {},
            "CompletedDate": self.some_date(),
            "Countersignature": {
                "Guid": self.guid(),
                "Name": f"{self.person_name()[0]} {self.person_name()[1]}",
                "SignedDate": self.some_date(),
                "Title": "Authorized Signatory",
                "IsRequired": True,
                "IsSigned": self.rng.random() > 0.4,
                "Location": self.rng.choice(CITIES),
            },
            "CurrentOwner": " and ".join(f"{f} {l}" for f, l in (self.person_name() for _ in range(2))),
            "EffectiveDate": self.some_date(),
            "Endorsements": [self.endorsement(i) for i in range(self.d.endorsements_per_product)],
            "Exceptions": [self.exception(i) for i in range(self.d.exceptions_per_commitment)],
            "ExceptionsStartNumber": 1,
            "Guid": self.guid(),
            "IssuedDate": self.some_date(),
            "LockStatus": "Unlocked",
            "Notes": [self.note() for _ in range(self.rng.randint(0, 2))],
            "Number": f"CM-{self.rng.randint(100000, 999999)}",
            "OwnershipInterest": "Fee Simple",
            "PhraseNumberingScheme": "Sequential",
            "Policies": [self.policy(k) for k in ("Owners", "Loan")],
            "PolicyInstructions": {
                "Guid": self.guid(),
                "DeliveryMethod": "Email",
                "Instructions": self.rng.choice(NOTE_BODIES),
                "IsRush": self.rng.random() > 0.8,
                "Recipient": self.company_name(),
                "RequestedDate": self.some_date(),
                "Type": "Standard",
            },
            "PreparedFor": [],
            "Properties": [{"Guid": self.guid(0.5), "Address": self.address()}],
            "Requirements": [self.requirement(i) for i in range(self.d.requirements_per_commitment)],
            "RequirementsMetWithin": "30",
            "RequirementsStartNumber": 1,
            "Revision": "",
            "RevisionNumber": str(self.rng.randint(0, 4)),
            "ScheduleD": {
                "Guid": self.guid(),
                "AgentName": self.company_name(),
                "AgentAddress": self.address(),
                "UnderwriterName": self.company_name(),
                "IssuingOffice": self.rng.choice(CITIES),
                "LicenseNumber": f"LIC{self.rng.randint(100000, 999999)}",
                "Phone": self.phone(),
                "Email": "agent@example.invalid",
                "PolicyNumbers": [f"P-{self.rng.randint(100000, 999999)}" for _ in range(2)],
                "SearchPeriodFrom": self.some_date(),
                "SearchPeriodTo": self.some_date(),
            },
        }

    def title(self) -> dict[str, Any]:
        return {
            "AdditionalCharges": [
                {
                    "Amount": self.money(25, 1500),
                    "Code": f"AC{i:03d}",
                    "Description": self.rng.choice(CHARGE_DESCRIPTIONS),
                    "Guid": self.guid(),
                    "IsManual": self.rng.random() > 0.6,
                    "PayeeName": self.company_name(),
                    "Rate": f"{self.rng.uniform(0.1, 3.0):.3f}",
                    "Type": self.rng.choice(["Endorsement", "Service", "Search"]),
                }
                for i in range(self.d.additional_charges)
            ],
            "Commitments": [self.commitment() for _ in range(self.d.commitments)],
            "Endorsements": [self.endorsement(i) for i in range(self.d.endorsements_per_product)],
            "Guid": self.guid(),
            "LoanPolicies": [self.policy("Loan") for _ in range(self.d.loan_policies)],
            "OwnersPolicies": [self.policy("Owners") for _ in range(self.d.owners_policies)],
            "PreliminaryTitleOpinions": [
                {
                    "Guid": self.guid(),
                    "CompletedDate": self.some_date(),
                    "ExaminerName": f"{self.person_name()[0]} {self.person_name()[1]}",
                    "Findings": self.rng.choice(NOTE_BODIES),
                    "FindingsRtf": self.rtf(self.rng.choice(NOTE_BODIES)),
                    "SearchFromDate": self.some_date(),
                    "SearchToDate": self.some_date(),
                    "Status": self.rng.choice(["Complete", "In Review"]),
                }
            ],
            "TitleInsuranceCalculations": [
                {
                    "Guid": self.guid(),
                    "CalculationType": self.rng.choice(["Simultaneous", "Reissue", "Standard"]),
                    "OwnersAmount": self.money(100000, 2000000),
                    "LoanAmount": self.money(100000, 2000000),
                    "Premium": self.money(300, 9000),
                    "Discount": self.maybe(self.money(0, 500)),
                }
            ],
            "TitleProducts": [
                {
                    "Guid": self.guid(),
                    "Code": f"TP{i:03d}",
                    "Description": self.rng.choice(["Owner's Policy", "Loan Policy", "Commitment", "Search"]),
                    "Endorsements": [self.endorsement(j) for j in range(self.d.endorsements_per_product)],
                    "Exceptions": [self.exception(j) for j in range(max(1, self.d.exceptions_per_commitment // 2))],
                    "EffectiveDate": self.some_date(),
                    "IssuedDate": self.some_date(),
                    "Premium": self.money(300, 9000),
                    "Rate": f"{self.rng.uniform(0.5, 4.0):.3f}",
                    "Status": self.rng.choice(["Issued", "Pending"]),
                    "UnderwriterName": self.company_name(),
                }
                for i in range(self.d.title_products)
            ],
        }

    def property_(self) -> dict[str, Any]:
        addr = self.address()
        return {
            "Acreage": f"{self.rng.uniform(0.05, 12.0):.3f}",
            "Address": addr,
            "Affidavit": {},
            "Appraisal": {
                "Amount": self.money(100000, 3000000),
                "AppraiserName": self.company_name(),
                "Date": self.some_date(),
                "Guid": self.guid(),
                "OrderedDate": self.some_date(),
            },
            "Block": self.maybe(str(self.rng.randint(1, 99))),
            "Building": self.maybe(str(self.rng.randint(1, 20))),
            "BuyersInterest": "Fee Simple",
            "CensusTract": f"{self.rng.randint(1000, 9999)}.{self.rng.randint(10, 99)}",
            "CityGLC": self.maybe(addr["City"][:3].upper()),
            "CommitmentLegal": self.rtf(
                f"Lot {self.rng.randint(1, 99)}, Block {self.rng.randint(1, 40)}, of "
                f"{self.rng.choice(STREET_NAMES)} Subdivision, according to the plat thereof "
                f"as recorded in Plat Book {self.rng.randint(1, 200)}, Page {self.rng.randint(1, 300)}."
            ),
            "Condo": {},
            "CoopInterest": "",
            "CoopName": "",
            "County": addr["County"],
            "CountyGLC": self.maybe(addr["County"][:3].upper()),
            "CountyTitle": addr["County"],
            "CSS1099S": self.rng.random() > 0.5,
            "CSSLine": self.maybe(str(self.rng.randint(1, 50))),
            "Declaration": {},
            "Deeds": [
                {
                    "Guid": self.guid(),
                    "Book": str(self.rng.randint(100, 9999)),
                    "Page": str(self.rng.randint(1, 999)),
                    "InstrumentNumber": f"{self.rng.randint(2020, 2026)}{self.rng.randint(100000, 999999)}",
                    "RecordedDate": self.some_date(),
                    "Type": self.rng.choice(["Warranty Deed", "Quit Claim", "Special Warranty"]),
                }
                for _ in range(self.rng.randint(1, 2))
            ],
            "Description": self.rng.choice(["Single Family", "Condominium", "Townhouse", "Vacant Land", "Commercial"]),
            "District": self.maybe(str(self.rng.randint(1, 20))),
            "EscrowBriefLegal": f"Lot {self.rng.randint(1, 99)}, {self.rng.choice(STREET_NAMES)} Subdivision",
            "EscrowBriefLegalLookupCode": self.maybe(f"LGL{self.rng.randint(100, 999)}"),
            "EscrowLegal": self.rtf(f"Lot {self.rng.randint(1, 99)} of {self.rng.choice(STREET_NAMES)} Subdivision."),
            "FinalTitleOpinion": {},
            "Guid": self.guid(0.1),
            "HOA": {
                "Guid": self.guid(),
                "Name": self.maybe(f"{self.rng.choice(STREET_NAMES)} Homeowners Association"),
                "Phone": self.maybe(self.phone()),
                "TransferFee": self.maybe(self.money(50, 900)),
            },
            "HOACharges": [],
            "HOAManagement": {},
            "IsPrimaryResidence": self.rng.random() > 0.35,
            "LandZonedAs": self.rng.choice(["R-1", "R-2", "C-1", "PUD", "AG"]),
            "Lots": self.maybe(str(self.rng.randint(1, 99))),
            "ManufacturedHousing": {},
            "MapReference": self.maybe(f"MAP{self.rng.randint(100, 999)}"),
            "MapReferenceRecording": {},
            "OccupiedBy": self.rng.choice(["Owner", "Tenant", "Vacant"]),
            "OtherType": "",
            "OtherUse": "",
            "Parcels": [
                {
                    "Guid": self.guid(),
                    "ParcelNumber": f"{self.rng.randint(10, 99)}-{self.rng.randint(1000, 9999)}-{self.rng.randint(100, 999)}",
                    "Description": self.maybe("Primary parcel"),
                    "TaxYear": str(self.rng.randint(2023, 2026)),
                    "AssessedValue": self.money(50000, 2000000),
                    "TaxAmount": self.money(500, 40000),
                }
                for _ in range(self.d.parcels_per_property)
            ],
            "PropertyType": self.rng.choice(["Residential", "Commercial", "Land"]),
            "Section": self.maybe(str(self.rng.randint(1, 36))),
            "Subdivision": f"{self.rng.choice(STREET_NAMES)} Subdivision",
            "SurveyOrderedDate": self.some_date(),
            "TaxIdentification": f"TAX{self.rng.randint(100000, 999999)}",
            "Township": self.maybe(str(self.rng.randint(1, 40))),
            "Unit": self.maybe(str(self.rng.randint(1, 400))),
        }

    def loan(self) -> dict[str, Any]:
        amount = round(self.rng.uniform(75_000, 2_400_000), 2)
        return {
            "ApprovalDate": self.some_date(),
            "ARMDetail": {},
            "Amount": f"{amount:.2f}",
            "Borrowers": [{"Guid": self.guid(0.4), "Name": f"{f} {l}"} for f, l in (self.person_name() for _ in range(2))],
            "BuydownDetail": {},
            "CDF": {"Guid": self.guid(0.3)},
            "CommitmentExpirationDate": self.some_date(),
            "CSS": {},
            "Funding": {
                "Guid": self.guid(),
                "FundedDate": self.some_date(),
                "FundingAmount": f"{amount:.2f}",
                "WireReference": self.maybe(f"WR{self.rng.randint(100000, 999999)}"),
            },
            "Guid": self.guid(0.05),
            "HUD": {},
            "InterimInterest": {
                "Amount": self.money(0, 2500),
                "Days": self.rng.randint(0, 30),
                "Guid": self.guid(),
                "PerDiem": self.money(1, 200),
            },
            "Lender": self.party("LENDER", company=True),
            "LenderPerson": self.person(),
            "LoanPolicy": {"Guid": self.guid(0.2), "Amount": f"{amount:.2f}"},
            "LoanServicer": {},
            "MortgageBroker": {},
            "MortgageBrokerPerson": {},
            "MortgageInsuranceCaseNumber": self.maybe(f"MI{self.rng.randint(100000, 999999)}"),
            "Notes": [self.note() for _ in range(self.rng.randint(0, 2))],
            "Number": f"LN{self.rng.randint(1000000, 9999999)}",
            "Payments": [
                {
                    "Guid": self.guid(),
                    "Amount": self.money(500, 9000),
                    "DueDate": self.some_date(),
                    "PrincipalAndInterest": self.money(400, 8000),
                    "Escrow": self.money(50, 1500),
                    "Type": self.rng.choice(["Monthly", "Biweekly"]),
                }
                for _ in range(self.rng.randint(1, 3))
            ],
            "PriorFHACaseNumber": "",
            "Product": self.rng.choice(["30 Year Fixed", "15 Year Fixed", "5/1 ARM", "7/1 ARM"]),
            "ProductDescription": self.maybe("Conforming fixed-rate first mortgage"),
            "SecurityInstrument": {
                "Guid": self.guid(),
                "RecordedDate": self.some_date(),
                "Book": str(self.rng.randint(100, 9999)),
                "Page": str(self.rng.randint(1, 999)),
                "InstrumentNumber": f"{self.rng.randint(2020, 2026)}{self.rng.randint(100000, 999999)}",
                "Type": "Mortgage",
            },
            "Terms": {
                "AdditionalTerms": [],
                "AmountFinanced": f"{amount:.2f}",
                "AnnualPercentageRate": f"{self.rng.uniform(3.0, 8.5):.3f}",
                "BalloonDueDate": "",
                "BalloonPaymentTermIsApplicable": False,
                "CanInterestRateRise": self.rng.random() > 0.7,
                "CanLoanBalanceRise": False,
                "CanMonthlyAmountOwedRise": self.rng.random() > 0.8,
                "FinanceCharge": self.money(10000, 900000),
                "Guid": self.guid(),
                "InterestRate": f"{self.rng.uniform(3.0, 8.0):.3f}",
                "LoanTermMonths": self.rng.choice([180, 240, 360]),
                "MaturityDate": self.some_date(400, 700),
                "MonthlyPrincipalAndInterest": self.money(400, 9000),
                "PrepaymentPenalty": False,
                "TotalOfPayments": self.money(100000, 3000000),
            },
            "TitleCompany": {"Guid": self.guid(0.4), "Name": self.company_name()},
            "Type": self.rng.choice(LOAN_TYPES),
            "TypeDescription": self.maybe("First lien purchase money mortgage"),
        }

    def task(self, i: int, requested: bool) -> dict[str, Any]:
        name = self.rng.choice(TASK_NAMES)
        return {
            "AssignedTo": f"{self.person_name()[0]} {self.person_name()[1]}",
            "CompletedDate": self.some_date() if self.rng.random() > 0.4 else "",
            "CreatedDate": self.some_date(),
            "Description": name,
            "DescriptionRtf": self.rtf(name),
            "DueDate": self.some_date(),
            "Guid": self.guid(),
            "IsComplete": self.rng.random() > 0.4,
            "IsRequired": self.rng.random() > 0.3,
            "Notes": self.maybe(self.rng.choice(NOTE_BODIES)),
            "Number": i,
            "Priority": self.rng.choice(["Low", "Normal", "High"]),
            "RequestedBy": self.company_name() if requested else "",
            "Status": self.rng.choice(["Open", "In Progress", "Complete", "Waived"]),
            "Type": self.rng.choice(["Checklist", "Request", "Milestone"]),
        }

    # -- full order ---------------------------------------------------------

    def build_object_data(self) -> dict[str, Any]:
        d = self.d
        buyers = [self.party("BUYER") for _ in range(d.buyers)]
        sellers = [self.party("SELLER") for _ in range(d.sellers)]
        others = [
            self.party(r, company=r in ("ATTORNEY", "BUILDER", "APPRAISER", "TITLE_COMPANY"))
            for r in [
                ["ATTORNEY", "BUILDER", "APPRAISER", "REAL_ESTATE_AGENT", "SURVEYOR", "INSPECTOR"][i % 6]
                for i in range(d.others)
            ]
        ]
        loans = [self.loan() for _ in range(d.loans)]
        notes = [self.note() for _ in range(d.notes)]

        status = self.rng.choices(ORDER_STATUSES, weights=ORDER_STATUS_WEIGHTS, k=1)[0]

        return {
            # --- scalar header fields (relational candidates) ---------------
            "Balance": self.money(0, 250000),
            "CompletedDate": self.some_date(),
            "ConsummationDate": self.some_date(),
            "CreatedDate": self.some_date(),
            "CreatedWithVersion": "24.3.1",
            "DisbursementDate": self.some_date(),
            "DueDate": self.some_date(),
            "EmailSubjectLine": self.maybe("Closing documents for your review"),
            "Guid": self.guid(),
            "HasRescissionPeriod": self.rng.random() > 0.7,
            "Information": self.maybe("Standard residential purchase transaction"),
            "IsBuyerChargedForProrationDate": self.rng.random() > 0.5,
            "IsCashSale": self.rng.random() > 0.85,
            "IsCDFForNonSellerTransactions": False,
            "IsCommercial": self.rng.random() > 0.85,
            "IsConstruction": self.rng.random() > 0.9,
            "IsOutOfCounty": self.rng.random() > 0.8,
            "IsRestrictedTemplate": False,
            "IsRush": self.rng.random() > 0.85,
            "IsSaturdayInRescission": False,
            "IsSettlementDateEstimated": self.rng.random() > 0.6,
            "IsSubEscrow": False,
            "IsTemplate": False,
            "LockStatus": "Unlocked",
            "Number": f"{self.rng.randint(100000, 999999)}",
            "Project": self.rng.choice(PROJECTS),
            "ProrationDate": self.some_date(),
            "ReceivedDate": self.some_date(),
            "RelatedOrders": "",
            "RescissionDate": self.some_date(),
            "ReservedDate": self.some_date(),
            "SettlementDate": self.some_date(),
            "SettlementType": self.rng.choice(SETTLEMENT_TYPES),
            "Source": self.rng.choice(["Web", "Portal", "Phone", "Integration"]),
            "Status": status,
            "StatusComment": self.maybe("Awaiting lender clear to close"),
            "TransactionType": self.rng.choice(TRANSACTION_TYPES),
            # --- large sections (JSON block / Cosmos aggregate candidates) --
            "Buyers": buyers,
            "CDFAmounts": self.cdf_amounts(),
            "CDFs": [self.cdf() for _ in range(d.cdf_count)],
            "ChecklistTasks": [self.task(i, False) for i in range(d.checklist_tasks)],
            "ExistingLiens": [
                {
                    "Amount": self.money(10000, 800000),
                    "Guid": self.guid(),
                    "HolderName": self.company_name(),
                    "LoanNumber": f"LN{self.rng.randint(1000000, 9999999)}",
                    "PayoffAmount": self.money(10000, 800000),
                    "PayoffGoodThrough": self.some_date(),
                    "RecordedDate": self.some_date(),
                    "Type": self.rng.choice(["Mortgage", "HELOC", "Judgment", "Tax Lien"]),
                }
                for _ in range(d.existing_liens)
            ],
            "GeneralNotes": [self.note() for _ in range(d.general_notes)],
            "Invoices": [
                {
                    "Guid": self.guid(),
                    "Number": f"INV{self.rng.randint(10000, 99999)}",
                    "Date": self.some_date(),
                    "DueDate": self.some_date(),
                    "Lines": [
                        {
                            "Amount": self.money(),
                            "Description": self.rng.choice(CHARGE_DESCRIPTIONS),
                            "Guid": self.guid(),
                            "Quantity": self.rng.randint(1, 3),
                        }
                        for _ in range(d.invoice_lines)
                    ],
                    "Status": self.rng.choice(["Open", "Paid", "Void"]),
                    "Total": self.money(100, 20000),
                }
                for _ in range(d.invoices)
            ],
            "Lenders": [self.party("LENDER", company=True) for _ in range(d.lenders)],
            "Loans": loans,
            "Notes": notes,
            "Others": others,
            "Properties": [self.property_() for _ in range(d.properties)],
            "RequestedTasks": [self.task(i, True) for i in range(d.requested_tasks)],
            "Sellers": sellers,
            "Title": self.title(),
            "TitleCompanies": [self.party("TITLE_COMPANY", company=True) for _ in range(d.title_companies)],
            # --- long tail of small sections --------------------------------
            **{
                name: []
                for name in (
                    "Assessments", "Attachments", "Commissions", "Contacts", "Deposits",
                    "Documents", "Escrows", "Holdbacks", "Judgments", "Milestones",
                    "Payoffs", "Prorations", "Recordings", "Referrals", "Rentals",
                    "Signings", "Surveys", "Taxes", "Transfers", "Vestings",
                    "Warranties", "Wires", "Workflows",
                )
            },
            **{
                name: {}
                for name in (
                    "AuditSummary", "ClosingInstructions", "Commission", "Escrow",
                    "MarketingSource", "Policy", "Preferences", "Recording",
                    "Settlement", "Underwriter", "Workflow",
                )
            },
        }


def build_order(
    seed: int,
    order_index: int,
    profile: SizeProfile,
    customer_id: str,
    version: int = 1,
) -> dict[str, Any]:
    """Deterministically build one full extract envelope."""
    # Per-order seed derived from (global seed, index) so any order can be
    # regenerated independently and identically.
    h = hashlib.sha256(f"{seed}:{order_index}:{version}".encode()).digest()
    rng = random.Random(int.from_bytes(h[:8], "big"))

    dials = GrowthDials().scaled(profile.scale)
    factory = OrderFactory(rng, dials)
    object_data = factory.build_object_data()

    order_guid = str(uuid.UUID(bytes=hashlib.sha256(f"order:{seed}:{order_index}".encode()).digest()[:16], version=4))
    extract_ts = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(
        seconds=order_index * 37 + version * 911
    )

    return {
        "ExtractDetails": {
            "CustomerSerialNumber": customer_id,
            "ExtractDateTimeUTC": extract_ts.strftime("%Y-%m-%dT%H:%M:%S"),
            "ExtractServer": f"POC-EXTRACT-{(order_index % 4) + 1:02d}",
            "ExtractType": "FULL",
        },
        "ExtractData": {
            "ExtractObjects": [
                {
                    "ObjectDetails": {
                        "ObjectType": "ORDER",
                        "OrderID": order_guid,
                        "OrderVersion": version,
                    },
                    "ObjectData": object_data,
                }
            ]
        },
    }


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------


def calibrate(profile: SizeProfile, seed: int = 1, tolerance: float = 0.06, max_iter: int = 24) -> float:
    """Binary-search the growth scale that lands within ``tolerance`` of the
    profile's target compact byte size. Deterministic for a given seed."""
    lo, hi = 0.05, 40.0
    best_scale, best_err = 1.0, float("inf")
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        trial = SizeProfile(profile.name, profile.target_compact_bytes, profile.weight, mid)
        size = compact_bytes(build_order(seed, 0, trial, "POC001"))
        err = abs(size - profile.target_compact_bytes) / profile.target_compact_bytes
        if err < best_err:
            best_scale, best_err = mid, err
        if err <= tolerance:
            return round(mid, 4)
        if size < profile.target_compact_bytes:
            lo = mid
        else:
            hi = mid
    return round(best_scale, 4)


def calibrated_profiles(seed: int = 1, cache: Path | None = None) -> list[SizeProfile]:
    """Return the size profiles with calibrated scale factors, using a cached
    calibration file when available so runs stay fast and reproducible."""
    cache = cache or Path("artifacts/size-calibration.json")
    if cache.exists():
        data = json.loads(cache.read_text())
        if data.get("seed") == seed:
            out = []
            for p in SIZE_PROFILES:
                out.append(
                    SizeProfile(p.name, p.target_compact_bytes, p.weight, data["scales"][p.name])
                )
            return out

    out = []
    scales = {}
    for p in SIZE_PROFILES:
        s = calibrate(p, seed=seed)
        scales[p.name] = s
        out.append(SizeProfile(p.name, p.target_compact_bytes, p.weight, s))
        print(f"  calibrated {p.name}: scale={s}", file=sys.stderr)

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"seed": seed, "scales": scales}, indent=2), encoding="utf-8")
    return out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    s = sorted(values)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return int(s[f] + (s[c] - s[f]) * (k - f))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--orders", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--customers", type=int, default=8)
    ap.add_argument("--out", default="data/generated/orders")
    ap.add_argument("--profile", help="force a single size profile (e.g. p1m)")
    ap.add_argument("--calibrate", action="store_true", help="recalibrate size profiles and exit")
    ap.add_argument("--stats-out", default="artifacts/generated-dataset-stats.json")
    ap.add_argument("--no-write", action="store_true", help="compute stats without writing files")
    args = ap.parse_args()

    if args.calibrate:
        Path("artifacts/size-calibration.json").unlink(missing_ok=True)
        profiles = calibrated_profiles(seed=args.seed)
        for p in profiles:
            actual = compact_bytes(build_order(args.seed, 0, p, "POC001"))
            print(f"{p.name:8} target={p.target_compact_bytes:>9,}  actual={actual:>9,}  "
                  f"scale={p.scale:>7}  err={abs(actual - p.target_compact_bytes) / p.target_compact_bytes * 100:5.1f}%")
        return

    profiles = calibrated_profiles(seed=args.seed)
    if args.profile:
        profiles = [p for p in profiles if p.name == args.profile]
        if not profiles:
            raise SystemExit(f"unknown profile {args.profile}; known: {list(PROFILES_BY_NAME)}")

    weights = [p.weight for p in profiles]
    pick_rng = random.Random(args.seed ^ 0x5EED)

    outdir = Path(args.out)
    if not args.no_write:
        outdir.mkdir(parents=True, exist_ok=True)

    sizes: list[int] = []
    by_profile: dict[str, list[int]] = {p.name: [] for p in profiles}
    manifest: list[dict[str, Any]] = []

    for i in range(args.orders):
        prof = pick_rng.choices(profiles, weights=weights, k=1)[0]
        customer_id = f"POC{(i % args.customers) + 1:03d}"
        doc = build_order(args.seed, i, prof, customer_id)
        blob = json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        sizes.append(len(blob))
        by_profile[prof.name].append(len(blob))

        details = doc["ExtractData"]["ExtractObjects"][0]["ObjectDetails"]
        manifest.append(
            {
                "index": i,
                "orderId": details["OrderID"],
                "orderVersion": details["OrderVersion"],
                "customerId": customer_id,
                "profile": prof.name,
                "compactBytes": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest(),
            }
        )

        if not args.no_write:
            (outdir / f"order-{i:06d}.json").write_bytes(blob)

        if (i + 1) % 250 == 0:
            print(f"  {i + 1}/{args.orders} orders", file=sys.stderr)

    stats = {
        "seed": args.seed,
        "orders": args.orders,
        "customers": args.customers,
        "generatedUtc": datetime.now(timezone.utc).isoformat(),
        "compactBytes": {
            "min": min(sizes),
            "max": max(sizes),
            "mean": round(statistics.fmean(sizes), 1),
            "median": int(statistics.median(sizes)),
            "p90": percentile(sizes, 0.90),
            "p95": percentile(sizes, 0.95),
            "p99": percentile(sizes, 0.99),
            "totalBytes": sum(sizes),
            "totalGiB": round(sum(sizes) / 1024**3, 3),
        },
        "byProfile": {
            name: {
                "count": len(v),
                "min": min(v) if v else 0,
                "max": max(v) if v else 0,
                "mean": round(statistics.fmean(v), 1) if v else 0,
            }
            for name, v in by_profile.items()
        },
        "profileScales": {p.name: p.scale for p in profiles},
    }

    sp = Path(args.stats_out)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(stats, indent=2), encoding="utf-8")

    if not args.no_write:
        (outdir / "_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    c = stats["compactBytes"]
    print(
        f"{args.orders} orders  min={c['min']:,}  median={c['median']:,}  mean={c['mean']:,.0f}  "
        f"p90={c['p90']:,}  p95={c['p95']:,}  p99={c['p99']:,}  max={c['max']:,}  "
        f"total={c['totalGiB']} GiB"
    )
    print(f"stats -> {sp}")


if __name__ == "__main__":
    main()
