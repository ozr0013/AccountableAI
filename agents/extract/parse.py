"""One raw file -> zero or more parsed records. No cross-file reasoning here (that's link.py)."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date

from contracts.models import SourceKind

from .fields import (
    VendorIndex,
    keyway_refs,
    labeled_money,
    money_values,
    parse_date,
    parse_datetime_header,
    vendor_invoice_numbers,
)
from .readers import Mail, pdf_text, read_eml

TOTAL_LABELS = ["amount due", "balance due", "total due", "please pay"]


@dataclass
class Parsed:
    """A document read off a raw file, before linking. data follows the clean Document schema."""

    source: str                         # raw file, relative path
    kind: SourceKind | None             # None = ignored
    part: str = ""                      # suffix when one file yields several docs
    vendor_id: str | None = None
    dated: date | None = None
    text: str = ""
    data: dict = field(default_factory=dict)
    method: str = "rules"               # how the text was read: text_layer, ocr_*, csv, email
    confidence: float = 1.0
    warnings: list[str] = field(default_factory=list)
    needs_human: bool = False
    meta: dict = field(default_factory=dict)   # unresolved bits the linker fills in

    @property
    def doc_id(self) -> str:
        return f"raw:{self.source}" + (f"#{self.part}" if self.part else "")

    def flag(self, why: str, human: bool = False, conf: float | None = None) -> None:
        self.warnings.append(why)
        self.needs_human = self.needs_human or human
        if conf is not None:
            self.confidence = min(self.confidence, conf)


# ---------------------------------------------------------------------------------------------
# PDFs
# ---------------------------------------------------------------------------------------------

_EMAIL_HEAD = re.compile(r"^\s*From:\s*.+\n(?:.*\n){0,2}?\s*To:\s*.+", re.MULTILINE)
_BANK_HEAD = re.compile(r"^\s*DATE\s+(?:TYPE\s+)?DESCRIPTION\s+AMOUNT\s+BALANCE\b",
                        re.MULTILINE | re.IGNORECASE)


def _pdf_kind(text: str) -> str:
    low = text.lower()
    if _EMAIL_HEAD.search(text[:800]):
        return "email"                       # an email printed to PDF: read it as an email
    if "property management agreement" in low[:400]:
        return "agreement"                   # before the W-9 check: agreements mention W-9s
    if _BANK_HEAD.search(text) or "account activity" in low[:400]:
        return "bank"
    if "credit memo" in low:
        return "credit_memo"
    if "estimate" in low and "not an invoice" in low:
        return "estimate"
    if "form w-9" in low or "taxpayer identification" in low:
        return "w9"
    if "ach authorization" in low:
        return "ach_form"
    if "wire confirmation" in low:
        return "wire"
    if "property management agreement" in low:
        return "agreement"
    if re.search(r"\bLn\b.*\bVendor\b.*\bStatus\b", text) or "owner statement" in low or \
            ("period:" in low and "prepared" in low):
        return "statement"
    if "invoice" in low or any(lab in low for lab in TOTAL_LABELS):
        return "invoice"
    return "unknown"


def parse_invoice(text: str, source: str, vendors: VendorIndex, method: str,
                  mail: Mail | None = None) -> Parsed:
    p = Parsed(source, SourceKind.vendor_invoice, method=method, text=text[:1500])
    if method.startswith("ocr"):
        p.flag(f"read by {method.replace('_', ' ')}", conf=0.8)
    vid, score = vendors.find_in(text)
    if not vid and mail:
        vid = vendors.by_email(mail_original_sender(mail) or mail.from_addr)
        if vid:
            p.flag("vendor taken from the sender's email domain", conf=0.85)
        else:
            vid, score = vendors.match(original_sender_name(mail) or mail.from_name, 0.75)
    p.vendor_id = vid
    if not vid:
        p.flag("vendor not recognized", human=True, conf=0.3)
    m = re.search(r"(?:Invoice\s*(?:#|No\.?)?|INVOICE|Inv)\s*:?\s*([A-Z]{1,4}-\d{3,5})", text)
    vendor_no = m[1] if m else (vendor_invoice_numbers(text) or [None])[0]
    job = re.search(r"\bJob\s*(?:ref|#|no\.?)\s*:?\s*([A-Z0-9-]{3,12})", text, re.IGNORECASE)
    refs = [r for r in keyway_refs(text) if not vendor_no or r[-4:] not in vendor_no]
    total = labeled_money(text, TOTAL_LABELS)
    current = labeled_money(text, ["current charges"])
    subtotal = labeled_money(text, ["subtotal"])
    tax = labeled_money(text, ["tax"]) or 0.0
    older = re.search(r"previous balance|past due|balance forward", text, re.IGNORECASE)
    older_amt = labeled_money(text, ["previous balance", "past due", "balance forward"])
    p.meta["amount_candidates"] = [round(x, 2) for x in
                                   (subtotal, current, (total - older_amt) if total and older_amt
                                    else None) if x is not None]
    if current is not None:
        if total is not None and abs(current - total) > 0.005:
            p.flag(f"invoice also carries an older balance; took current charges {current:,.2f} "
                   f"not the {total:,.2f} total", conf=0.9)
        total = current
    elif total is not None and subtotal is not None and abs(subtotal + tax - total) > 0.005:
        p.meta["ambiguous_amount"] = True
        p.flag(f"total {total:,.2f} doesn't equal subtotal {subtotal:,.2f} + tax"
               + ("; an older balance is included" if older else ""), conf=0.5)
    elif older:
        p.meta["ambiguous_amount"] = True
        p.flag("invoice mentions an older balance; amount may include it", conf=0.5)
    if total is None:
        vals = money_values(text)
        total = max(vals) if vals else None
        if total is not None:
            p.flag("no 'amount due' label; took the largest amount", conf=0.6)
    inv_date = None
    dm = re.search(r"(?:Date of service|Invoice Date|Dated|Date)\s*:?\s*(.+)", text)
    if dm:
        inv_date = parse_date(dm[1])
    received = None
    if mail:
        received = forwarded_date(mail) or (mail.sent.date() if mail.sent else None)
    if total is None:
        p.flag("amount unreadable", human=True, conf=0.2)
    if len(refs) > 1:
        p.flag(f"several Keyway references on one invoice: {', '.join(refs)}", human=True, conf=0.4)
    p.data = {"amount": total}
    if refs:
        p.data["invoice_id"] = refs[0]
    else:
        p.meta["needs_invoice_id"] = True
    if vendor_no:
        p.data["vendor_ref"] = vendor_no
    if job:
        p.data["job_ref"] = job[1]
    if received:
        p.data["received"] = received.isoformat()
    if re.search(r"inspection pending", text, re.IGNORECASE):
        p.data["inspection"] = "pending"
    terms = re.search(r"Terms:\s*([^.\n]+)", text)
    if terms and "10" not in terms[1]:
        p.flag(f"invoice says '{terms[1].strip()}'; the agreement's 10 days governs", conf=0.95)
    p.dated = received or inv_date
    p.meta.update(vendor_no=vendor_no, invoice_date=inv_date.isoformat() if inv_date else None)
    return p


def mail_original_sender(mail: Mail) -> str | None:
    m = re.search(r"^From:\s*.*?<([^>]+)>", mail.body, re.MULTILINE)
    return m[1].lower() if m else None


def original_sender_name(mail: Mail) -> str | None:
    m = re.search(r"^From:\s*(.*?)\s*<", mail.body, re.MULTILINE)
    return m[1] if m else None


def forwarded_date(mail: Mail) -> date | None:
    """When the vendor sent it to Keyway (the forwarded header), not when Keyway forwarded it."""
    if "forwarded message" not in mail.body.lower():
        return None
    m = re.search(r"^Date:\s*(.+)$", mail.body, re.MULTILINE)
    dt = parse_datetime_header(m[1]) if m else None
    return dt.date() if dt else None


_STATUS = [(re.compile(r"\b(hold|held)\b.*w-?9|w-?9.*\b(hold|held)\b", re.IGNORECASE), "hold_missing_w9"),
           (re.compile(r"\bsched", re.IGNORECASE), "scheduled"),
           (re.compile(r"\bpa?id\b|\bpd\b", re.IGNORECASE), "paid"),
           (re.compile(r"\bbilled\b|\bopen\b|rec'?d", re.IGNORECASE), "billed")]


def norm_status(s: str) -> str:
    for rx, val in _STATUS:
        if rx.search(s):
            return val
    return s.strip().lower() or "unknown"


_STMT_COLS = {"date": "date", "vendor": "vendor", "ref": "ref", "property": "property",
              "description": "description", "amount": "amount", "status": "status"}


def _statement_rows_by_header(pdf) -> list[dict] | None:
    """Columns taken from the table's header row (Date | Vendor | Ref | ... | Amount | Status), for
    statements without a line-number column. Wrapped cells continue on the next PDF line."""
    rows: list[dict] = []
    found = False
    for page in pdf.pages:
        lines: dict[int, list] = {}
        for wd in page.extract_words(keep_blank_chars=False):
            lines.setdefault(round(wd["top"] / 3), []).append(wd)
        starts: list[tuple[float, str]] = []
        last_key = None
        for key in sorted(lines):
            ws = sorted(lines[key], key=lambda w: w["x0"])
            names = [w["text"].lower() for w in ws]
            if not starts:
                if "vendor" in names and "amount" in names and "ln" not in names:
                    starts = [(w["x0"] - 2, _STMT_COLS[w["text"].lower()]) for w in ws
                              if w["text"].lower() in _STMT_COLS]
                    found = True
                continue
            cols: dict[str, list[str]] = {c: [] for _, c in starts}
            for wd in ws:
                col = next((c for x, c in reversed(starts) if wd["x0"] >= x), starts[0][1])
                cols[col].append(wd["text"])
            c = {k: " ".join(v) for k, v in cols.items()}
            dated = bool(re.match(r"^\d{1,2}/\d{1,2}", c.get("date", "")))
            if dated and money_values(c.get("amount", "") + " "):
                rows.append({**c, "ln": str(len(rows) + 1)})
                last_key = key
            elif rows and last_key is not None and key - last_key <= 4 and not c.get("date"):
                for k, v in c.items():                       # a wrapped cell
                    if v and k in rows[-1]:
                        rows[-1][k] = (rows[-1][k] + " " + v).strip()
                last_key = key
            elif rows:
                last_key = None                              # the table has ended
    return rows if found else None


def parse_statement(data: bytes, text: str, source: str, vendors: VendorIndex) -> Parsed:
    """Column positions from the PDF words: Ln | Date | Vendor | Ref | Amount | Status, or the
    table's own header row when there is no line-number column."""
    import pdfplumber
    p = Parsed(source, SourceKind.manager_statement, method="text_layer", text=text[:1500])
    pm = re.search(r"Prepared\s*:?\s+(.+)", text)
    p.dated = parse_date(pm[1]) if pm else None
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        by_header = _statement_rows_by_header(pdf)
    if by_header is not None:
        return _statement_lines(p, by_header, vendors)
    rows: list[dict] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            lines: dict[int, list] = {}
            for wd in page.extract_words(keep_blank_chars=False):
                lines.setdefault(round(wd["top"] / 3), []).append(wd)
            for key in sorted(lines):
                ws = sorted(lines[key], key=lambda w: w["x0"])
                cols = {"ln": [], "date": [], "vendor": [], "ref": [], "amount": [], "status": []}
                for wd in ws:
                    x = wd["x0"]
                    col = ("ln" if x < 75 else "date" if x < 130 else "vendor" if x < 295 else
                           "ref" if x < 385 else "amount" if x < 465 else "status")
                    cols[col].append(wd["text"])
                c = {k: " ".join(v) for k, v in cols.items()}
                if c["ln"].isdigit() and money_values(c["amount"] + " "):
                    rows.append(c)
                elif rows and not c["ln"] and re.search(r"rec'?d", c["vendor"] + c["ref"], re.IGNORECASE):
                    rows[-1]["note"] = c["vendor"] + " " + c["ref"]
    return _statement_lines(p, rows, vendors)


def _statement_lines(p: Parsed, rows: list[dict], vendors: VendorIndex) -> Parsed:
    out = []
    for r in rows:
        ref_txt = r["ref"]
        refs = keyway_refs(ref_txt)
        on = parse_date(r["date"]) if r["date"] else None
        line = {"line": f"L{r['ln']}", "amount": money_values(r["amount"] + " ")[0],
                "status": norm_status(r.get("status", "")), "on": on.isoformat() if on else None,
                "_status_text": r.get("status", "")}
        retained = re.search(r"\$\s?([\d,]+(?:\.\d{2})?)\s+(?:retained|retainage|held back)",
                             r.get("status", ""), re.IGNORECASE)
        if retained:
            line["retained"] = float(retained[1].replace(",", ""))
        if refs:
            line["invoice_id"] = refs[0]
        elif ref_txt.strip() not in ("", "-"):
            line["_vendor_ref"] = ref_txt.strip()
        vid, _ = vendors.match(r["vendor"], 0.7)
        line["_vendor_id"] = vid
        if r.get("note"):
            rec = parse_date(r["note"].split("rec'd")[-1] if "rec'd" in r["note"] else r["note"])
            if rec:
                line["received"] = rec.isoformat()
        out.append(line)
    p.data = {"lines": out}
    if not out:
        p.flag("statement has no readable lines", human=True, conf=0.3)
    return p


def parse_wire(text: str, source: str, vendors: VendorIndex) -> list[Parsed]:
    dm = re.search(r"Wire date:\s*(\S+)", text)
    wire_day = parse_date(dm[1]) if dm else None
    out = []
    for line in text.splitlines():
        m = re.match(r"^\s*((?:INV-)?KW-\d{3,4})\s+(.*?)\s+([\d,]+\.\d\d)\s*$", line)
        if not m:
            continue
        inv = keyway_refs(m[1])[0]
        p = Parsed(source, SourceKind.funding, part=inv, dated=wire_day, method="text_layer",
                   text=line.strip())
        p.vendor_id = vendors.match(m[2], 0.7)[0]
        p.data = {"invoice_id": inv, "amount": float(m[3].replace(",", "")),
                  "funded_on": wire_day.isoformat() if wire_day else None}
        out.append(p)
    total = labeled_money(text, ["amount:"])
    if out and total is not None and abs(total - sum(p.data["amount"] for p in out)) > 0.01:
        for p in out:
            p.flag("wire total doesn't equal its remittance lines", human=True, conf=0.5)
    return out


def parse_agreement(text: str, source: str) -> Parsed:
    m = re.search(r"Effective Date:\s*(.+)", text)
    clauses = [ln.strip() for ln in text.splitlines()]
    body = "\n".join(c for c in clauses if re.match(r"^\d+\.\d+\s", c))
    return Parsed(source, SourceKind.agreement, dated=parse_date(m[1]) if m else None,
                  text=body or text, method="text_layer")


def parse_pdf_file(data: bytes, source: str, vendors: VendorIndex,
                   mail: Mail | None = None) -> list[Parsed]:
    pt = pdf_text(data)
    if pt.method == "none":
        p = Parsed(source, SourceKind.vendor_invoice, method="none", confidence=0.0)
        p.flag("scanned image with no text layer and no OCR available: ask for a clean copy",
               human=True)
        if mail:
            p.vendor_id = vendors.by_email(mail_original_sender(mail) or "") or \
                vendors.match(original_sender_name(mail) or mail.from_name, 0.75)[0]
        return [p]
    kind = _pdf_kind(pt.text)
    if kind in ("estimate", "w9", "ach_form"):
        return [Parsed(source, None, method=pt.method, warnings=[f"ignored: {kind}"])]
    if kind == "credit_memo":
        p = Parsed(source, None, method=pt.method, text=pt.text[:800])
        amt = money_values(pt.text)
        p.flag(f"vendor credit memo ({'$%.2f' % amt[-1] if amt else 'amount unclear'}): check it is "
               "applied to Oakline's next funding", human=True)
        return [p]
    if kind == "email" and mail is None:
        return parse_mail(mail_from_text(pt.text), source, vendors)
    if kind == "bank":
        return [parse_bank_text(pt.text, source, vendors)]
    if kind == "wire":
        return parse_wire(pt.text, source, vendors)
    if kind == "agreement":
        return [parse_agreement(pt.text, source)]
    if kind == "statement":
        return [parse_statement(data, pt.text, source, vendors)]
    if kind == "invoice":
        p = parse_invoice(pt.text, source, vendors, pt.method, mail)
        p.meta["_pdf"] = data
        return [p]
    p = Parsed(source, None, method=pt.method)
    p.flag("PDF not recognized", human=True)
    return [p]


# ---------------------------------------------------------------------------------------------
# Bank CSV exports
# ---------------------------------------------------------------------------------------------

_NON_VENDOR = re.compile(r"SERVICE FEE|TRANSFER|PAYROLL|WIRE IN|HOME DEPOT|INTEREST|DEPOSIT", re.IGNORECASE)
_PREFIX = re.compile(r"^(ACH DEBIT|ACH PMT|BILL PAY|ONLINE PMT|CHECK\s+\d+|CHK\s+\d+)\s*", re.IGNORECASE)


def parse_bank_csv(text: str, source: str, vendors: VendorIndex) -> Parsed:
    p = Parsed(source, SourceKind.manager_bank, method="csv", text="")
    head, _, rest = text.partition("\n\n")
    gm = re.search(r"Generated:\s*(\S+)", head)
    p.dated = parse_date(gm[1]) if gm else None
    period = re.search(r"Statement Period:\s*([^,]+)", head)
    pays, skipped = [], 0
    for row in csv.DictReader(io.StringIO(rest.strip())):
        debit = (row.get("Debit") or "").strip()
        if not debit:
            continue
        desc = row.get("Description", "")
        if _NON_VENDOR.search(desc):
            skipped += 1
            continue
        refs = keyway_refs(desc)
        name = _PREFIX.sub("", desc)
        name = re.sub(r"\bPPD ID:\d+|\bLESS RETAINAGE\b", "", name)
        for rx in (r"(?<![A-Z])KW[\s\-#]*\d{3,4}", r"(?:Keyway\s*)?WO\s*#?\s*\d{3,4}",
                   r"KeywayWO\d+"):
            name = re.sub(rx, "", name, flags=re.IGNORECASE)
        vid, score = vendors.match(name.strip(), 0.7)
        on = parse_date(row.get("Posting Date", ""))
        pays.append({"vendor_id": vid, "amount": float(debit), "on": on.isoformat() if on else None,
                     "_refs": refs, "_desc": desc, "_retainage": "RETAINAGE" in desc.upper()})
        if not vid:
            p.flag(f"bank line payee not recognized: '{desc}'", human=True, conf=0.6)
    p.text = (f"Keyway operating account export, period {period[1].strip() if period else '?'}: "
              f"{len(pays)} vendor payments, {skipped} other debits ignored.")
    p.data = {"payments": pays}
    return p


_BANK_ROW = re.compile(r"^(\d{1,2}/\d{1,2})\s+(DR|CR)\s+(.+?)\s+(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})\s*$")
_FEE = re.compile(r"SVC\s*CHG|SERVICE CHARGE|\bFEE\b", re.IGNORECASE)
_CHECK = re.compile(r"^CHK\s+\d+\s*", re.IGNORECASE)


def parse_bank_text(text: str, source: str, vendors: VendorIndex) -> Parsed:
    """A bank activity export printed to PDF: DATE | TYPE | DESCRIPTION | AMOUNT | BALANCE."""
    p = Parsed(source, SourceKind.manager_bank, method="text_layer", text="")
    per = re.search(r"Period:\s*(\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})", text)
    start, end = (parse_date(per[1]), parse_date(per[2])) if per else (None, None)
    ex = re.search(r"Exported:\s*(\d{1,2}/\d{1,2}/\d{4})", text)
    p.dated = parse_date(ex[1]) if ex else end
    pays, skipped = [], 0
    for line in text.splitlines():
        m = _BANK_ROW.match(line.strip())
        if not m:
            continue
        amount = float(m[4].replace(",", ""))
        desc = m[3].strip()
        if m[2].upper() != "DR" or amount >= 0:
            continue
        if _NON_VENDOR.search(desc) or _FEE.search(desc):
            skipped += 1
            continue
        on = parse_date(f"{m[1]}/{(end or start).year}") if (end or start) else parse_date(m[1])
        name = _CHECK.sub("", _PREFIX.sub("", desc))
        name = re.sub(r"\b(pt\s*\d+|[A-Z]?\d{3,6})\b", "", name, flags=re.IGNORECASE).strip()
        vid, _ = vendors.match(name, 0.7)
        pays.append({"vendor_id": vid, "amount": -amount, "on": on.isoformat() if on else None,
                     "_refs": keyway_refs(desc), "_desc": desc,
                     "_nums": re.findall(r"\b[A-Z]?\d{3,6}\b", desc),
                     "_retainage": "RETAINAGE" in desc.upper()})
        if not vid:
            p.flag(f"bank line payee not recognized: '{desc}'", human=True, conf=0.6)
    bal = {k: float(v.replace(",", "")) for k, v in
           re.findall(r"(OPENING|CLOSING) BALANCE\s+(-?[\d,]+\.\d{2})", text, re.IGNORECASE)}
    p.text = (f"Bank activity export {start or '?'} to {end or '?'}: {len(pays)} vendor payments, "
              f"{skipped} other debits ignored.")
    p.data = {"payments": pays, "opening": bal.get("OPENING"), "closing": bal.get("CLOSING")}
    return p


# ---------------------------------------------------------------------------------------------
# Emails
# ---------------------------------------------------------------------------------------------

def mail_from_text(text: str) -> Mail:
    """An email printed to PDF: subject line, then From / To / Cc / Date headers, then the body."""
    from email.utils import parseaddr
    lines = text.splitlines()
    head: dict[str, str] = {}
    subject, body_at = "", len(lines)
    for i, line in enumerate(lines):
        m = re.match(r"^\s*(From|To|Cc|Date|Subject):\s*(.*)$", line)
        if m and m[1].lower() not in head:
            head[m[1].lower()] = m[2].strip()
            if m[1] == "From" and not subject:
                subject = " ".join(x.strip() for x in lines[:i] if x.strip())
            if m[1] == "Date":
                body_at = i + 1
                break
    name, addr = parseaddr(head.get("from", ""))
    return Mail(name, addr.lower(), head.get("to", ""), head.get("subject") or subject,
                parse_datetime_header(head.get("date", "")), "\n".join(lines[body_at:]))

_JUNK = re.compile(r"automatic reply|out of (the )?office|unsubscribe|newsletter|quarterly:", re.IGNORECASE)
_BANK_CHANGE = re.compile(r"banking information|bank(ing)? (details|info|provider)|new account|"
                          r"remittance (info|details)|payment instructions", re.IGNORECASE)
_W9_SENT = re.compile(r"\bW-?9\b[^.\n]{0,40}\b(attached|enclosed)\b|\b(attached|enclosed)\b[^.\n]{0,40}\bW-?9\b",
                      re.IGNORECASE)
_PAST_DUE = re.compile(r"past due|overdue|have not received payment|not received payment", re.IGNORECASE)
_CONFIRM = re.compile(r"received your payment|we received|hit our account|payment rec'?d|"
                      r"confirming|caught up", re.IGNORECASE)
_PAID_CLAIM = re.compile(r"\b(paid|sent|cleared|processed|went out)\b", re.IGNORECASE)


def parse_email(data: bytes, source: str, vendors: VendorIndex,
                keyway_domain: str = "keywaypm.com") -> list[Parsed]:
    return parse_mail(read_eml(data), source, vendors, keyway_domain)


def parse_mail(mail: Mail, source: str, vendors: VendorIndex,
               keyway_domain: str = "keywaypm.com") -> list[Parsed]:
    sent = mail.sent.date() if mail.sent else None
    if mail.attachments:
        out: list[Parsed] = []
        for i, (name, blob) in enumerate(mail.attachments):
            if not name.lower().endswith(".pdf"):
                continue
            src = source if len(mail.attachments) == 1 else f"{source}/{name}"
            for p in parse_pdf_file(blob, src, vendors, mail):
                if p.kind == SourceKind.manager_statement and not p.dated:
                    p.dated = sent
                out.append(p)
        if out and all(p.kind is None for p in out) and _BANK_CHANGE.search(mail.body):
            out = []          # the ACH form is junk, but the email around it is the real notice
        else:
            return out
    if _JUNK.search(mail.subject + "\n" + mail.body):
        return [Parsed(source, None, method="email", warnings=["ignored: auto-reply / newsletter"])]
    if "statement" in mail.subject.lower() and not mail.attachments:
        p = Parsed(source, None, method="email")
        p.flag("Keyway says a statement is attached, but nothing was attached")
        return [p]
    own = mail.own_text
    from_keyway = mail.from_addr.endswith("@" + keyway_domain)
    vid = None if from_keyway else vendors.by_email(mail.from_addr)
    lookalike = False
    if not from_keyway and not vid:
        vid, _ = vendors.match(mail.from_name, 0.75)
        if not vid:
            vid, _ = vendors.find_in(own)
        lookalike = bool(vid) and "@" in mail.from_addr and \
            not any(mail.from_addr.endswith(d) for d in ("gmail.com", "yahoo.com", "outlook.com"))
    if from_keyway:
        refs = keyway_refs(mail.subject + "\n" + mail.body)
        nos = vendor_invoice_numbers(mail.subject + "\n" + mail.body)
        p = Parsed(source, SourceKind.manager_reply, dated=sent, method="email", text=own.strip()[:800])
        p.data = {"invoice_id": refs[0] if refs else None,
                  "status": "paid" if _PAID_CLAIM.search(own) else "other",
                  "reply_id": source.rsplit("/", 1)[-1].removesuffix(".eml")}
        if nos and not refs:
            p.data["vendor_ref"] = nos[0]
        if not refs and not nos:
            p.flag("reply doesn't say which invoice", human=True, conf=0.4)
        elif not keyway_refs(own):
            p.flag("invoice named only in the quoted message", conf=0.85)
        if p.data["status"] == "paid" and not re.search(r"\b(ach|check|chk|trace|confirmation|"
                                                         r"proof|attached)\b", own, re.IGNORECASE):
            p.flag("says paid but gives no proof (no trace number, check or attachment)")
        return [p]
    refs = keyway_refs(own)
    nos = vendor_invoice_numbers(mail.subject + "\n" + own)
    if _W9_SENT.search(own):
        p = Parsed(source, SourceKind.vendor_notice, vendor_id=vid, dated=sent, method="email",
                   text=own.strip()[:800])
        p.data = {"invoice_id": refs[0] if refs else None, "status": "paperwork_received"}
        if nos:
            p.data["vendor_ref"] = nos[0]
        return [p]
    if _BANK_CHANGE.search(own):
        p = Parsed(source, SourceKind.vendor_notice, vendor_id=vid, dated=sent, method="email",
                   text=own.strip()[:800])
        last4 = re.search(r"ending (?:in )?(\d{4})", own)
        p.data = {"invoice_id": refs[0] if refs else None, "status": "bank_change_request",
                  "new_account_last4": last4[1] if last4 else None}
        if nos:
            p.data["vendor_ref"] = nos[0]
        if lookalike:
            p.flag(f"sender {mail.from_addr} is not the vendor's address on file", conf=0.9)
        return [p]
    if _PAST_DUE.search(own) or _PAST_DUE.search(mail.subject):
        p = Parsed(source, SourceKind.vendor_notice, vendor_id=vid, dated=sent, method="email",
                   text=own.strip()[:800])
        p.data = {"invoice_id": refs[0] if refs else None, "status": "not_received"}
        if nos:
            p.data["vendor_ref"] = nos[0]
        if not refs:
            p.meta["needs_invoice_id"] = True
            p.meta["amount"] = (money_values(own) or [None])[0]
        return [p]
    if _CONFIRM.search(own) and lookalike:
        # a payment confirmation is proof only from the vendor's own address
        p = Parsed(source, None, method="email", text=own.strip()[:800])
        p.flag(f"'payment received' from {mail.from_addr}, not the vendor's address on file: "
               "not treated as proof", human=True)
        return [p]
    if _CONFIRM.search(own):
        p = Parsed(source, SourceKind.vendor_confirmation, vendor_id=vid, dated=sent,
                   method="email", text=own.strip()[:800])
        amounts = money_values(own)
        line = next((ln for ln in own.splitlines() if _CONFIRM.search(ln)), own)
        stripped = re.sub(r"\$?[\d,]+\.\d\d", "", re.sub(r"(?<![A-Z])KW[\s\-#]*\d{3,4}", "", line))
        paid_on = parse_date(stripped, sent)
        p.data = {"status": "paid", "amount": amounts[0] if amounts else None,
                  "paid_on": paid_on.isoformat() if paid_on else None}
        if refs:
            p.data["invoice_id"] = refs[0]
        else:
            p.meta["needs_invoice_id"] = True
        if nos:
            p.data["vendor_ref"] = nos[0]
        if not vid:
            p.flag("can't tell which vendor sent this", human=True, conf=0.3)
        return [p]
    p = Parsed(source, None, method="email")
    p.flag(f"email not understood: '{mail.subject}'", human=True)
    return [p]
