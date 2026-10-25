"""A pack run (report, readings, documents) -> the data Live investigation shows.

    python -I world/pack/export.py world/pack/run_sonnet_2026-10-24 dashboard/public/feed/pack_run.json
"""
import json
import re
import sys
from pathlib import Path

OUT, DEST = Path(sys.argv[1]), Path(sys.argv[2])
READ_COST = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0     # readings reused from an earlier run
rep = json.loads((OUT / "report.json").read_text())
readings = json.loads((OUT / "readings.json").read_text())
docs = json.loads((OUT / "documents.json").read_text())
notes = rep["notes"]
NAMES = {"v-riogrande": "Rio Grande Plumbing", "v-brightcurrent": "Bright Current Electric", "v-summit": "Summit Roofing",
         "v-greenleaf": "GreenLeaf Landscaping", "v-lonestar": "Lone Star HVAC", "v-sparkle": "Sparkle Clean", "v-metro": "Metro Appliance"}

files = []
for name, r in sorted(readings.items()):
    rd = r.get("reading")
    if not rd:
        files.append({"name": name, "kind": "unreadable", "use": "rejected", "detail": "; ".join(r.get("rejected", []))[:160]})
        continue
    if rd["kind"] == "agreement":
        t, c = rep["terms"], rep["term_clauses"]
        files.append({"name": name, "kind": "agreement", "use": "evidence",
                      "detail": f"terms read: pay within {t['vendor_payment_days']} days ({c['vendor_payment_days']}), "
                                f"{t['holdback_pct']:g}% retained ({c['holdback_pct']}), ${t['approval_limit']:,.0f} limit ({c['approval_limit']})"})
        continue
    used = [d for d in docs if d["id"].startswith(f"pack:{name}")]
    replied = [x for x in notes["replies"] if x["name"] == name]
    quar = [q for q in notes["quarantined"] if q.startswith(name)]
    if quar:
        files.append({"name": name, "kind": rd["kind"], "use": "quarantined", "detail": quar[0].split(": ", 1)[1]})
    elif used or replied:
        what = sorted({d["kind"].replace("_", " ") for d in used} | ({"reply to a reminder (chaser reads it)"} if replied else set()))
        ids = sorted({d["data"].get("invoice_id") for d in used if d["data"].get("invoice_id")} | {i for x in replied for i in x["invoice_ids"]})
        files.append({"name": name, "kind": rd["kind"], "use": "evidence", "detail": ", ".join(what) + (f" · {', '.join(ids[:6])}" if ids else "")})
    else:
        files.append({"name": name, "kind": rd["kind"], "use": "on file", "detail": rd["summary"][:160]})

cause = {}
for line in rep["causes"]:
    m = re.match(r"^([A-Z]{2}-\d+): (\w+), cause (\w+)", line)
    if m:
        cause[m[1]] = m[3]
alerts = rep["open_alerts"]
ledger = []
for ln in rep["ledger"]:
    a = next((x["kind"].replace("_", " ") for x in alerts if ln["invoice_id"] in x["headline"]), None)
    ledger.append({"invoice_id": ln["invoice_id"], "vendor": NAMES.get(ln["vendor_id"], ln["vendor_id"]), "amount": ln["amount"],
                   "funded": ln["funded_amount"], "paid": ln["paid_amount"], "due": ln["due_date"], "status": ln["status"],
                   "cause": cause.get(ln["invoice_id"]) if ln["status"] not in ("paid",) else None,
                   "alert": "duplicate billing" if ln.get("duplicate_of") else a})
first = next((c for c in rep["cases"] if c), "")
m = re.search(r"holding \$([\d,]+\.\d\d)", first)
start = float(m[1].replace(",", "")) if m else (rep["days"][0]["unexplained"] if rep["days"] else 0)
last_hold = next((h for d in reversed(rep["days"]) for h in d["holds"]), None) or (first.split(". ")[0] if first else None)
named = lambda t: re.sub(r"\((v-[a-z]+)\)", lambda x: f"({NAMES.get(x[1], x[1])})", t)  # noqa: E731
days = [{**d, "alerts": [named(a) for a in d["alerts"]]} for d in rep["days"]]
out = {"pack": "messy_pack_v1 · 20 PDFs", "model": rep["model"].replace("claude-", "").replace("-", " ").title(),
       "cost_usd": round(rep["cost_usd"] + READ_COST, 2), "calls": rep["model_calls"], "files": files, "ledger": ledger, "writes": rep["event_log_writes"],
       "unexplained_start": start, "unexplained_end": rep["days"][-1]["unexplained"] if rep["days"] else start,
       "alerts": [{**x, "headline": named(x["headline"])} for x in alerts], "reminders": len(rep["reminders"]),
       "hold": last_hold, "quarantined": notes["quarantined"], "flags": notes["flags"], "days": days, "terms": rep["terms"]}
DEST.write_text(json.dumps(out, indent=1) + "\n")
print(f"wrote {DEST}: {len(files)} files, {len(ledger)} invoices, {len(days)} days")
