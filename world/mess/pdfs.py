"""PDF writers for the messy world: four invoice layouts, crooked scans with no text layer,
statements, wire confirmations. Deterministic output (reportlab invariant mode)."""

from __future__ import annotations

import io
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab import rl_config
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

rl_config.invariant = 1
W, H = letter


def _canvas() -> tuple[io.BytesIO, canvas.Canvas]:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter, invariant=1)
    return buf, c


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _invoice_lines(f: dict) -> list[tuple[str, str]]:
    """Label/value pairs every layout shows, in that layout's own wording."""
    lab = [("Invoice #", "Invoice No.", "INVOICE", "Inv")[f["layout"]],
           ("Date", "Invoice Date", "Dated", "Date of service")[f["layout"]],
           ("Customer PO", "Your Ref", "P.O. / Work Order", "Ref")[f["layout"]],
           ("Amount Due", "Balance Due", "TOTAL DUE", "Please pay")[f["layout"]]]
    rows = [(lab[0], f["vendor_no"]), (lab[1], f["inv_date"])]
    if f["keyway_ref"]:
        rows.append((lab[2], f["keyway_ref"]))
    return rows


def invoice_pdf(f: dict) -> bytes:
    buf, c = _canvas()
    lay = f["layout"]
    y = H - 60
    if lay in (0, 2):
        c.setFont("Helvetica-Bold", 18)
        c.drawString(50, y, f["vendor_name"])
        c.setFont("Helvetica", 9)
        c.drawString(50, y - 14, "Licensed & Insured  |  Serving the metro area since 2009")
        c.setFont("Helvetica-Bold", 22 if lay == 0 else 14)
        c.drawRightString(W - 50, y, "INVOICE")
    elif lay == 1:
        c.rect(40, y - 30, W - 80, 50)
        c.setFont("Helvetica-Bold", 14)
        c.drawCentredString(W / 2, y - 2, f["vendor_name"].upper())
        c.setFont("Helvetica", 9)
        c.drawCentredString(W / 2, y - 18, "Invoice")
        y -= 20
    else:
        c.setFont("Times-Roman", 12)
        c.drawString(60, y, f["vendor_name"])
        c.drawString(60, y - 16, "Invoice")
    y -= 50
    c.setFont("Helvetica", 10)
    for label, val in _invoice_lines(f):
        if lay == 3:
            c.drawString(60, y, f"{label}: {val}")
        else:
            c.drawString(W - 260, y, label)
            c.drawRightString(W - 50, y, val)
        y -= 15
    y -= 10
    c.drawString(50, y, "Bill To: Keyway Property Management, 200 Commerce Blvd Ste 4")
    y -= 15
    c.drawString(50, y, f"Job site: {f['site']}  (owner: Oakline Homes)")
    y -= 25
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "Description")
    c.drawRightString(W - 50, y, "Amount")
    c.line(50, y - 4, W - 50, y - 4)
    c.setFont("Helvetica", 10)
    y -= 18
    c.drawString(50, y, f["work"])
    y -= 15
    for name, amt in f["items"]:
        c.drawString(70, y, name)
        c.drawRightString(W - 50, y, _money(amt))
        y -= 15
    c.line(W - 220, y + 5, W - 50, y + 5)
    y -= 8
    c.drawString(W - 260, y, "Subtotal")
    c.drawRightString(W - 50, y, _money(f["total"]))
    y -= 15
    c.drawString(W - 260, y, "Tax")
    c.drawRightString(W - 50, y, "$0.00" if lay != 2 else "N/A (services)")
    due = f["total"]
    if f.get("prior"):
        y -= 15
        if f["prior_style"] == "current":
            c.drawString(W - 260, y, "Current charges")
            c.drawRightString(W - 50, y, _money(f["total"]))
            y -= 15
            c.drawString(W - 260, y, "Previous balance")
        else:
            c.drawString(W - 260, y, f"Past due ({f['vendor_no'][:3]}{int(f['vendor_no'][-4:]) - 37})")
        c.drawRightString(W - 50, y, _money(f["prior"]))
        due = round(f["total"] + f["prior"], 2)
    y -= 18
    c.setFont("Helvetica-Bold", 11)
    c.drawString(W - 260, y, ("Amount Due", "Balance Due", "TOTAL DUE", "Please pay")[lay])
    c.drawRightString(W - 50, y, _money(due))
    c.setFont("Helvetica", 9)
    y -= 30
    c.drawString(50, y, f"Terms: {f['terms']}.  Make checks payable to {f['vendor_name']}.")
    if f.get("note"):
        y -= 14
        c.drawString(50, y, f["note"])
    c.drawString(50, 50, "Thank you for your business!")
    c.showPage()
    c.save()
    return buf.getvalue()


def _font(size: int):
    for name in ("DejaVuSans.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
                 "/Library/Fonts/Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def scanned_invoice_pdf(f: dict, rng: random.Random) -> bytes:
    """The invoice as a phone scan: an image, slightly rotated, grey, speckled. No text layer."""
    img = Image.new("L", (1275, 1650), 250)
    d = ImageDraw.Draw(img)
    big, mid = _font(44), _font(28)
    y = 90
    d.text((90, y), f["vendor_name"], font=big, fill=20)
    d.text((900, y), "INVOICE", font=big, fill=20)
    y += 120
    for label, val in _invoice_lines(f):
        d.text((90, y), f"{label}: {val}", font=mid, fill=30)
        y += 44
    y += 30
    d.text((90, y), "Bill To: Keyway Property Management", font=mid, fill=30)
    y += 44
    d.text((90, y), f"Job: {f['work']}, {f['site']}", font=mid, fill=30)
    y += 70
    for name, amt in f["items"]:
        d.text((120, y), name, font=mid, fill=30)
        d.text((900, y), _money(amt), font=mid, fill=30)
        y += 44
    y += 20
    due = f["total"]
    if f.get("prior"):
        d.text((600, y), "Previous balance", font=mid, fill=30)
        d.text((900, y), _money(f["prior"]), font=mid, fill=30)
        y += 44
        due = round(f["total"] + f["prior"], 2)
    d.text((600, y), "Amount Due", font=mid, fill=10)
    d.text((900, y), _money(due), font=mid, fill=10)
    y += 60
    d.text((90, y), f"Terms: {f['terms']}", font=mid, fill=40)
    px = img.load()
    heavy = rng.random() < 0.35
    for _ in range(26000 if heavy else 9000):
        px[rng.randrange(1275), rng.randrange(1650)] = rng.randrange(60, 200)
    img = img.rotate(rng.uniform(-2.2, 2.2), expand=False, fillcolor=235, resample=Image.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(1.1 if heavy else 0.6))
    if heavy:                                   # faxed: half resolution, then back up
        img = img.resize((637, 825)).resize((1275, 1650))
    out = io.BytesIO()
    img.convert("RGB").save(out, "PDF", resolution=150)
    return out.getvalue()


def simple_pdf(title: str, paras: list[str]) -> bytes:
    buf, c = _canvas()
    y = H - 60
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, y, title)
    c.setFont("Helvetica", 10)
    y -= 30
    for p in paras:
        words, line = p.split(), ""
        for wd in words:
            if c.stringWidth(line + " " + wd, "Helvetica", 10) > W - 100:
                c.drawString(50, y, line)
                y -= 14
                line = wd
            else:
                line = (line + " " + wd).strip()
        c.drawString(50, y, line)
        y -= 20
        if y < 60:
            c.showPage()
            c.setFont("Helvetica", 10)
            y = H - 60
    c.showPage()
    c.save()
    return buf.getvalue()


def statement_pdf(s: dict) -> bytes:
    buf, c = _canvas()
    cols = [50, 80, 135, 300, 390, 470]

    def header(y: float) -> float:
        c.setFont("Helvetica-Bold", 13)
        c.drawString(50, y, s["title"])
        c.setFont("Helvetica", 9)
        c.drawString(50, y - 14, f"Owner: Oakline Homes LLC    Period: {s['period']}    "
                                 f"Prepared {s['prepared']}")
        y -= 40
        c.setFont("Helvetica-Bold", 9)
        for x, t in zip(cols, ["Ln", "Date", "Vendor", "Ref", "Amount", "Status"]):
            c.drawString(x, y, t)
        c.line(50, y - 3, W - 50, y - 3)
        return y - 16

    y = header(H - 60)
    c.setFont("Helvetica", 9)
    for r in s["rows"]:
        if y < 70:
            c.drawString(50, 40, "continued on next page")
            c.showPage()
            y = header(H - 60)
            c.setFont("Helvetica", 9)
        c.drawString(cols[0], y, str(r["line"]))
        c.drawString(cols[1], y, r["date"])
        c.drawString(cols[2], y, r["vendor"][:28])
        c.drawString(cols[3], y, r["ref"])
        c.drawRightString(cols[4] + 60, y, f"{r['amount']:,.2f}")
        c.drawString(cols[5], y, r["status"])
        if r.get("note"):
            y -= 11
            c.setFont("Helvetica-Oblique", 8)
            c.drawString(cols[2], y, r["note"])
            c.setFont("Helvetica", 9)
        y -= 14
    c.showPage()
    c.save()
    return buf.getvalue()


def funding_confirmation_pdf(wd: dict) -> bytes:
    buf, c = _canvas()
    y = H - 60
    c.setFont("Helvetica-Bold", 13)
    c.drawString(50, y, "First Western Bank - Outgoing Wire Confirmation")
    c.setFont("Helvetica", 10)
    y -= 24
    for k, v in [("Wire date", wd["date"]), ("Confirmation #", wd["wire_no"]),
                 ("Originator", "OAKLINE HOMES LLC"),
                 ("Beneficiary", "KEYWAY PROPERTY MANAGEMENT TRUST ACCT"),
                 ("Amount", f"${wd['total']:,.2f}")]:
        c.drawString(50, y, f"{k}:")
        c.drawString(170, y, v)
        y -= 15
    y -= 15
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "Remittance detail (repair funding)")
    y -= 16
    c.setFont("Helvetica", 9)
    for ln in wd["lines"]:
        c.drawString(60, y, ln["ref"])
        c.drawString(160, y, ln["vendor"][:34])
        c.drawRightString(W - 60, y, f"{ln['amount']:,.2f}")
        y -= 13
        if y < 60:
            c.showPage()
            c.setFont("Helvetica", 9)
            y = H - 60
    c.showPage()
    c.save()
    return buf.getvalue()
