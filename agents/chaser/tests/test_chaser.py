"""Task 5: chaser agent. Approved templates only; a "paid" reply counts only with proof."""

from datetime import UTC, datetime

from agents.chaser import ChaserAgent
from agents.chaser.agent import ReplyReading
from agents.testing import ScriptedLLM, invoice
from contracts.models import Alert, AlertDraft, AlertKind, ChaseMessage, Verdict, VerdictStatus
from rails.guards import APPROVED_TEMPLATES, policy_check


def _alert(inv="INV-KW-0301", amount=1240.0):
    draft = AlertDraft(id=f"al-{inv}", kind=AlertKind.unexplained_unpaid, manager_id="keyway",
                       invoice_ids=[inv], amount=amount,
                       headline=f"{inv} (v-riogrande): ${amount:,.2f} unpaid", claims=[])
    return Alert(draft=draft, verdict=Verdict(alert_id=draft.id, status=VerdictStatus.approved,
                                              reasons=[]))


def _sent(level):
    return ChaseMessage(id=f"c-{level}", manager_id="keyway", invoice_ids=["INV-KW-0301"],
                        template_id=f"tpl-{level}", level=level, body="...",
                        sent_at=datetime(2026, 4, 1, tzinfo=UTC))


def test_levels_escalate_polite_firm_escalation():
    agent = ChaserAgent(None)
    levels = []
    prior: list[ChaseMessage] = []
    for _ in range(4):
        m = agent.draft_chase("keyway", [_alert()], prior)
        levels.append(m.level)
        prior.append(_sent(m.level))
    assert levels == ["polite", "firm", "escalation", "escalation"]


def test_body_is_the_approved_template_and_passes_policy():
    names = {"keyway": "Keyway Property Management", "v-riogrande": "Rio Grande Plumbing"}.get
    m = ChaserAgent(None, names).draft_chase("keyway", [_alert()], [])
    assert m.template_id in APPROVED_TEMPLATES
    assert "Keyway Property Management" in m.body and "Rio Grande Plumbing" in m.body
    assert "$1,240.00" in m.body and "INV-KW-0301" in m.body
    assert policy_check("send_chase", "chaser", {"template_id": m.template_id,
                                                 "prior_levels": []}) == (True, "ok")


def test_paid_reply_without_proof_is_not_proof():
    _, docs = invoice("INV-KW-0301", "keyway")
    r = ChaserAgent(None).read_reply("We paid the plumber.", "keyway", docs)
    assert r.claims_paid and r.proof == [] and "INV-KW-0301" in r.invoice_ids


def test_not_paid_wording_is_not_a_payment_claim():
    _, docs = invoice("INV-KW-0201", "keyway")
    r = ChaserAgent(None).read_reply("Not paid yet, we are waiting on their W-9.", "keyway", docs)
    assert not r.claims_paid


def test_proof_found_when_records_show_payment():
    _, docs = invoice("INV-KW-0501", "keyway")
    r = ChaserAgent(None).read_reply("AmperPro was paid on 4/8.", "keyway", docs)
    assert {c.source_id for c in r.proof} >= {"doc-bank-kw-2026-04-s5", "doc-conf-kw-0501"}


def test_llm_cannot_offer_the_reply_itself_as_proof():
    _, docs = invoice("INV-KW-0901", "keyway")
    bad = ReplyReading(claims_paid=True, invoice_ids=["INV-KW-0901"],
                       proof_source_ids=["doc-reply-kw-0901"], summary="Manager says paid.")
    good = bad.model_copy(update={"proof_source_ids": []})
    llm = ScriptedLLM(bad, good)
    r = ChaserAgent(llm).read_reply("this one was paid last week", "keyway", docs)
    assert r.claims_paid and r.proof == [] and "not a bank record" in llm.prompts[1]


def test_llm_reads_fuzzy_reply():
    _, docs = invoice("INV-KW-0901", "keyway")
    llm = ScriptedLLM(ReplyReading(claims_paid=False, invoice_ids=["INV-KW-0901"],
                                   proof_source_ids=[], summary="Payment is scheduled."))
    r = ChaserAgent(llm).read_reply("cleared for next week's run", "keyway", docs)
    assert not r.claims_paid                 # rules alone would read "cleared" as paid
