"""Render a clean World as the raw paperwork a controller actually receives.

    raw/inbox/*.eml   vendor invoices forwarded by Keyway (PDF attached), past-due notices,
                      payment confirmations, Keyway's replies, statements, plus junk mail
    raw/bank/*.csv    Keyway's bank exports: truncated payee names, combined payments,
                      fees and transfers mixed in
    raw/drive/*.pdf   Oakline wire confirmations (one wire covers many invoices), the
                      management agreement, loose scanned invoices

Facts never change, only how they are written. manifest.json maps every raw file back to the
clean documents it came from, so the parser can be scored against the truth.
"""

from __future__ import annotations

import csv
import io
import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path

from contracts.models import Document, SourceKind

from . import noise as N
from .pdfs import (
    funding_confirmation_pdf,
    invoice_pdf,
    scanned_invoice_pdf,
    simple_pdf,
    statement_pdf,
)

SCAN_RATE = 0.12          # background invoices that arrive as a crooked scan, no text layer
PRIOR_BAL_RATE = 0.07    # background invoices that also carry an old unpaid balance
HTML_ONLY_RATE = 0.15    # vendor emails sent as HTML only, no plain-text part
NO_REF_RATE = 0.06        # background invoices with no Keyway work-order reference at all
BATCH_RATE = 0.5          # same vendor, same day: paid as one combined ACH
MEMO_REF_RATE = 0.55      # bank memo carries the Keyway reference
CONF_REF_RATE = 0.5       # vendor confirmation email mentions the invoice reference


@dataclass
class RawFile:
    path: str                                   # relative to the raw/ root
    truth: list[str]                            # clean document ids it carries
    noise: list[str] = field(default_factory=list)
    expect: str = "parse"                       # parse | ignore | needs_human_without_ocr


@dataclass
class Rendered:
    root: Path
    files: list[RawFile] = field(default_factory=list)

    def add(self, rel: str, data: bytes | str, truth: list[str], noise=(), expect="parse"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data.encode() if isinstance(data, str) else data)
        self.files.append(RawFile(rel, list(truth), list(noise), expect))


def _dt(d: date, rng: random.Random, hour: tuple[int, int] = (7, 18)) -> datetime:
    return datetime(d.year, d.month, d.day, rng.randint(*hour), rng.randint(0, 59),
                    tzinfo=timezone(timedelta(hours=-6)))


def _vendor_addr(rng: random.Random, vid: str) -> str:
    if vid in N.FREEMAIL:
        return f"{N.vendor_name(rng, vid)} <{N.FREEMAIL[vid]}>"
    first = rng.choice(["billing", "office", "accounts", "ar", "invoices"])
    return f"{N.vendor_name(rng, vid)} <{first}@{N.VENDOR_DOMAINS.get(vid, 'example.com')}>"


def _eml(frm: str, to: str, subject: str, body: str, when: datetime,
         attachments: list[tuple[str, bytes, str]] = (), cc: str | None = None,
         in_reply_to: str | None = None, html_only: bool = False) -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = frm, to, subject
    if cc:
        m["Cc"] = cc
    m["Date"] = format_datetime(when)
    m["Message-ID"] = make_msgid(domain=frm.split("@")[-1].strip(">"))
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
    if html_only:
        paras = "".join(f"<p>{ln or '&nbsp;'}</p>" for ln in body.replace("&", "&amp;").split("\n"))
        m.set_content(f"<html><body style=\"font-family:Arial\"><div>{paras}</div></body></html>",
                      subtype="html")
    else:
        m.set_content(body)
    for name, data, mime in attachments:
        maintype, subtype = mime.split("/")
        m.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return bytes(m)


def _split_amount(rng: random.Random, amount: float) -> list[tuple[str, float]]:
    """Line items that add up to the invoice total (labor + materials, sometimes a trip fee)."""
    cents = round(amount * 100)
    if cents < 15000 or rng.random() < 0.3:
        return [("Labor", cents / 100)]
    trip = rng.choice([0, 0, 7500, 9500]) if cents > 30000 else 0
    rest = cents - trip
    labor = round(rest * rng.uniform(0.45, 0.75) / 5) * 5
    items = [("Labor", labor / 100), ("Materials", (rest - labor) / 100)]
    if trip:
        items.append(("Trip charge", trip / 100))
    return items


# ---------------------------------------------------------------------------------------------
# One renderer per clean document kind
# ---------------------------------------------------------------------------------------------

class Renderer:
    def __init__(self, world, root: Path, seed: int):
        self.w = world
        self.rng = random.Random(seed * 7919 + 17)
        self.out = Rendered(root)
        self.vendors = {v.id: v for v in world.vendors}
        self.scenario_docs = self._scenario_doc_ids()
        self.inv_by_id = {d.data.get("invoice_id"): d for d in world.documents
                          if d.kind == SourceKind.vendor_invoice}

    def _scenario_doc_ids(self) -> set[str]:
        """Documents a planted scenario or the moment depends on. They get mess, but never mess
        that only OCR could undo (no scans, no missing references)."""
        ids: set[str] = set()
        planted = {t["invoice_id"] for t in self.w.truth if t.get("invoice_id")}
        if self.w.moment:
            planted.add("INV-KW-0402")
        for t in self.w.truth:
            ids.update(t.get("must_cite", []))
        for d in self.w.documents:
            inv = d.data.get("invoice_id") or ""
            if inv in planted or (inv[-4:].isdigit() and int(inv[-4:]) < 1000):
                ids.add(d.id)          # the hand-written scenario invoices (KW-0101 to KW-0901)
        return ids

    # -- vendor invoices -----------------------------------------------------------------------
    def invoices(self) -> None:
        rng = self.rng
        dup_done = False
        for d in [d for d in self.w.documents if d.kind == SourceKind.vendor_invoice]:
            inv, amt, vid = d.data["invoice_id"], float(d.data["amount"]), d.vendor_id
            critical = d.id in self.scenario_docs
            tags: list[str] = []
            vname = N.vendor_name(rng, vid)
            if vname != N.VENDOR_VARIANTS.get(vid, [vname])[0]:
                tags.append("vendor_name_variant")
            ref = None if (not critical and rng.random() < NO_REF_RATE) else N.keyway_ref(rng, inv)
            if ref is None:
                tags.append("no_keyway_ref")
            received = date.fromisoformat(d.data["received"]) if d.data.get("received") else None
            inv_date = (received or d.dated) - timedelta(days=rng.choice([0, 0, 1, 2, 3]))
            what = d.text.split(". ")[2] if d.text.count(". ") >= 3 else ""
            if not what or what.endswith(" service"):
                what = rng.choice(N.TRADE_WORK.get(self.vendors[vid].trade, ["Service call"]))
            vendor_no = d.data.get("vendor_ref") or N.vendor_invoice_no(rng, vid, inv)
            fields = dict(vendor_name=vname, vendor_no=vendor_no, keyway_ref=ref,
                          inv_date=N.fmt_date(rng, inv_date), work=what,
                          site=N.address(rng), items=_split_amount(rng, amt), total=amt,
                          terms=rng.choice(["Net 10", "Net 10", "Due on receipt", "Net 15"]),
                          prior_credit=0.0, layout=rng.randrange(4),
                          prior=0.0, prior_style=rng.choice(["current", "pastdue"]),
                          note=("Final inspection pending - 10% retainage per contract"
                                if d.data.get("inspection") == "pending" else ""))
            if not critical and rng.random() < PRIOR_BAL_RATE:
                fields["prior"] = round(rng.uniform(150, 1800), 2)
                tags.append(f"prior_balance_{fields['prior_style']}")
            if fields["terms"] != "Net 10":
                tags.append("terms_disagree_with_agreement")
            scan = not critical and rng.random() < SCAN_RATE
            pdf = scanned_invoice_pdf(fields, rng) if scan else invoice_pdf(fields)
            if scan:
                tags.append("scanned_no_text_layer")
            fname = f"{vendor_no.replace(' ', '')}.pdf" if rng.random() < 0.6 else \
                rng.choice(["invoice.pdf", "scan0001.pdf", f"Invoice_{inv_date:%m%d}.pdf"])
            expect = "needs_human_without_ocr" if scan else "parse"
            if received is None:
                # Nobody knows when this one arrived: it sits loose in the shared drive.
                self.out.add(f"drive/scans/{d.id.replace('doc-', '')}_{fname}", pdf, [d.id],
                             tags + ["no_received_date"], expect)
                continue
            first = self._forward_invoice(d, received, vname, vid, fname, pdf, tags, expect)
            if not dup_done and not critical and rng.random() < 0.08:
                dup_done = True       # the same invoice forwarded twice: dedupe, don't double count
                self._forward_invoice(d, received, vname, vid, fname, pdf,
                                      tags + ["duplicate_forward"], expect, after=first)

    def _forward_invoice(self, d: Document, received: date, vname: str, vid: str, fname: str,
                         pdf: bytes, tags: list[str], expect: str,
                         after: datetime | None = None) -> datetime:
        rng = self.rng
        orig_when = _dt(received, rng)
        fwd_when = orig_when + timedelta(hours=rng.randint(1, 30))
        if after is not None:          # Keyway resends it a day or two later
            fwd_when = after + timedelta(hours=rng.randint(20, 50))
        fwd_when = min(fwd_when, _dt(self.w.as_of, rng, (6, 7)))
        frm = _vendor_addr(rng, vid)
        orig_subject = rng.choice([f"Invoice from {vname}", "invoice attached", f"{vname} invoice",
                                   "Inv for completed work", "Please see attached"])
        orig_date = rng.choice([format_datetime(orig_when),
                                orig_when.strftime("%a, %b %d, %Y at %I:%M %p")])
        body = (f"{rng.choice(['Hi Oakline team,', 'FYI - for funding.', 'Please fund.', ''])}\n\n"
                f"-------- Forwarded message --------\nFrom: {frm}\nDate: {orig_date}\n"
                f"Subject: {orig_subject}\nTo: {N.KEYWAY_AP}\n\n"
                f"{rng.choice(['Hello,', 'Hi Dana,', 'Good afternoon'])}\n"
                f"{rng.choice(['Attached is our invoice for the work completed.', 'Invoice attached.', 'please find attached inv. thanks'])}\n"
                f"{rng.choice(['Thank you for your business!', 'Thanks', ''])}\n")
        subj = ("FW: FW: " if after else rng.choice(["FW: ", "Fwd: ", "FW: "])) + orig_subject
        sender = rng.choice(N.KEYWAY_PEOPLE)
        eml = _eml(sender, N.OAKLINE_AP, subj, body, fwd_when, [(fname, pdf, "application/pdf")])
        stamp = fwd_when.strftime("%Y%m%d_%H%M")
        self.out.add(f"inbox/{stamp}_{rng.randrange(16**6):06x}.eml", eml, [d.id], tags, expect)
        return fwd_when

    # -- Oakline wires (one wire, many invoices) -----------------------------------------------
    def funding(self) -> None:
        rng = self.rng
        by_day: dict[date, list[Document]] = defaultdict(list)
        for d in self.w.documents:
            if d.kind == SourceKind.funding:
                by_day[d.dated].append(d)
        for day, docs in sorted(by_day.items()):
            lines = []
            for d in docs:
                inv = d.data["invoice_id"]
                src = self.inv_by_id.get(inv)
                vid = src.vendor_id if src else None
                lines.append(dict(ref=inv if rng.random() < 0.7 else inv.replace("INV-", ""),
                                  vendor=N.vendor_name(rng, vid, 0.8) if vid else "",
                                  amount=float(d.data["amount"])))
            wire_no = f"FW{day:%y%m%d}{rng.randint(100, 999)}"
            pdf = funding_confirmation_pdf(dict(date=N.fmt_date(rng, day, "us"), wire_no=wire_no, lines=lines,
                                total=round(sum(x["amount"] for x in lines), 2)))
            tags = ["one_wire_many_invoices"] if len(lines) > 1 else []
            self.out.add(f"drive/wires/Wire_Confirmation_{wire_no}.pdf", pdf,
                         [d.id for d in docs], tags)

    # -- Keyway statements (PDF attached to an email) ------------------------------------------
    def statements(self) -> None:
        rng = self.rng
        for d in [d for d in self.w.documents if d.kind == SourceKind.manager_statement]:
            rows = []
            for ln in d.data.get("lines", []):
                inv = ln["invoice_id"]
                src = self.inv_by_id.get(inv)
                vid = src.vendor_id if src else None
                ref = (src.data.get("vendor_ref") if src and src.data.get("vendor_ref")
                       else inv.replace("INV-", "") if rng.random() < 0.5 else inv)
                on = ln.get("on")
                note = ""
                if ln.get("received"):
                    note = f"inv rec'd {N.fmt_date(rng, date.fromisoformat(ln['received']), 'us2')}"
                rows.append(dict(line=int(ln["line"][1:]), date=N.fmt_date(rng, date.fromisoformat(on), "us2") if on else "",
                                 vendor=N.vendor_name(rng, vid, 0.6) if vid else "", ref=ref,
                                 amount=float(ln["amount"]),
                                 status=rng.choice(N.STATUS_WORDS.get(ln["status"], [ln["status"]])),
                                 note=note))
            title = d.text.split(":")[0].split(". L")[0].strip().rstrip(".")
            pdf = statement_pdf(dict(title=title, period=d.dated.strftime("%B %Y"),
                                     prepared=N.fmt_date(rng, d.dated, "long"), rows=rows))
            when = _dt(min(d.dated + timedelta(days=rng.randint(0, 3)), self.w.as_of), rng)
            body = rng.choice(["Attached is the statement for your properties.",
                               "Hi - statement attached. Let us know if questions.",
                               "Please see the attached owner statement."])
            eml = _eml(rng.choice(N.KEYWAY_PEOPLE), N.OAKLINE_AP,
                       f"{title} - {d.dated:%b %Y}", body, when,
                       [(f"Keyway_Stmt_{d.dated:%Y%m}_{rng.randrange(99):02d}.pdf", pdf,
                         "application/pdf")])
            self.out.add(f"inbox/{when:%Y%m%d_%H%M}_{rng.randrange(16**6):06x}.eml", eml, [d.id],
                         ["status_word_variants"])

    # -- Keyway bank exports -------------------------------------------------------------------
    def bank(self) -> None:
        rng = self.rng
        for d in [d for d in self.w.documents if d.kind == SourceKind.manager_bank]:
            pays = d.data.get("payments", [])
            if pays:
                start = min(date.fromisoformat(p["on"]) for p in pays) - timedelta(days=2)
            else:
                start = d.dated - timedelta(days=30)
            end = d.dated
            rows: list[dict] = []
            tags: list[str] = []
            groups: dict[tuple, list[dict]] = defaultdict(list)
            for p in pays:
                groups[(p["vendor_id"], p["on"])].append(p)
            for (vid, on), ps in groups.items():
                if len(ps) > 1 and rng.random() < BATCH_RATE:
                    refs = " ".join(N.keyway_ref(rng, p["invoice_id"]).replace(" ", "")
                                    for p in ps) if rng.random() < 0.5 else ""
                    rows.append(self._bank_row(vid, on, sum(p["amount"] for p in ps), refs))
                    tags.append("combined_payment")
                    continue
                for p in ps:
                    memo = ""
                    inv = p["invoice_id"]
                    src = self.inv_by_id.get(inv)
                    if src and abs(float(src.data["amount"]) - p["amount"]) > 0.005:
                        memo = f"{N.keyway_ref(rng, inv).replace(' ', '')} LESS RETAINAGE"
                        tags.append("partial_payment")
                    elif rng.random() < MEMO_REF_RATE:
                        memo = N.keyway_ref(rng, inv).replace(" ", "")
                    rows.append(self._bank_row(vid, on, p["amount"], memo))
            rows += self._filler(start, end)
            rows.sort(key=lambda r: (r["date"], r["desc"]))
            bal = round(rng.uniform(40000, 90000), 2)
            buf = io.StringIO()
            buf.write(f"Account Name: KEYWAY PROPERTY MGMT OPERATING,Account: ****{rng.randint(1000, 9999)}\n")
            buf.write(f"Statement Period: {start:%m/%d/%Y} - {end:%m/%d/%Y},Generated: {end:%m/%d/%Y}\n\n")
            wr = csv.writer(buf)
            wr.writerow(["Posting Date", "Description", "Check #", "Debit", "Credit", "Balance"])
            for r in rows:
                bal = round(bal - r["debit"] + r["credit"], 2)
                wr.writerow([r["date"].strftime("%m/%d/%Y"), r["desc"], r.get("check", ""),
                             f"{r['debit']:.2f}" if r["debit"] else "",
                             f"{r['credit']:.2f}" if r["credit"] else "", f"{bal:.2f}"])
            if not pays:
                tags.append("no_vendor_payment_in_period")
            self.out.add(f"bank/FPB_Operating_{start:%Y%m%d}_{end:%Y%m%d}_{d.id[-6:]}.csv",
                         buf.getvalue(), [d.id], sorted(set(tags + ["filler_rows"])))

    def _bank_row(self, vid: str, on: str, amount: float, memo: str) -> dict:
        rng = self.rng
        name = N.bank_name(rng, vid)
        if rng.random() < 0.2:
            chk = str(rng.randint(10200, 10999))
            return dict(date=date.fromisoformat(on), desc=f"CHECK {chk} {name}".strip(),
                        check=chk, debit=round(amount, 2), credit=0.0)
        kind = rng.choice(["ACH DEBIT", "ACH PMT", "BILL PAY", "ONLINE PMT"])
        desc = f"{kind} {name} {memo}".strip()
        if rng.random() < 0.3:
            desc += f" PPD ID:{rng.randint(10**9, 10**10 - 1)}"
        return dict(date=date.fromisoformat(on), desc=desc, debit=round(amount, 2), credit=0.0)

    def _filler(self, start: date, end: date) -> list[dict]:
        rng = self.rng
        span = max((end - start).days, 1)
        day = lambda: start + timedelta(days=rng.randrange(span))
        rows = [dict(date=day(), desc="WIRE IN OAKLINE HOMES LLC REF OAK-FUND", debit=0.0,
                     credit=round(rng.uniform(3000, 25000), 2)),
                dict(date=end, desc="MONTHLY SERVICE FEE", debit=25.0, credit=0.0),
                dict(date=day(), desc="ONLINE TRANSFER TO SAV ****8812", debit=round(
                    rng.uniform(500, 4000), 2), credit=0.0),
                dict(date=day(), desc="ADP PAYROLL FEES", debit=round(rng.uniform(80, 300), 2),
                     credit=0.0)]
        if rng.random() < 0.4:
            rows.append(dict(date=day(), desc="HOME DEPOT #4412 PURCHASE", debit=round(
                rng.uniform(20, 400), 2), credit=0.0))
        return rows

    # -- vendor emails: confirmations, past-due notices, bank change ---------------------------
    def vendor_mail(self) -> None:
        rng = self.rng
        groups: dict[tuple, list[Document]] = defaultdict(list)
        for d in self.w.documents:
            if d.kind == SourceKind.vendor_confirmation:
                groups[(d.vendor_id, d.data["paid_on"])].append(d)
            elif d.kind == SourceKind.vendor_notice:
                self._notice(d)
        for (_, _), docs in groups.items():
            if len(docs) > 1 and not any(d.id in self.scenario_docs for d in docs) \
                    and rng.random() < 0.7:
                self._combined_confirmation(docs)
            else:
                for d in docs:
                    self._confirmation(d)

    def _combined_confirmation(self, docs: list[Document]) -> None:
        rng = self.rng
        d0 = docs[0]
        total = round(sum(float(d.data["amount"]) for d in docs), 2)
        paid_on = date.fromisoformat(d0.data["paid_on"])
        body = (f"Hi,\n\nWe received {N.fmt_money(rng, total)} on {N.fmt_date(rng, paid_on, 'us2')} "
                f"covering both open invoices. Thanks, we're all caught up.\n\n"
                f"{N.vendor_name(rng, d0.vendor_id)}\n")
        when = _dt(max(d.dated for d in docs), rng)
        eml = _eml(_vendor_addr(rng, d0.vendor_id), N.OAKLINE_AP, "Re: payment confirmation",
                   body, when, html_only=rng.random() < HTML_ONLY_RATE)
        self.out.add(f"inbox/{when:%Y%m%d_%H%M}_{rng.randrange(16**6):06x}.eml", eml,
                     [d.id for d in docs], ["one_confirmation_two_invoices"])

    def _confirmation(self, d: Document) -> None:
        rng = self.rng
        inv, amt = d.data["invoice_id"], float(d.data["amount"])
        paid_on = date.fromisoformat(d.data["paid_on"])
        crit = d.id in self.scenario_docs
        ref = N.keyway_ref(rng, inv) if (crit or rng.random() < CONF_REF_RATE) else None
        money = N.fmt_money(rng, amt)
        when_paid = rng.choice([N.fmt_date(rng, paid_on), paid_on.strftime("%A"),
                                N.fmt_date(rng, paid_on, "us2")])
        if not crit and when_paid == paid_on.strftime("%A"):
            when_paid = N.fmt_date(rng, paid_on, "us2")
        line = rng.choice([
            f"We received your payment of {money} on {when_paid}" + (f" for {ref}" if ref else "") + ". Thank you!",
            f"Confirming {money} hit our account {when_paid}" + (f" ({ref})" if ref else "") + ". Appreciate it.",
            f"Payment rec'd {when_paid}, {money}" + (f", ref {ref}" if ref else "") + ". Account is current.",
        ])
        site = N.address(rng)
        if not ref:
            line += f" That covers the {rng.choice(['job', 'work', 'service'])} at {site}."
        body = (f"{rng.choice(['Hi,', 'Hello Oakline,', 'Hi there'])}\n\n{line}\n\n"
                f"{N.vendor_name(rng, d.vendor_id)}\n{rng.choice(['Office Manager', 'Billing', 'Owner'])}\n"
                f"\n> On {N.fmt_date(rng, d.dated - timedelta(days=1), 'long')}, Oakline AP wrote:\n"
                f"> Can you confirm whether you've been paid for recent work for Keyway Property "
                f"Management?\n")
        when = _dt(d.dated, rng)
        html = rng.random() < HTML_ONLY_RATE
        eml = _eml(_vendor_addr(rng, d.vendor_id), N.OAKLINE_AP,
                   rng.choice(["Re: payment confirmation", "RE: Payment status", "Re: Paid?"]),
                   body, when, html_only=html)
        tags = ([] if ref else ["no_invoice_ref_amount_only"]) + (["html_only_email"] if html else [])
        self.out.add(f"inbox/{when:%Y%m%d_%H%M}_{rng.randrange(16**6):06x}.eml", eml, [d.id], tags)

    def _notice(self, d: Document) -> None:
        rng = self.rng
        inv = d.data["invoice_id"]
        src = self.inv_by_id.get(inv)
        when = _dt(d.dated, rng)
        if d.data.get("status") == "bank_change_request":
            body = ("Hello,\n\nPlease be advised our banking information has changed. Our old "
                    "account is closed. Please send payment for "
                    f"{N.keyway_ref(rng, inv)} ({N.fmt_money(rng, float(src.data['amount']), 'dollar')}) "
                    f"to our new account ending {d.data['new_account_last4']}. New ACH form "
                    "attached. This is time sensitive, please update today.\n\nThanks,\n"
                    f"{N.vendor_name(rng, d.vendor_id)} Accounts\n")
            frm = (f"{N.vendor_name(rng, d.vendor_id)} Accounts "
                   f"<accounts@{N.VENDOR_DOMAINS[d.vendor_id].replace('.com', '-pay.com')}>")
            eml = _eml(frm, N.OAKLINE_AP, "URGENT: updated remittance info", body, when,
                       [("ACH_Update_Form.pdf",
                         simple_pdf("ACH Authorization Update", [
                             f"Vendor: {N.vendor_name(rng, d.vendor_id)}",
                             f"New account ending: {d.data['new_account_last4']}",
                             "Routing: on file with bank", "Effective immediately"]),
                         "application/pdf")])
            self.out.add(f"inbox/{when:%Y%m%d_%H%M}_{rng.randrange(16**6):06x}.eml", eml, [d.id],
                         ["lookalike_sender_domain"])
            return
        amt = float(src.data["amount"]) if src else 0.0
        inv_date = date.fromisoformat(src.data["received"]) if src and src.data.get("received") else d.dated
        days = (d.dated - inv_date).days
        body = (f"{rng.choice(['Hello,', 'To whom it may concern,'])}\n\n"
                f"Our records show the following invoice is PAST DUE:\n\n"
                f"  Invoice/Ref     Date        Amount      Days\n"
                f"  {N.keyway_ref(rng, inv):<14}  {inv_date:%m/%d/%y}    {N.fmt_money(rng, amt, 'dollar'):<10}  {days}\n\n"
                f"We have not received payment as of {N.fmt_date(rng, d.dated, 'us2')}. Please remit "
                f"immediately or call our office. We may pause new work orders until the balance "
                f"is cleared.\n\n{N.vendor_name(rng, d.vendor_id)}\n")
        eml = _eml(_vendor_addr(rng, d.vendor_id), f"{N.OAKLINE_AP}, {N.KEYWAY_AP}",
                   rng.choice(["PAST DUE - 2nd notice", "Past due invoice", "Overdue balance"]),
                   body, when)
        self.out.add(f"inbox/{when:%Y%m%d_%H%M}_{rng.randrange(16**6):06x}.eml", eml, [d.id],
                     ["table_in_plain_text"])

    # -- Keyway replies to a chase -------------------------------------------------------------
    def replies(self) -> None:
        rng = self.rng
        for d in [d for d in self.w.documents if d.kind == SourceKind.manager_reply]:
            when = _dt(d.dated, rng)
            body = (f"{d.text}\n\nDana Ortiz\nKeyway Property Management\n(555) 014-2290\n\n"
                    f"On {N.fmt_date(rng, d.dated - timedelta(days=2), 'long')}, Oakline AP "
                    f"<ap-intake@oaklinehomes.com> wrote:\n> Following up on "
                    f"{d.data['invoice_id']}. The vendor reports it is unpaid. Please send proof "
                    f"of payment.\n")
            eml = _eml(N.KEYWAY_PEOPLE[0], N.OAKLINE_AP, f"Re: {d.data['invoice_id']} - payment status",
                       body, when)
            self.out.add(f"inbox/{when:%Y%m%d_%H%M}_{rng.randrange(16**6):06x}.eml", eml, [d.id],
                         ["claim_without_proof", "id_only_in_quoted_text"])

    # -- agreement -----------------------------------------------------------------------------
    def agreement(self) -> None:
        for d in [d for d in self.w.documents if d.kind == SourceKind.agreement]:
            paras = ["PROPERTY MANAGEMENT AGREEMENT",
                     "between Oakline Homes LLC (Owner) and Keyway Property Management (Manager)",
                     f"Effective Date: {d.dated:%B} {d.dated.day}, {d.dated.year}",
                     "1. Appointment. Owner appoints Manager as exclusive agent for the Properties.",
                     "5.1 Management Fee. Eight percent (8%) of gross collected rents.",
                     *d.text.split("\n"),
                     "9.3 Records. Manager shall provide monthly statements by the 15th.",
                     "12. Term. This Agreement continues month to month."]
            self.out.add("drive/contracts/Keyway_PMA_executed.pdf",
                         simple_pdf("Property Management Agreement", paras), [d.id])

    # -- junk that a real inbox is full of -----------------------------------------------------
    def junk(self) -> None:
        rng = self.rng
        start = self.w.as_of - timedelta(days=100)
        items = [
            ("Keyway Property Management <news@keywaypm.com>", "Keyway Quarterly: spring maintenance tips",
             "Spring is here! Remember to schedule HVAC tune-ups. Unsubscribe here.", []),
            ("Dana Ortiz <dana.ortiz@keywaypm.com>", "Automatic reply: invoice question",
             "I am out of the office until Monday with limited access to email.", []),
            (_vendor_addr(rng, "v-paintpro"), "Estimate for exterior paint, 44 Willow Dr",
             "Hi, estimate attached as requested. This is not an invoice.",
             [("Estimate_EST-2291.pdf", simple_pdf("ESTIMATE", [
                 "PaintPro Painting", "Estimate EST-2291", "Exterior paint, 44 Willow Dr",
                 "Estimated total: $6,450.00", "THIS IS AN ESTIMATE, NOT AN INVOICE"]),
               "application/pdf")]),
            (_vendor_addr(rng, "v-yardpro"), "W-9 for your records",
             "Attached is our current W-9.", [("W9_YardPro.pdf", simple_pdf("Form W-9", [
                 "Request for Taxpayer Identification Number", "Name: YardPro Landscaping LLC",
                 "TIN: on file"]), "application/pdf")]),
            (_vendor_addr(rng, "v-ironpipe"), "Credit memo",
             "Attached is a credit memo for the overcharge on the last service call.",
             [("CM-118.pdf", simple_pdf("CREDIT MEMO", [
                 "Ironpipe Plumbing", "Credit Memo CM-118", "Bill To: Keyway Property Management",
                 "Overcharge on service call, trip fee billed twice", "Credit amount: -$75.00",
                 "Apply to your account balance"]), "application/pdf")]),
            ("Marcus Lee <marcus@keywaypm.com>", "statement attached",
             "Here is the statement. (no attachment)", []),
        ]
        for frm, subj, body, att in items:
            when = _dt(start + timedelta(days=rng.randrange(100)), rng)
            eml = _eml(frm, N.OAKLINE_AP, subj, body, when, att)
            credit = subj == "Credit memo"
            self.out.add(f"inbox/{when:%Y%m%d_%H%M}_{rng.randrange(16**6):06x}.eml", eml, [],
                         ["credit_memo" if credit else "distractor"], "flag" if credit else "ignore")

    def run(self) -> Rendered:
        self.agreement()
        self.invoices()
        self.funding()
        self.statements()
        self.bank()
        self.vendor_mail()
        self.replies()
        self.junk()
        return self.out


def render(world, root: Path, seed: int | None = None) -> Rendered:
    """Write the raw folder and manifest.json. Returns what was written."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    out = Renderer(world, root, world.seed if seed is None else seed).run()
    manifest = {
        "seed": world.seed, "as_of": world.as_of.isoformat(),
        "files": [f.__dict__ for f in out.files],
        "truth_docs": {d.id: {"kind": d.kind.value, "vendor_id": d.vendor_id,
                              "dated": d.dated.isoformat() if d.dated else None, "data": d.data}
                       for d in world.documents},
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=1, default=str) + "\n")
    return out
