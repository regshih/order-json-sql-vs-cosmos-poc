"""Synthetic value pools and size profiles for the order generator.

Every value here is invented. Nothing is copied from the customer sample - only
the *shape* of the sample (field names, nesting, array cardinality behaviour) is
reproduced. See docs/DATA_PROFILE.md for the measured structure this mirrors.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Synthetic vocabularies
# --------------------------------------------------------------------------

FIRST_NAMES = [
    "Avery", "Bryn", "Casimir", "Delphine", "Emrys", "Fiora", "Gideon", "Halcyon",
    "Ilse", "Jules", "Kestrel", "Linnea", "Marek", "Novaly", "Oriel", "Pilar",
    "Quillon", "Rowan", "Solveig", "Tamsin", "Ulric", "Verity", "Wren", "Xanthe",
    "Yarrow", "Zephyr", "Alder", "Briony", "Corwin", "Dara",
]

LAST_NAMES = [
    "Ashcombe", "Brightwater", "Calloway", "Danforth", "Eastgate", "Fairholm",
    "Grindlay", "Hallowfield", "Ingersoll", "Jessup", "Kirkbride", "Lindquist",
    "Marchetti", "Norrington", "Oakhurst", "Pennyworth", "Quarrington", "Rothwell",
    "Stanbridge", "Thorncroft", "Underhill", "Vancleave", "Wetherby", "Yolen",
    "Ziegler", "Abernathy", "Blackwood", "Chatterton", "Draycott", "Everly",
]

COMPANY_STEMS = [
    "Meridian", "Copperline", "Northgate", "Silverbrook", "Ashford", "Crestpoint",
    "Harborview", "Lakemont", "Pinecrest", "Redstone", "Summit Ridge", "Tallgrass",
    "Waypoint", "Blue Heron", "Cedar Hollow", "Ironwood", "Kingsley", "Larkspur",
]

COMPANY_SUFFIXES = [
    "Title Agency", "Escrow Services", "Lending Group", "National Bank",
    "Mortgage Partners", "Appraisal Associates", "Law Offices", "Builders LLC",
    "Realty Partners", "Financial Services",
]

STREET_NAMES = [
    "Alderbrook", "Bellhaven", "Chandler", "Dovetail", "Elmridge", "Foxglove",
    "Granite", "Harlow", "Inverness", "Junegrass", "Kensington", "Larchmont",
    "Marlowe", "Northfield", "Overlook", "Primrose", "Quarry", "Rosewood",
    "Stonegate", "Thistledown", "Umber", "Vantage", "Westmoreland", "Yardley",
]

STREET_TYPES = ["Street", "Avenue", "Lane", "Court", "Drive", "Terrace", "Way", "Circle"]

CITIES = [
    "Fairmont", "Brookhaven", "Northvale", "Cedar Falls", "Lakeshore", "Westbury",
    "Millbrook", "Stonehaven", "Ridgemont", "Harborton", "Glenview", "Oakmont",
    "Silverton", "Pinehurst", "Ashgrove", "Elmwood",
]

# (code, description) - real US state codes are not customer data.
STATES = [
    ("AZ", "Arizona"), ("CA", "California"), ("CO", "Colorado"), ("FL", "Florida"),
    ("GA", "Georgia"), ("IL", "Illinois"), ("IN", "Indiana"), ("MA", "Massachusetts"),
    ("MI", "Michigan"), ("NC", "North Carolina"), ("NJ", "New Jersey"), ("NY", "New York"),
    ("OH", "Ohio"), ("PA", "Pennsylvania"), ("SC", "South Carolina"), ("TN", "Tennessee"),
    ("TX", "Texas"), ("UT", "Utah"), ("VA", "Virginia"), ("WA", "Washington"),
]

COUNTIES = [
    "Ashland", "Bridgewater", "Chesterton", "Dunmore", "Eastbrook", "Fairview",
    "Glenmoor", "Havenwood", "Ironbridge", "Jamesport", "Kirkwood", "Langley",
]

ORDER_STATUSES = ["Open", "In Process", "Title Review", "Clear to Close", "Closing", "Closed", "Cancelled"]
ORDER_STATUS_WEIGHTS = [12, 24, 16, 10, 8, 26, 4]

TRANSACTION_TYPES = ["Purchase", "Refinance", "Construction", "Commercial", "Cash Sale"]
SETTLEMENT_TYPES = ["Escrow", "Closing", "Settlement"]
PROJECTS = ["Residential Standard", "Commercial Standard", "Refi Express", "Builder Program", "REO Portfolio"]

LOAN_TYPES = ["Conventional", "FHA", "VA", "USDA", "Jumbo", "Construction", "HELOC"]

CDF_SECTIONS = [
    ("OriginationChargeSection", "A. Origination Charges"),
    ("ServiceNotShoppedForSection", "B. Services Borrower Did Not Shop For"),
    ("ServiceShoppedForSection", "C. Services Borrower Did Shop For"),
    ("TaxesAndGovernmentFeesSection", "E. Taxes and Other Government Fees"),
    ("PrepaidSection", "F. Prepaids"),
    ("EscrowSection", "G. Initial Escrow Payment at Closing"),
    ("OtherCostSection", "H. Other"),
    ("DueFromBuyerSection", "K. Due from Borrower at Closing"),
    ("DueToBuyerSection", "L. Paid Already by or on Behalf of Borrower"),
    ("DueFromSellerSection", "M. Due to Seller at Closing"),
    ("DueToSellerSection", "N. Due from Seller at Closing"),
    ("BuyerCashToCloseSection", "Calculating Cash to Close"),
    ("SellerCashToCloseSection", "Seller's Transaction"),
    ("TotalClosingCostSection", "J. Total Closing Costs"),
]

CHARGE_DESCRIPTIONS = [
    "Loan Origination Fee", "Appraisal Fee", "Credit Report Fee", "Flood Determination Fee",
    "Tax Service Fee", "Title - Settlement Agent Fee", "Title - Lender's Title Insurance",
    "Title - Owner's Title Insurance", "Title - Examination Fee", "Title - Closing Protection Letter",
    "Survey Fee", "Pest Inspection Fee", "Recording Fee - Deed", "Recording Fee - Mortgage",
    "Transfer Tax - State", "Transfer Tax - County", "Homeowner's Insurance Premium",
    "Property Taxes", "Prepaid Interest", "HOA Transfer Fee", "Wire Transfer Fee",
    "Courier Fee", "Notary Fee", "Escrow Deposit - Taxes", "Escrow Deposit - Insurance",
    "Attorney Fee", "Municipal Lien Search", "Endorsement - ALTA 8.1", "Endorsement - ALTA 9",
    "Commission - Listing Broker", "Commission - Selling Broker", "Home Warranty",
]

TITLE_EXCEPTION_TEXTS = [
    "Taxes and assessments for the year {year} and subsequent years, a lien not yet due and payable.",
    "Rights or claims of parties in possession not shown by the public records.",
    "Easements, or claims of easements, not shown by the public records.",
    "Any encroachment, encumbrance, violation, variation, or adverse circumstance affecting the title that would be disclosed by an accurate and complete land survey of the Land.",
    "Any lien, or right to a lien, for services, labor, or material heretofore or hereafter furnished, imposed by law and not shown by the public records.",
    "Restrictions, conditions, covenants, and easements as set forth in the plat of record, but omitting any covenant or restriction based on race, color, religion, sex, disability, familial status, or national origin.",
    "Mineral rights and all rights incident thereto previously conveyed or reserved of record.",
    "Terms, conditions, and provisions of the Declaration of Condominium recorded in the public records.",
    "Rights of tenants in possession under unrecorded leases as tenants only.",
    "Subject to the lien of the mortgage described in Schedule A hereof.",
]

TITLE_REQUIREMENT_TEXTS = [
    "Pay the agreed amounts for the interest in the Land and/or the Mortgage to be insured.",
    "Pay us the premiums, fees, and charges for the Policy.",
    "Documents satisfactory to us creating the interest in the Land and/or the Mortgage to be insured must be signed, delivered, and recorded.",
    "You must tell us in writing the name of anyone not referred to in this Commitment who will get an interest in the Land or who will make a loan on the Land.",
    "Satisfactory evidence must be furnished that all improvements and repairs have been completed and all contractors have been paid in full.",
    "Release of the mortgage described in Schedule B, Part I, in favor of the lender of record.",
    "Proof of payment of all outstanding municipal utility charges affecting the Land.",
    "A properly executed owner's affidavit must be furnished at or prior to closing.",
]

ENDORSEMENT_NAMES = [
    "ALTA 4-06 Condominium", "ALTA 5-06 Planned Unit Development", "ALTA 6-06 Variable Rate",
    "ALTA 8.1-06 Environmental Protection Lien", "ALTA 9-06 Restrictions, Encroachments, Minerals",
    "ALTA 22-06 Location", "ALTA 25-06 Same as Survey", "ALTA 17-06 Access and Entry",
]

NOTE_BODIES = [
    "Confirmed settlement statement figures with the lender and updated the disbursement schedule accordingly.",
    "Received the payoff statement and verified the good-through date against the scheduled disbursement date.",
    "Left a voicemail for the listing agent requesting the HOA estoppel letter; follow up tomorrow.",
    "Survey received and reviewed; no encroachments noted that would require an additional exception.",
    "Buyer requested a change to the vesting language; updated the deed and re-sent for review.",
    "Municipal lien search returned clear. No open permits or code violations reported.",
    "Wire instructions verified by callback to a known number on file prior to release of funds.",
    "Title examination complete. Commitment issued and delivered to all required recipients.",
]

TASK_NAMES = [
    "Order title search", "Review title commitment", "Order survey", "Request payoff statement",
    "Order HOA estoppel", "Verify wire instructions", "Prepare settlement statement",
    "Send closing disclosure", "Schedule signing appointment", "Collect earnest money",
    "Record deed", "Record mortgage", "Issue final policy", "Disburse funds",
    "Obtain lender closing instructions", "Clear title exceptions", "Municipal lien search",
    "Confirm insurance binder", "Prepare 1099-S", "Send recorded documents",
]

# --------------------------------------------------------------------------
# Size profiles
# --------------------------------------------------------------------------


@dataclass
class SizeProfile:
    """A named target payload size.

    ``scale`` multiplies every growable array cardinality. The generator
    auto-calibrates ``scale`` per profile to land within tolerance of
    ``target_compact_bytes`` so that profile names mean what they say.
    """

    name: str
    target_compact_bytes: int
    weight: float  # relative frequency in a mixed corpus
    scale: float = 1.0

    @property
    def target_kib(self) -> float:
        return self.target_compact_bytes / 1024


# Targets come straight from the POC brief (§12). Weights describe a plausible
# corpus mix: most orders are ~1 MB, a small tail is very large.
SIZE_PROFILES: list[SizeProfile] = [
    SizeProfile("p500k", 500 * 1024, weight=0.22),
    SizeProfile("p1m", 1024 * 1024, weight=0.34),
    SizeProfile("p1_5m", int(1.5 * 1024 * 1024), weight=0.22),
    SizeProfile("p1_9m", int(1.9 * 1024 * 1024), weight=0.12),
    SizeProfile("p3m", 3 * 1024 * 1024, weight=0.07),
    SizeProfile("p5m", 5 * 1024 * 1024, weight=0.03),
]

PROFILES_BY_NAME = {p.name: p for p in SIZE_PROFILES}


@dataclass
class GrowthDials:
    """Base (scale=1.0) array cardinalities, calibrated so that scale≈1 lands
    near the measured sample size (~552 KB compact).

    Growth is expressed through *business* structures: more CDF lines, more
    disbursements, more title exceptions, more notes, more tasks, more parties.
    No padding strings are ever emitted.
    """

    cdf_count: int = 1
    cdf_lines_per_section: int = 6
    charges_per_line: int = 1
    disbursements: int = 8
    receipts: int = 2
    title_products: int = 3
    commitments: int = 1
    exceptions_per_commitment: int = 21
    requirements_per_commitment: int = 9
    endorsements_per_product: int = 4
    loan_policies: int = 1
    owners_policies: int = 1
    additional_charges: int = 6
    properties: int = 1
    parcels_per_property: int = 2
    loans: int = 1
    buyers: int = 1
    sellers: int = 1
    people_per_party: int = 2
    others: int = 6
    lenders: int = 1
    title_companies: int = 3
    invoices: int = 3
    invoice_lines: int = 3
    notes: int = 13
    general_notes: int = 13
    checklist_tasks: int = 48
    requested_tasks: int = 22
    existing_liens: int = 1

    # Fields that must not scale (a single order has one header).
    NON_SCALING: tuple[str, ...] = field(
        default=("cdf_count", "properties", "loans", "commitments"), repr=False
    )

    def scaled(self, scale: float) -> "GrowthDials":
        """Return a copy with every growable dial multiplied by ``scale``."""
        out = GrowthDials()
        for key, base in vars(self).items():
            if key == "NON_SCALING" or not isinstance(base, int):
                continue
            if key in self.NON_SCALING:
                setattr(out, key, base)
            else:
                setattr(out, key, max(1, int(round(base * scale))))
        # Very large orders realistically gain a second CDF and more properties
        # and loans rather than infinitely long single arrays.
        if scale >= 3.0:
            out.cdf_count = 2
            out.properties = 2
            out.loans = 2
            out.commitments = 2
        elif scale >= 1.8:
            out.properties = 2
            out.loans = 2
        return out
