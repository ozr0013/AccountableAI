"""Sample paperwork to drop into the running demo, one new fact each, made with the same
renderers as the messy world (world/mess). The dashboard lists them in a dropdown; adding one
parses the raw file with agents/extract and runs the workflow on what it read.

    python dashboard/feed/samples.py          # (re)write dashboard/feed/samples/

Every file is dated on the demo date (Apr 22, 2026), so nothing is dated after "today".
"""

from __future__ import annotations

import csv
import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
DIR = HERE / "samples"

# What each file is, which of the three flows it starts, and what should happen.
SAMPLES = [
    {"file": "Keyway_Owner_Statement_Apr2026.pdf", "label": "Keyway statement (PDF)",
     "flow": "statement", "expect": "One line says $640.00 for Rio Grande's $1,240.00 invoice: a mismatch alert."},
    {"file": "RioGrande_Payment_Received.eml", "label": "Rio Grande: “we received your payment”",
     "flow": "evidence", "expect": "The vendor confirms it was paid: that $1,240.00 alert is withdrawn."},
    {"file": "Keyway_Reply_KW-0901.eml", "label": "Keyway reply: “Brightside was paid”",
     "flow": "reply", "expect": "A reply with no receipt is not proof: the $1,000.00 alert stays open."},
    {"file": "Keyway_Bank_Export_Apr22.csv", "label": "Keyway bank export (CSV)",
     "flow": "evidence", "expect": "The bank shows the $1,000.00 Brightside payment: that alert is withdrawn."},
]

WHEN = datetime(2026, 4, 22, 15, 10, tzinfo=UTC)


def _statement() -> bytes:
    from world.mess.pdfs import statement_pdf
    rows = [
        {"line": 1, "date": "04/08/26", "vendor": "AmperPro Electric", "ref": "KW-0501", "amount": 640.00, "status": "PAID"},
        {"line": 2, "date": "04/10/26", "vendor": "Rio Grande Plumbing", "ref": "KW-0301", "amount": 640.00, "status": "PAID"},
        {"line": 3, "date": "", "vendor": "Sparkline Electric", "ref": "KW-0402", "amount": 900.00, "status": "PENDING",
         "note": "inv rec'd 03/28/26"},
    ]
    return statement_pdf({"title": "Keyway Property Management - Owner Statement", "period": "April 2026",
                          "prepared": "04/22/2026", "rows": rows})


def _vendor_email() -> bytes:
    from world.mess.noise import VENDOR_DOMAINS
    from world.mess.render import _eml
    domain = VENDOR_DOMAINS.get("v-riogrande", "riograndeplumbing.com")
    body = ("Hi Keyway team,\n\nWe received your payment of $1,240.00 for KW-0301 today. "
            "Thank you, you're all caught up.\n\nRio Grande Plumbing\nAccounts Receivable")
    return _eml(f"Rio Grande Plumbing <billing@{domain}>", "ap@keywaypm.com",
                "Payment received - KW-0301", body, WHEN, cc="controller@oaklinehomes.com")


def _keyway_reply() -> bytes:
    from world.mess.render import _eml
    body = ("Hi,\n\nBrightside Cleaning was paid last week for KW-0901. "
            "Let us know if you need anything else.\n\nThanks,\nKeyway AP")
    return _eml("Keyway AP <ap@keywaypm.com>", "controller@oaklinehomes.com",
                "Re: Payment reminder KW-0901", body, WHEN)


def _bank_export() -> bytes:
    buf = io.StringIO()
    buf.write("Account Name: KEYWAY PROPERTY MGMT OPERATING,Account: ****4417\n")
    buf.write("Statement Period: 04/20/2026 - 04/22/2026,Generated: 04/22/2026\n\n")
    wr = csv.writer(buf)
    wr.writerow(["Posting Date", "Description", "Check #", "Debit", "Credit", "Balance"])
    wr.writerow(["04/20/2026", "ADP PAYROLL FEES", "", "212.40", "", "61211.18"])
    wr.writerow(["04/22/2026", "ACH PMT BRIGHTSIDE CLEANIN KW0901", "", "1000.00", "", "60211.18"])
    wr.writerow(["04/22/2026", "TRANSFER FROM RESERVE", "", "", "5000.00", "65211.18"])
    return buf.getvalue().encode()


def write() -> list[dict]:
    DIR.mkdir(exist_ok=True)
    makers = [_statement, _vendor_email, _keyway_reply, _bank_export]
    for s, make in zip(SAMPLES, makers):
        (DIR / s["file"]).write_bytes(make())
    (DIR / "manifest.json").write_text(json.dumps(SAMPLES, indent=2) + "\n")
    return SAMPLES


if __name__ == "__main__":
    sys.path[:0] = [str(ROOT)]
    for s in write():
        print(f"  {s['file']:40s} {s['flow']:9s} {s['expect']}")
