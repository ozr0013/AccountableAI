"""A larger batch of test documents (fictional companies only), meant to be emailed to a test inbox.

    python agreement_agent/samples/make_batch.py        # writes samples/batch/*.pdf and batch/manifest.json

Each document says what the agent should find: `expect` lists the required fields that are
deliberately missing or unclear ([] means the document is complete and nothing is escalated).
"""

from __future__ import annotations

import json
from pathlib import Path

from make_samples import pdf

HERE = Path(__file__).resolve().parent
OUT = HERE / "batch"
OWNER = "Oakline Homes LLC"

DOCS: list[dict] = [
    {
        "file": "summit_roofing_services_agreement.pdf", "sender": "Summit Roofing Co.",
        "subject": "Signed roofing agreement", "expect": [],
        "lines": [
            "ROOFING SERVICES AGREEMENT",
            f"This Roofing Services Agreement is made as of April 1, 2026 by and between {OWNER} (\"Owner\") and Summit Roofing Co. (\"Vendor\").",
            "1. Services. Vendor shall inspect and repair roofs at Owner's properties on request.",
            "2. Fees. Owner shall pay Vendor a fixed fee of $4,800 per completed roof repair.",
            "3. Payment Terms. Payment is due Net 30 from the invoice date.",
            "4. Payment Information. Payments shall be made by ACH to Canyon State Bank, routing number 122105155, account number 88120443, in the name of Summit Roofing Co.",
            "5. Term. Twelve months from the effective date.",
            "By: Dana Ortiz, Controller, Oakline Homes LLC   Date: April 1, 2026",
            "By: Mara Lindqvist, President, Summit Roofing Co.   Date: April 1, 2026",
        ],
    },
    {
        "file": "brightpath_cleaning_agreement.pdf", "sender": "BrightPath Cleaning LLC",
        "subject": "Cleaning contract for your review", "expect": ["payment_info"],
        "lines": [
            "CLEANING SERVICES AGREEMENT",
            f"This Agreement is made as of April 10, 2026 between {OWNER} (\"Client\") and BrightPath Cleaning LLC (\"Contractor\").",
            "1. Scope. Contractor will clean common areas at 412 Alder St. twice a week.",
            "2. Compensation. Client will pay Contractor $1,600 per month.",
            "3. Payment Terms. Each invoice is due within fifteen (15) days of receipt.",
            "4. Termination. Either party may terminate with 30 days' written notice.",
            "By: Dana Ortiz, Controller, Oakline Homes LLC",
            "By: Keisha Morgan, Owner, BrightPath Cleaning LLC",
        ],
    },
    {
        "file": "ironwood_hvac_maintenance_agreement.pdf", "sender": "Ironwood HVAC Inc.",
        "subject": "HVAC maintenance agreement", "expect": ["payment_due"],
        "lines": [
            "HVAC MAINTENANCE AGREEMENT",
            f"This Maintenance Agreement is entered into as of March 15, 2026 by and between {OWNER} (\"Owner\") and Ironwood HVAC Inc. (\"Vendor\").",
            "1. Services. Vendor shall service heating and cooling units quarterly.",
            "2. Fees. Owner shall pay Vendor $350 per unit per visit.",
            "3. Payment Information. Checks shall be made payable to Ironwood HVAC Inc. and mailed to 88 Foundry Rd., Mesa, AZ 85201.",
            "4. Term. One year, renewing automatically.",
            "By: Dana Ortiz, Controller, Oakline Homes LLC",
            "By: Tom Becker, Service Manager, Ironwood HVAC Inc.",
        ],
    },
    {
        "file": "clearview_windows_agreement.pdf", "sender": "Clearview Window Services LLC",
        "subject": "Window cleaning agreement", "expect": ["payment_amount", "payment_info"],
        "lines": [
            "WINDOW SERVICES AGREEMENT",
            f"This Agreement is made between {OWNER} (\"Owner\") and Clearview Window Services LLC (\"Contractor\").",
            "1. Services. Contractor will clean exterior windows at Owner's buildings each quarter.",
            "2. Fees. Fees will be agreed for each visit.",
            "3. Payment Terms. Owner shall pay within thirty (30) days of each invoice.",
            "4. Payment. Payment details to be provided separately.",
            "By: ____________________ (Owner)",
        ],
    },
    {
        "file": "redrock_pest_control_agreement.pdf", "sender": "Red Rock Pest Control LLC",
        "subject": "Pest control service terms", "expect": [],
        "lines": [
            "PEST CONTROL SERVICE AGREEMENT",
            f"This Service Agreement is made as of May 1, 2026 by and between {OWNER} (\"Owner\") and Red Rock Pest Control LLC (\"Vendor\").",
            "1. Services. Vendor shall provide monthly pest treatment at all listed properties.",
            "2. Fees. Owner shall pay Vendor $210 per property per month.",
            "3. Payment Terms. Invoices are due Net 15.",
            "4. Payment Information. Payment by wire to Desert Valley Credit Union, routing number 322281617, account number 5530918, account name Red Rock Pest Control LLC.",
            "By: Dana Ortiz, Controller, Oakline Homes LLC   Date: May 1, 2026",
            "By: Ana Ruiz, Owner, Red Rock Pest Control LLC   Date: May 1, 2026",
        ],
    },
    {
        "file": "unsigned_snow_removal_terms.pdf", "sender": "Northline Snow & Ice",
        "subject": "Snow removal terms (draft)", "expect": ["vendor_name", "payment_due", "payment_info"],
        "lines": [
            "SNOW REMOVAL TERMS",
            f"These terms apply to snow removal for {OWNER} (\"Owner\").",
            "1. Services. Plowing and salting of parking areas after any snowfall over two inches.",
            "2. Fees. $175 per visit.",
            "3. Term. November through March.",
        ],
    },
    {
        "file": "harbor_elevator_service_contract.pdf", "sender": "Harbor Elevator Corp.",
        "subject": "Elevator service contract", "expect": [],
        "lines": [
            "ELEVATOR SERVICE CONTRACT",
            f"This Contract is made as of February 1, 2026 by and between {OWNER} (\"Owner\") and Harbor Elevator Corp. (\"Contractor\").",
            "1. Services. Contractor shall maintain and inspect passenger elevators monthly.",
            "2. Fees. Owner shall pay Contractor $2,250 per month.",
            "3. Payment Terms. Payment is due on the first day of each month.",
            "4. Payment Information. ACH to Pacific Harbor Bank, routing number 121000358, account number 70011234, in the name of Harbor Elevator Corp.",
            "By: Dana Ortiz, Controller, Oakline Homes LLC   Date: February 1, 2026",
            "By: Owen Pike, Vice President, Harbor Elevator Corp.   Date: February 1, 2026",
        ],
    },
    {
        "file": "mesa_painting_agreement.pdf", "sender": "Mesa Painting Co.",
        "subject": "Painting agreement attached", "expect": ["payment_due", "payment_info"],
        "lines": [
            "PAINTING AGREEMENT",
            f"This Agreement is made as of April 20, 2026 by and between {OWNER} (\"Owner\") and Mesa Painting Co. (\"Contractor\").",
            "1. Scope. Interior repainting of vacant units between tenants.",
            "2. Compensation. Owner will pay Contractor $1,150 per unit.",
            "3. Payment. To be arranged.",
            "By: Dana Ortiz, Controller, Oakline Homes LLC",
            "By: Rafael Soto, Owner, Mesa Painting Co.",
        ],
    },
    {
        "file": "summit_roofing_invoice_SR-1187.pdf", "sender": "Summit Roofing Co.",
        "subject": "Invoice SR-1187", "expect": [],
        "lines": [
            "SUMMIT ROOFING CO.",
            "INVOICE",
            "Invoice No. SR-1187",
            f"Bill To: {OWNER}",
            "Date: April 22, 2026",
            "Roof repair, 77 Birch Ct.",
            "Amount Due: $4,800.00",
            "Terms: Net 30",
            "Remit to: ACH to Canyon State Bank, routing number 122105155, account number 88120443.",
        ],
    },
    {
        "file": "brightpath_invoice_BP-2044.pdf", "sender": "BrightPath Cleaning LLC",
        "subject": "Invoice BP-2044", "expect": ["payment_info"],
        "lines": [
            "BRIGHTPATH CLEANING LLC",
            "INVOICE",
            "Invoice No. BP-2044",
            f"Bill To: {OWNER}",
            "Date: April 30, 2026",
            "Common-area cleaning, April",
            "Amount Due: $1,600.00",
            "Due Date: May 15, 2026",
        ],
    },
    {
        "file": "ironwood_invoice_IW-0310.pdf", "sender": "Ironwood HVAC Inc.",
        "subject": "Invoice IW-0310", "expect": ["payment_due"],
        "lines": [
            "IRONWOOD HVAC INC.",
            "INVOICE",
            "Invoice No. IW-0310",
            f"Bill To: {OWNER}",
            "Date: April 18, 2026",
            "Quarterly service, 6 units at $350",
            "Amount Due: $2,100.00",
            "Make checks payable to Ironwood HVAC Inc., 88 Foundry Rd., Mesa, AZ 85201.",
        ],
    },
    {
        "file": "company_picnic_flyer.pdf", "sender": "Oakline Homes Social Committee",
        "subject": "Company picnic flyer", "expect": "skipped",
        "lines": [
            "OAKLINE SPRING PICNIC",
            "Join us Saturday, May 16 at Riverside Park from noon to 4pm.",
            "Bring a dish to share. Games, music and lawn chairs provided.",
            "RSVP to the front office by May 9.",
        ],
    },
]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for d in DOCS:
        pdf(d["lines"], OUT / d["file"])
    manifest = [{k: d[k] for k in ("file", "sender", "subject", "expect")} for d in DOCS]
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"wrote {len(DOCS)} documents to {OUT}")


if __name__ == "__main__":
    main()
