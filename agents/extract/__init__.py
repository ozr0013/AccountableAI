"""Read the raw paperwork (emails, PDFs, scans, bank CSVs) into Documents the rails understand.

    from agents.extract import ingest_folder
    res = ingest_folder("out/raw", vendors)       # vendors: the vendor master (world.vendors)
    res.documents     # clean-schema Documents, ids like raw:inbox/20260304_1948_c56e58.eml
    res.records       # every parsed record with method, confidence, warnings
    res.needs_human   # what the system refuses to guess about

Rules first (regex, column positions, fuzzy vendor match, cross-document linking). Scans with
no text layer go to tesseract when installed, else Claude vision when ANTHROPIC_API_KEY is set,
else they are flagged for a person. Owner: Omar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from contracts.models import Document, SourceKind

from . import llm
from .fields import VendorIndex
from .link import link
from .parse import Parsed, parse_bank_csv, parse_email, parse_pdf_file

MANAGER_ID = "keyway"


@dataclass
class IngestResult:
    documents: list[Document] = field(default_factory=list)
    records: list[Parsed] = field(default_factory=list)

    @property
    def needs_human(self) -> list[Parsed]:
        return [p for p in self.records if p.needs_human]

    @property
    def ignored(self) -> list[Parsed]:
        return [p for p in self.records if p.kind is None and not p.needs_human]


def _vendor_domains() -> dict[str, str]:
    """Vendor master email domains (what AP keeps on file for each vendor)."""
    try:
        from world.mess.noise import VENDOR_DOMAINS
        return dict(VENDOR_DOMAINS)
    except ImportError:
        return {}


def parse_file(path: Path, rel: str, vendors: VendorIndex) -> list[Parsed]:
    data = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".eml":
        return parse_email(data, rel, vendors)
    if suffix == ".pdf":
        return parse_pdf_file(data, rel, vendors)
    if suffix == ".csv":
        return [parse_bank_csv(data.decode("utf-8", "replace"), rel, vendors)]
    return [Parsed(rel, None, warnings=[f"ignored: {suffix} file"])]


def _clean(data: dict) -> dict:
    out = {}
    for k, v in data.items():
        if k.startswith("_") or v is None:
            continue
        if isinstance(v, list):
            v = [{kk: vv for kk, vv in r.items() if not kk.startswith("_")} if isinstance(r, dict)
                 else r for r in v]
        out[k] = v
    return out


def to_document(p: Parsed) -> Document:
    return Document(id=p.doc_id, kind=p.kind, manager_id=MANAGER_ID, vendor_id=p.vendor_id,
                    dated=p.dated, text=p.text, data=_clean(p.data))


def reread(records: list[Parsed]) -> None:
    """Before linking: invoices whose amount rules can't trust (an old balance mixed in, an OCR
    read, or a total that disagrees with Oakline's wire) get a second read by the model."""
    funded = {p.data.get("invoice_id"): p.data.get("amount") for p in records
              if p.kind == SourceKind.funding}
    for p in records:
        if p.kind != SourceKind.vendor_invoice or p.method == "none":
            continue
        f_amt = funded.get(p.data.get("invoice_id"))
        mismatch = f_amt is not None and p.data.get("amount") is not None and \
            abs(f_amt - p.data["amount"]) > 0.005
        if not (p.meta.get("ambiguous_amount") or mismatch or p.method.startswith("ocr")):
            continue
        if not (p.meta.get("ambiguous_amount") or mismatch):
            continue          # OCR read cleanly and agrees with the wire: nothing to ask
        agree = [c for c in p.meta.get("amount_candidates", [])
                 if f_amt is not None and abs(c - f_amt) < 0.005]
        if agree and mismatch:
            p.flag(f"printed total {p.data['amount']:,.2f} includes an older balance; this "
                   f"invoice's own charges {agree[0]:,.2f} match Oakline's wire", conf=0.9)
            p.data["amount"] = agree[0]
            p.meta.pop("ambiguous_amount", None)
            continue
        got = llm.reread_invoice(p.text, p.meta.get("_pdf"))
        if got is None or got.current_charges is None:
            continue
        before = p.data.get("amount")
        p.data["amount"] = round(got.current_charges, 2)
        p.meta.pop("ambiguous_amount", None)
        p.flag(f"model re-read the invoice: this invoice's charges are {got.current_charges:,.2f}"
               + (f" (rules read {before:,.2f})" if before is not None else ""), conf=0.85)
        p.method += "+llm"


def ingest_folder(root: str | Path, vendors, domains: dict[str, str] | None = None) -> IngestResult:
    root = Path(root)
    index = VendorIndex(vendors, domains if domains is not None else _vendor_domains())
    records: list[Parsed] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "manifest.json" and not path.name.startswith("."):
            records += parse_file(path, path.relative_to(root).as_posix(), index)
    reread(records)
    link(records)
    res = IngestResult(records=records)
    for p in records:
        if p.kind is None or (p.kind == SourceKind.vendor_invoice and p.method == "none"):
            continue
        if p.dated is None:
            p.flag("no date anywhere on the document", human=True)
            continue
        res.documents.append(to_document(p))
    return res
