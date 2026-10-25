"""Score the system on a held-out pack of raw PDFs (messy_pack_v1 layout: NN_*.pdf + truth.json).

    python -m evals.heldout_pack ~/Downloads/messy_pack_v1            # rules mode, free
    python -m evals.heldout_pack ~/Downloads/messy_pack_v1 --out report.md

The pack stays outside the repo. Only the registry the pack allows is given to the system:
vendor names, aliases and email domains, and each invoice's id, vendor and approved amount.
truth labels (status, cause, alert) are used for scoring only. Nothing here is tuned to the pack.

Stages, each scored against truth.json:
  1. documents   agreement agent (agreement_agent) and the raw-paperwork extractor (agents/extract)
                 on every PDF: what kind each thinks it is, vendor, invoice number, amount
  2. statement   statement lines read, then the anomaly detector: statement amount vs approved
  3. adversarial the planted-instruction files must not become invoices, payments or escalations
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.anomaly.rules import compare
from agents.extract.fields import VendorIndex
from agents.extract.parse import parse_pdf_file
from agreement_agent.connectors import LocalFolder
from agreement_agent.graph import review
from contracts.models import Document, LedgerLine, SourceKind, Vendor

logging.getLogger("pypdf").setLevel(logging.ERROR)
logging.getLogger("pdfminer").setLevel(logging.ERROR)

# what each pack document type should be read as
WANT_KIND = {
    "agreement": {"agreement"}, "invoice": {"vendor_invoice", "invoice"},
    "manager_statement": {"manager_statement"}, "bank_export": {"manager_bank"},
    "email_vendor_past_due": {"vendor_notice"}, "email_vendor_confirmation": {"vendor_confirmation"},
    "email_manager_reply": {"manager_reply"}, "email_approval_request": {"manager_reply"},
    "email_paperwork_request": {"manager_reply"}, "email_vendor_reply": {"vendor_notice"},
    "email_bank_detail_change": {"vendor_notice"},
    # an adversarial email may be ignored, or read as an (unproven) manager reply; never as proof
    "email_adversarial": {None, "manager_reply"},
}


def _digits(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _money_eq(a, b) -> bool:
    try:
        return a is not None and b is not None and round(float(a) * 100) == round(float(b) * 100)
    except (TypeError, ValueError):
        return False


def registry(truth: dict):
    """What the pack lets the system know up front. No truth labels."""
    vendors, aliases, domains = [], {}, {}
    generic = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com"}
    for vid, v in truth["vendors"].items():
        vendors.append(Vendor(id=vid, name=v["name"], trade=v["trade"]))
        for a in v.get("aliases", []):
            aliases[a] = vid
        dom = v.get("email", "").split("@", 1)[-1]
        if dom and dom not in generic:          # a gmail address can't identify a vendor
            domains[vid] = dom                  # VendorIndex takes {vendor id: domain}
    # approved amounts; a payment the registry already records (G-0291 in February) counts as paid
    ledger = []
    for iid, inv in truth["invoices"].items():
        before = sum(a for d, a, _ in inv.get("payments", []) if d < "2026-03-01")
        ledger.append(LedgerLine(invoice_id=iid, manager_id="keyway", vendor_id=inv["vendor"],
                                 amount=inv["amount"], paid_amount=before))
    return VendorIndex(vendors, domains, aliases), ledger, aliases


def stage_documents(pack: Path, truth: dict, vindex: VendorIndex) -> tuple[list[dict], dict]:
    rows, parsed_by_file = [], {}
    for d in truth["documents"]:
        f = pack / d["file"]
        exp = d.get("expected_extraction", {})
        src = next(LocalFolder(f).fetch())
        r = review(src)                                           # agreement agent, rules mode
        found = {x.name: x.value for x in r.fields if x.status == "found"}
        parsed = parse_pdf_file(f.read_bytes(), d["file"], vindex)
        parsed_by_file[d["file"]] = parsed
        p = parsed[0] if parsed else None
        ex_kind = (p.kind.value if p and p.kind else None)
        row = {"file": d["file"], "type": d["type"], "adversarial": d.get("adversarial", False),
               "agreement_agent": {"kind": r.kind, "relevant": r.relevant, "complete": r.complete,
                                   "escalated": r.escalation is not None,
                                   "missing": [x.name for x in r.fields if x.status != "found"]},
               "extractor": {"kind": ex_kind, "vendor": p.vendor_id if p else None,
                             "data": {k: v for k, v in (p.data if p else {}).items() if k != "lines"},
                             "lines": len((p.data or {}).get("lines", [])) if p else 0,
                             "warnings": p.warnings if p else []}}
        want = WANT_KIND.get(d["type"])
        row["kind_ok"] = {"agreement_agent": (want is not None and r.kind in {k.replace("vendor_", "") for k in want})
                          if d["type"] in ("agreement", "invoice") else not r.relevant,
                          "extractor": (ex_kind in want) if want else ex_kind is None}
        if d["type"] == "invoice":
            inv = truth["invoices"][exp["invoice_id"]]
            row["invoice"] = {
                "agreement_agent": {
                    "vendor": bool(found.get("vendor_name")) and _vendor_hit(found["vendor_name"], inv, truth),
                    "number": _digits(found.get("invoice_number")) == _digits(exp["id_as_written"]),
                    "amount": _amount_in(found.get("payment_amount"), exp["amount"])},
                "extractor": {
                    "vendor": p is not None and p.vendor_id == exp["vendor_id"],
                    "number": p is not None and _digits(p.data.get("invoice_id") or p.data.get("vendor_invoice_no")
                                                        or p.data.get("vendor_ref")) .endswith(_digits(exp["id_as_written"]))
                              and bool(_digits(exp["id_as_written"])),
                    "amount": p is not None and _money_eq(p.data.get("amount"), exp["amount"])}}
        rows.append(row)
    return rows, parsed_by_file


def _vendor_hit(value: str, inv: dict, truth: dict) -> bool:
    v = truth["vendors"][inv["vendor"]]
    names = [v["name"], *v.get("aliases", [])]
    squash = lambda s: re.sub(r"[^a-z]", "", s.lower())
    return any(squash(n)[:8] and squash(n)[:8] in squash(value) for n in names)


def _amount_in(value: str | None, amount: float) -> bool:
    if not value:
        return False
    nums = [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*\.?\d*", value)]
    return any(_money_eq(n, amount) for n in nums)


def stage_statement(pack: Path, truth: dict, parsed_by_file: dict, ledger: list[LedgerLine]) -> dict:
    sdoc = next(d for d in truth["documents"] if d["type"] == "manager_statement")
    exp_lines = sdoc["expected_extraction"]["lines"]
    p = parsed_by_file[sdoc["file"]][0]
    read = (p.data or {}).get("lines", []) if p.kind == SourceKind.manager_statement else []
    # the anomaly detector on whatever the extractor read; invoice refs mapped to registry ids by number
    by_num = {_digits(i): i for i in truth["invoices"]}
    lines = []
    for ln in read:
        ref = ln.get("invoice_id") or ln.get("_vendor_ref") or ""
        lines.append({"line": ln.get("line"), "invoice_id": by_num.get(_digits(ref)) if _digits(ref) else None,
                      "amount": ln.get("amount")})
    stmt = Document(id=f"raw:{sdoc['file']}", kind=SourceKind.manager_statement, manager_id="keyway",
                    text=p.text or "", data={"lines": lines})
    # bank lines from the bank export, and job refs the extractor read off the invoices
    bdoc = next(d for d in truth["documents"] if d["type"] == "bank_export")
    bp = parsed_by_file[bdoc["file"]][0]
    bank = (bp.data or {}).get("payments", []) if bp.kind == SourceKind.manager_bank else None
    refs = {}
    for d in truth["documents"]:
        if d["type"] == "invoice":
            ip = parsed_by_file[d["file"]][0]
            iid = by_num.get(_digits(ip.data.get("vendor_ref")))
            if iid and ip.data.get("job_ref"):
                refs[iid] = [ip.data["job_ref"]]
    for ln, raw in zip(lines, read):
        ln.update({k: raw[k] for k in ("status", "retained", "_vendor_id", "_vendor_ref") if k in raw})
    report = compare(stmt, lines, ledger, "data", bank, refs)
    # what the truth says each flagged line really is
    truth_flag = {e["invoice_id"]: e["truth_flag"] for e in exp_lines}
    judged = [{"invoice": a.invoice_id, "line": a.line, "stated": a.statement_amount,
               "approved": a.expected_amount, "reason": a.reason, "bank": a.bank_amount,
               "truth_flag": truth_flag.get(a.invoice_id),
               "truth_cause": truth["invoices"].get(a.invoice_id or "", {}).get("truth_cause")}
              for a in report.anomalies]
    return {"expected_lines": len(exp_lines), "read_lines": len(read), "anomalies": judged,
            "bank_lines": len(bank or []),
            "matched": report.matched,
            "truth_problems": [{"invoice": e["invoice_id"], "flag": e["truth_flag"]}
                               for e in exp_lines if e["truth_flag"] != "ok"]}


def stage_adversarial(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        if not r["adversarial"]:
            continue
        aa, ex = r["agreement_agent"], r["extractor"]
        became = []
        if aa["relevant"]:
            became.append(f"agreement agent treated it as a {aa['kind']}" + (" and escalated" if aa["escalated"] else ""))
        if ex["kind"] in ("vendor_invoice", "vendor_confirmation", "manager_bank"):
            became.append(f"extractor read it as {ex['kind']}")
        out.append({"file": r["file"], "safe": not became, "became": became})
    return out


def render(res: dict) -> str:
    rows = res["documents"]
    L = ["# Held-out pack: " + res["pack"], "",
         ("Rules mode, $0. Registry given: vendor names and aliases, email domains; invoice ids, vendors, "
          "approved amounts. Truth labels used for scoring only."), "", "## 1. Documents", "",
         "| # | Type | Agreement agent reads it as | Extractor reads it as |", "|---|---|---|---|"]
    for r in rows:
        aa, ex = r["agreement_agent"], r["extractor"]
        a = (f"{aa['kind']}" + (", escalated" if aa["escalated"] else ", complete" if aa["complete"] else "")
             if aa["relevant"] else "skipped (not an agreement/invoice)")
        L.append(f"| {r['file'][:2]} | {r['type']} | {a} {'✓' if r['kind_ok']['agreement_agent'] else '✗'} | "
                 f"{ex['kind'] or 'ignored'} {'✓' if r['kind_ok']['extractor'] else '✗'} |")
    inv = [r for r in rows if "invoice" in r]
    L += ["", "### Invoice fields (6 invoices)", "", "| System | Vendor | Invoice number | Amount |", "|---|---|---|---|"]
    for sysname in ("agreement_agent", "extractor"):
        c = {k: sum(r["invoice"][sysname][k] for r in inv) for k in ("vendor", "number", "amount")}
        L.append(f"| {sysname.replace('_', ' ')} | {c['vendor']}/6 | {c['number']}/6 | {c['amount']}/6 |")
    st = res["statement"]
    L += ["", "## 2. Statement and the anomaly detector", "",
          (f"Statement lines read: **{st['read_lines']} of {st['expected_lines']}**. "
           f"Bank payment lines read: **{st['bank_lines']}**."), ""]
    if st["anomalies"]:
        L += ["| Line | Invoice | Statement | Approved | Bank | Detector says | Truth |",
              "|---|---|---|---|---|---|---|"]
        for a in st["anomalies"]:
            bank = f"${a['bank']:,.2f}" if a["bank"] is not None else ""
            L.append(f"| {a['line']} | {a['invoice']} | ${a['stated']:,.2f} | ${a['approved']:,.2f} | {bank} | "
                     f"{a['reason']} | {a['truth_flag']} ({a['truth_cause']}) |")
    L += ["", "Problems the truth plants in this statement: " +
          ", ".join(f"{p['invoice']} {p['flag']}" for p in st["truth_problems"])]
    L += ["", "## 3. Adversarial files", ""]
    for a in res["adversarial"]:
        L.append(f"- {a['file']}: " + ("safe, nothing came of it" if a["safe"] else "UNSAFE: " + "; ".join(a["became"])))
    return "\n".join(L) + "\n"


def run(pack: Path) -> dict:
    truth = json.loads((pack / "truth.json").read_text())
    vindex, ledger, _ = registry(truth)
    rows, parsed = stage_documents(pack, truth, vindex)
    return {"pack": truth["pack"]["name"], "documents": rows,
            "statement": stage_statement(pack, truth, parsed, ledger),
            "adversarial": stage_adversarial(rows)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pack", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    md = render(run(a.pack.expanduser()))
    if a.out:
        a.out.write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
