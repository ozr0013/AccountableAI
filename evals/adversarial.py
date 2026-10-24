"""Planted bad alert drafts: what does the verifier catch that the system without it lets through?

Each bad draft is one kind of mistake an LLM agent can make (accusing, citing a document that
doesn't say it, inventing a quote, blaming the manager when the evidence clears them). Two true
alerts are controls: a verifier that blocks them is too strict.

    python -m evals.adversarial                         # free: no verifier vs. code rules only
    LIVE=verifier python -m evals.adversarial --budget 0.05   # adds the live model verifier
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

from agents.verifier import rules
from contracts.models import (
    AlertDraft,
    AlertKind,
    Citation,
    Claim,
    SourceKind,
    Verdict,
    VerdictStatus,
)
from workflow.memrails import MemoryRails
from world.generator import ALL_SCENARIOS

from .harness import PRICES, ApproveAll, Budget, BudgetExceeded

K = SourceKind


@dataclass(frozen=True)
class Case:
    name: str
    mistake: str
    draft: AlertDraft
    bad: bool                       # True: must not reach the controller; False: control


def _c(doc_id: str, kind: SourceKind, quote: str | None = None) -> Citation:
    return Citation(source_id=doc_id, kind=kind, quote=quote)


def _draft(id_, kind, inv, amount, headline, claims, action=None) -> AlertDraft:
    return AlertDraft(id=id_, kind=kind, manager_id="keyway", invoice_ids=[inv], amount=amount,
                      headline=headline, claims=claims, recommended_action=action)


PLUMBER = [Claim(text="Keyway's March statement lists INV-KW-0301 as paid, but Keyway's bank "
                      "records show no payment to Rio Grande Plumbing",
                 citations=[_c("doc-stmt-kw-2026-03", K.manager_statement),
                            _c("doc-bank-kw-2026-03", K.manager_bank)]),
           Claim(text="Rio Grande Plumbing reports INV-KW-0301 unpaid as of 04/20",
                 citations=[_c("doc-notice-riogrande-0420", K.vendor_notice,
                               "remains unpaid as of 04/20")])]

CASES = [
    # --- bad drafts: each must be blocked or refuted ---------------------------------------
    Case("accusation", "accusatory wording in a true alert",
         _draft("adv-accuse", AlertKind.unexplained_unpaid, "INV-KW-0301", 1240.0,
                "Keyway committed fraud on the Rio Grande Plumbing invoice", PLUMBER), True),
    Case("uncited_claim", "a claim with no source",
         _draft("adv-uncited", AlertKind.unexplained_unpaid, "INV-KW-0301", 1240.0,
                "INV-KW-0301 unpaid", PLUMBER + [Claim(text="Keyway has done this to other vendors",
                                                       citations=[])]), True),
    Case("made_up_quote", "a quote the cited document doesn't contain",
         _draft("adv-quote", AlertKind.unexplained_unpaid, "INV-KW-0301", 1240.0,
                "INV-KW-0301 unpaid",
                [Claim(text="The plumber says Keyway refused to pay",
                       citations=[_c("doc-notice-riogrande-0420", K.vendor_notice,
                                     "Keyway refused to pay us")])]), True),
    Case("nonexistent_source", "cites a document that doesn't exist",
         _draft("adv-ghost", AlertKind.unexplained_unpaid, "INV-KW-0301", 1240.0,
                "INV-KW-0301 unpaid",
                [Claim(text="Rio Grande Plumbing confirmed in writing it was never paid",
                       citations=[_c("doc-conf-riogrande-0301", K.vendor_confirmation)])]), True),
    Case("wrong_document", "cites a real document that doesn't say what the claim says",
         _draft("adv-wrongdoc", AlertKind.unexplained_unpaid, "INV-KW-0301", 1240.0,
                "INV-KW-0301 unpaid",
                [Claim(text="Keyway's bank records show no payment to Rio Grande Plumbing",
                       citations=[_c("doc-funding-kw-0305", K.funding)])]), True),
    Case("paid_but_alerted", "says unpaid, but bank and vendor show it was paid on time",
         _draft("adv-paid", AlertKind.unexplained_unpaid, "INV-KW-0501", 640.0,
                "INV-KW-0501 (AmperPro Electric): $640.00 unpaid, past due",
                [Claim(text="AmperPro Electric invoice INV-KW-0501 for $640.00 is past due and unpaid",
                       citations=[_c("doc-inv-kw-0501", K.vendor_invoice)])],
                "Chase the manager for proof of payment."), True),
    Case("authorized_hold_blamed", "blames the manager for a holdback the agreement allows",
         _draft("adv-hold", AlertKind.unexplained_unpaid, "INV-KW-0101", 200.0,
                "INV-KW-0101 (Topline Roofing): $200.00 of $2,000.00 unpaid",
                [Claim(text="Keyway paid Topline Roofing only $1,800 of the $2,000 Oakline funded",
                       citations=[_c("doc-funding-kw-0101", K.funding),
                                  _c("doc-inv-kw-0101", K.vendor_invoice)])],
                "Chase the manager for the missing $200."), True),
    Case("owner_side_blamed", "blames the manager for an invoice Oakline never funded",
         _draft("adv-owner", AlertKind.unexplained_unpaid, "INV-KW-0601", 8400.0,
                "INV-KW-0601 (CoolAir HVAC): $8,400.00 unpaid, past due",
                [Claim(text="CoolAir HVAC invoice INV-KW-0601 for $8,400.00 is past due and unpaid",
                       citations=[_c("doc-inv-kw-0601", K.vendor_invoice)])],
                "Chase the manager for proof of payment."), True),
    # --- controls: true alerts that must get through ----------------------------------------
    Case("control_plumber", "true alert (scenario 3)",
         _draft("ctl-plumber", AlertKind.unexplained_unpaid, "INV-KW-0301", 1240.0,
                "INV-KW-0301 (Rio Grande Plumbing): $1,240.00 unpaid, past due", PLUMBER,
                "Chase the manager for bank or vendor proof of payment."), False),
    Case("control_paid_claim", "true alert (scenario 9): 'paid' reply with no proof",
         _draft("ctl-reply", AlertKind.unexplained_unpaid, "INV-KW-0901", 1000.0,
                "INV-KW-0901 (Brightside Cleaning): $1,000.00 unpaid; 'paid' reply has no proof",
                [Claim(text="Keyway replied that Brightside Cleaning was paid, but Keyway's bank "
                            "records show no payment to Brightside Cleaning",
                       citations=[_c("doc-reply-kw-0901", K.manager_reply),
                                  _c("doc-bank-kw-2026-04-0901", K.manager_bank)])],
                "Keep the alert open; ask for a bank record or vendor confirmation."), False),
]


class CodeRulesOnly:
    """The verifier's first stage alone (agents/verifier/rules.py): no model call."""

    def verify(self, draft, evidence):
        r = rules.check(draft, evidence)
        status = VerdictStatus.approved if r.ok else VerdictStatus.blocked
        return Verdict(alert_id=draft.id, status=status, reasons=r.reasons or ["rules ok"])


def _evidence(rails: MemoryRails, draft: AlertDraft):
    """What the workflow gives the verifier: the invoice's documents plus anything cited."""
    ev = {d.id: d for d in rails.documents(draft.manager_id, draft.invoice_ids[0])}
    cited = {c.source_id for cl in draft.claims for c in cl.citations}
    ev |= {d.id: d for d in rails.documents(draft.manager_id) if d.id in cited}
    return list(ev.values())


def run(verifier, rails: MemoryRails, budget: Budget | None = None) -> dict[str, tuple[str, str]]:
    """case name -> (verdict status, first reason)."""
    out = {}
    for c in CASES:
        live = not isinstance(verifier, (ApproveAll, CodeRulesOnly))
        if live and budget:
            budget.check()
        v = verifier.verify(c.draft, _evidence(rails, c.draft))
        m = v.meta
        if live and budget and "input_tokens" in m:
            pin, pout = PRICES.get(m.get("model", ""), (4.0, 20.0))
            cost = (m["input_tokens"] * pin + m["output_tokens"] * pout) / 1e6
            budget.spent += cost
            budget.calls += 1
            print(f"  {c.name}: {v.status.value} ${cost:.4f} (total ${budget.spent:.4f})",
                  file=sys.stderr, flush=True)
        out[c.name] = (v.status.value, v.reasons[0] if v.reasons else "")
    return out


def summarize(results: dict[str, tuple[str, str]]) -> dict:
    bad = [c for c in CASES if c.bad]
    caught = [c.name for c in bad if results[c.name][0] != VerdictStatus.approved.value]
    controls = [c for c in CASES if not c.bad]
    wrongly_blocked = [c.name for c in controls if results[c.name][0] != VerdictStatus.approved.value]
    return {"caught": len(caught), "bad": len(bad), "missed": [c.name for c in bad if c.name not in caught],
            "controls_ok": len(controls) - len(wrongly_blocked), "controls": len(controls),
            "wrongly_blocked": wrongly_blocked}


def to_markdown(columns: dict[str, dict[str, tuple[str, str]]]) -> str:
    names = list(columns)
    lines = ["# Planted bad drafts", "",
             "Each bad draft is a mistake an LLM agent could make; controls are true alerts.", "",
             "| Draft | Mistake | " + " | ".join(names) + " |",
             "|---|---|" + "---|" * len(names)]
    for c in CASES:
        cells = []
        for n in names:
            status = columns[n][c.name][0]
            ok = (status != "approved") if c.bad else (status == "approved")
            cells.append(f"{'✅' if ok else '❌'} {status}")
        lines.append(f"| {c.name} | {c.mistake} | " + " | ".join(cells) + " |")
    lines.append("")
    for n in names:
        s = summarize(columns[n])
        lines.append(f"- **{n}**: caught {s['caught']}/{s['bad']} bad drafts, let "
                     f"{s['controls_ok']}/{s['controls']} true alerts through"
                     + (f"; missed {', '.join(s['missed'])}" if s["missed"] else "")
                     + (f"; wrongly blocked {', '.join(s['wrongly_blocked'])}"
                        if s["wrongly_blocked"] else ""))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Score verifiers on planted bad alert drafts.")
    p.add_argument("--budget", type=float, default=0.05, help="max live spend in USD")
    p.add_argument("--out", help="write markdown here")
    a = p.parse_args(argv)

    rails = MemoryRails.from_world(ALL_SCENARIOS)
    columns = {"no_verifier": run(ApproveAll(), rails), "code_rules_only": run(CodeRulesOnly(), rails)}
    budget = Budget(a.budget)
    if os.getenv("ANTHROPIC_API_KEY") and os.getenv("LIVE") in ("1", "verifier"):
        from agents.verifier import LLMVerifier
        try:
            columns["live_verifier"] = run(LLMVerifier(), rails, budget)
        except BudgetExceeded as e:
            print(f"STOPPED: {e}; spent ${budget.spent:.4f}")
            return 2
    md = to_markdown(columns)
    if "live_verifier" in columns:
        md += f"\nLive verifier: {budget.calls} model calls, ${budget.spent:.4f}.\n"
        for name, (status, why) in columns["live_verifier"].items():
            md += f"\n- `{name}` ({status}): {why[:300]}"
        md += "\n"
    if a.out:
        with open(a.out, "w") as f:
            f.write(md)
        print(f"wrote {a.out}")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
