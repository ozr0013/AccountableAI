"""Write the sample documents the tests and demo use (fictional companies only).

    python agreement_agent/samples/make_samples.py
"""

from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

from fpdf import FPDF

HERE = Path(__file__).resolve().parent

COMPLETE = [
    "SERVICES AGREEMENT",
    "This Services Agreement is made as of March 1, 2026 by and between Oakline Homes LLC (\"Owner\") and Rio Grande Plumbing LLC (\"Vendor\").",
    "1. Services. Vendor shall provide plumbing repair and maintenance at Owner's rental properties as requested in writing.",
    "2. Fees. Owner shall pay Vendor at the rate of $95 per hour plus approved materials, as set out on each invoice.",
    "3. Payment Terms. Owner shall pay each undisputed invoice within thirty (30) days of receipt.",
    "4. Payment Information. Payments shall be made by ACH to First Mesa Bank, routing number 102000021, account number 4471029, in the name of Rio Grande Plumbing LLC.",
    "5. Term. This Agreement runs for twelve months from the effective date and renews unless either party gives 30 days' notice.",
    "6. Insurance. Vendor shall carry general liability insurance of at least $1,000,000.",
    "Signatures",
    "By: Dana Ortiz, Controller, Oakline Homes LLC   Date: March 1, 2026",
    "By: Luis Ortega, Owner, Rio Grande Plumbing LLC   Date: March 1, 2026",
]

MISSING = [
    "LANDSCAPING SERVICES AGREEMENT",
    "This Agreement is entered into by Oakline Homes LLC (\"Owner\") and GreenEdge Landscaping LLC (\"Contractor\").",
    "1. Scope. Contractor will provide weekly lawn care and seasonal cleanup at the properties listed in Exhibit A.",
    "2. Compensation. Owner will pay Contractor a monthly fee of $1,250 for the services.",
    "3. Invoicing. Contractor will send one invoice per month describing the work performed.",
    "4. Payment. Payment details to be provided separately.",
    "5. Termination. Either party may end this Agreement with 30 days' written notice.",
    "By: ____________________ (Owner)",
]

INVOICE = [
    "SPARKLINE ELECTRIC LLC",
    "INVOICE",
    "Invoice No. SE-0402",
    "Bill To: Oakline Homes LLC",
    "Date: April 15, 2026",
    "Panel inspection and breaker replacement, 412 Alder St.",
    "Amount Due: $900.00",
    "Thank you for your business.",
]


def pdf(lines: list[str], path: Path) -> None:
    doc = FPDF()
    doc.add_page()
    doc.set_font("Helvetica", size=11)
    for i, line in enumerate(lines):
        if i == 0:
            doc.set_font("Helvetica", "B", 14)
        doc.multi_cell(0, 7, line)
        doc.ln(2)
        doc.set_font("Helvetica", size=11)
    doc.output(str(path))


def main() -> None:
    pdf(COMPLETE, HERE / "rio_grande_services_agreement.pdf")
    pdf(MISSING, HERE / "greenedge_landscaping_agreement.pdf")
    pdf(INVOICE, HERE / "sparkline_invoice_SE-0402.pdf")
    # the invoice also arrives as an email attachment, like it would from Gmail
    msg = EmailMessage()
    msg["From"] = "billing@sparkline-electric.example"
    msg["To"] = "ap@oaklinehomes.example"
    msg["Subject"] = "Invoice SE-0402"
    msg["Message-ID"] = "<se-0402@sparkline-electric.example>"
    msg.set_content("Hi, invoice attached.")
    msg.add_attachment((HERE / "sparkline_invoice_SE-0402.pdf").read_bytes(), maintype="application",
                       subtype="pdf", filename="sparkline_invoice_SE-0402.pdf")
    (HERE / "inbox").mkdir(exist_ok=True)
    (HERE / "inbox" / "invoice_SE-0402.eml").write_bytes(bytes(msg))
    print("wrote samples to", HERE)


if __name__ == "__main__":
    main()
