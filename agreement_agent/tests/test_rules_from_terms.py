"""Rules derived from a reviewed agreement: parsed from verified quotes, missing terms block."""
from datetime import UTC, datetime
from pathlib import Path

from agreement_agent.graph import review
from agreement_agent.models import SourceDoc
from agreement_agent.rules_from_terms import _account, _amount, _days, rules_for

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def _review(name):
    p = SAMPLES / name
    return review(SourceDoc(id=p.stem, source="local", filename=p.name, mime="application/pdf",
                            data=p.read_bytes(), received_at=datetime.now(UTC)))


def _by_type(rules):
    return {r["rule_type"]: r for r in rules}


def test_complete_agreement_becomes_four_checkable_rules():
    rules = _by_type(rules_for(_review("rio_grande_services_agreement.pdf")))
    assert set(rules) == {"payee", "pay_within_days", "amount", "pay_to_account"}
    assert rules["pay_within_days"]["params"] == {"days": 30, "basis": "calendar",
                                                  "from": "invoice receipt", "undisputed_only": True}
    assert rules["amount"]["params"]["amount"] == 95.0 and rules["amount"]["params"]["per"] == "hour"
    # Rules mode quotes only the routing clause; the rule keeps exactly what was quoted.
    assert rules["pay_to_account"]["params"] == {"bank": "First Mesa Bank", "routing": "102000021"}
    assert all(r["status"] == "active" and r["source_quote"] for r in rules.values())


def test_missing_terms_become_blocking_holds():
    rules = rules_for(_review("greenedge_landscaping_agreement.pdf"))
    holds = {r["params"]["term"] for r in rules if r["rule_type"] == "hold_payment"}
    assert holds == {"payment_due", "payment_info"}
    assert all(r["status"] == "blocking" for r in rules if r["rule_type"] == "hold_payment")
    assert len({r["rule_id"] for r in rules}) == len(rules)
    assert _by_type(rules)["amount"]["params"] == {"amount": 1250.0, "per": "month",
                                                   "extras_allowed": False}


def test_parsers_read_common_wording():
    assert _days("within ten (10) business days of receipt") == 10
    assert _days("Net 45") == 45 and _days("within thirty days") == 30 and _days("on completion") is None
    assert _amount("a flat fee of $2,400.50") == (2400.5, "fixed")
    assert _account("wire to Desert Bank, routing number 123456789, account no. 99887766") == {
        "bank": "Desert Bank", "routing": "123456789", "account": "99887766"}
