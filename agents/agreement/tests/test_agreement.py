"""Task 2: agreement agent. Terms with clause citations; verbatim quotes; never self-confirmed."""

import pytest

from agents.agreement import AgreementAgent
from agents.agreement.agent import AgreementAnswer
from agents.testing import ScriptedLLM, world_rails
from contracts.models import Document, SourceKind


@pytest.fixture
def doc():
    return next(d for d in world_rails().documents("keyway") if d.kind == SourceKind.agreement)


def test_rules_extract_terms_with_clause_citations(doc):
    t = AgreementAgent(None).extract_terms(doc)
    assert (t.vendor_payment_days.value, t.vendor_payment_days.citation.locator) == (10, "7.2")
    assert (t.holdback_pct.value, t.holdback_pct.citation.locator) == (10, "7.4")
    assert t.holdback_condition.value == "until the work passes inspection"
    assert (t.approval_limit.value, t.approval_limit.citation.locator) == (5000, "8.1")
    assert not t.confirmed_by_human
    for term in (t.vendor_payment_days, t.holdback_pct, t.approval_limit):
        assert term.citation.quote in doc.text                     # verbatim, verifier-safe


def test_other_wording_extracted():
    d = Document(id="doc-agr-x", kind=SourceKind.agreement, manager_id="x", text=(
        "4.1 Payments. Manager shall pay all vendors within 15 days after receiving an invoice.\n"
        "4.3 Retainage. Manager may retain 5% of any repair until the owner signs off.\n"
        "6.2 Limits. Repairs above $2,500 need Owner approval."))
    t = AgreementAgent(None).extract_terms(d)
    assert (t.vendor_payment_days.value, t.holdback_pct.value, t.approval_limit.value) == (15, 5, 2500)


def _ans(**over):
    base = {
        "vendor_payment_days": {"value": 10, "clause": "7.2", "quote": "Manager shall pay each "
                                "vendor invoice within ten (10) days of receipt of a complete "
                                "invoice, using funds provided by Owner."},
        "holdback_pct": {"value": 10, "clause": "7.4", "quote": "Manager may withhold up to ten "
                         "percent (10%) of a repair invoice until the work passes inspection."},
        "approval_limit": {"value": 5000, "clause": "8.1", "quote": "Any single repair exceeding "
                           "$5,000 requires Owner's written approval and funding before work is "
                           "ordered."},
    }
    return AgreementAnswer.model_validate({**base, **over})


def test_llm_terms_accepted_when_quotes_check_out(doc):
    t = AgreementAgent(ScriptedLLM(_ans())).extract_terms(doc)
    assert t.approval_limit.value == 5000 and not t.confirmed_by_human


def test_llm_invented_clause_or_value_rejected(doc):
    bad = _ans(approval_limit={"value": 50000, "clause": "9.9", "quote": "The limit is generous."})
    llm = ScriptedLLM(bad, bad)
    t = AgreementAgent(llm).extract_terms(doc)
    assert t.approval_limit.value == 5000                          # rules fallback
    assert "not in the agreement" in llm.prompts[1] and "does not contain" in llm.prompts[1]
