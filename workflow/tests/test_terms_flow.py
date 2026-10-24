"""Human-confirm step for agreement terms (LangGraph interrupt / resume)."""

from datetime import UTC, datetime

import pytest

from agents import RealAgents
from contracts.models import Document, SourceKind
from rails.events.ingest import document_event
from rails.facts import current_terms
from workflow.memrails import MemoryRails
from world import generate

from .conftest import make_workflow

NEW = ("7.2 Vendor Payment. Manager shall pay each vendor invoice within fifteen (15) days of "
       "receipt of a complete invoice, using funds provided by Owner.\n"
       "7.4 Holdback. Manager may withhold up to ten percent (10%) of a repair invoice until the "
       "work passes inspection.\n"
       "8.1 Approval Limit. Any single repair exceeding $5,000 requires Owner's written approval "
       "and funding before work is ordered.")


@pytest.fixture
def wf():
    rails = MemoryRails()
    wf = make_workflow(rails, agents=RealAgents(None))
    wf.replay(generate(scenarios=(3,), n_clean=10).events())
    return wf


def _amendment():
    d = Document(id="doc-agreement-keyway-amend", kind=SourceKind.agreement, manager_id="keyway",
                 dated=datetime(2026, 4, 26, tzinfo=UTC).date(), text=NEW)
    return document_event(d)


def _days(wf):
    return current_terms(wf.deps.rails.events("keyway"))["keyway"]["vendor_payment_days"]


def test_same_terms_need_no_confirmation(wf):
    # the world's agreements match the already-confirmed terms: no pause during replay
    assert wf.waiting() == []


def test_new_terms_pause_for_controller_then_recheck(wf):
    [r] = wf.replay([_amendment()])
    assert r.waiting["proposed"]["vendor_payment_days"] == 15 and r.waiting["clauses"]
    assert wf.waiting() == [r.event_id] and _days(wf) == 10       # not binding yet
    done = wf.resume(r.event_id, {"approve": True, "by": "controller.dana"})
    assert _days(wf) == 15 and done.waiting is None
    assert any("rechecking" in t for t in done.trace) and wf.waiting() == []


def test_agent_cannot_confirm_terms(wf):
    [r] = wf.replay([_amendment()])
    done = wf.resume(r.event_id, {"approve": True, "by": "agreement_agent"})
    assert _days(wf) == 10 and any("not confirmed" in t for t in done.trace)


def test_controller_can_decline_or_edit(wf):
    [r] = wf.replay([_amendment()])
    wf.resume(r.event_id, {"approve": True, "by": "controller.dana",
                           "edits": {"vendor_payment_days": 12}})
    assert _days(wf) == 12


def test_amendment_removal_conditions_fees_and_provenance(wf):
    from workflow.terms import terms_for
    text = ('4.1 Pay all vendors within fifteen (15) days of invoice receipt.\n'
            '6.2 Owner approval above $5,000.\n'
            '9.1 A monthly administration fee of $25 is allowed.')
    d = Document(id='amend-remove', kind=SourceKind.agreement, manager_id='keyway',
                 dated=datetime(2026,4,26,tzinfo=UTC).date(), text=text)
    [r] = wf.replay([document_event(d)])
    assert r.waiting['proposed']['holdback_pct'] is None
    wf.resume(r.event_id, {'approve':True,'by':'controller.dana'})
    t = terms_for(wf.deps.rails, wf.deps.agents, 'keyway', datetime(2026,4,27).date())
    assert t.holdback_pct is None and t.holdback_condition is None
    assert t.allowed_charges
    assert t.vendor_payment_days.citation.source_id == 'amend-remove'
    assert t.vendor_payment_days.citation.locator == '4.1'
    assert t.vendor_payment_days.citation.quote in text


def test_older_pending_proposal_cannot_revert_latest(wf):
    first = _amendment()
    [older] = wf.replay([first])
    newer = first.model_copy(update={'id': 'newer-event', 'payload': {
        **first.payload, 'id':'newer-doc', 'text': NEW.replace('fifteen (15)', 'twenty (20)')}})
    [latest] = wf.replay([newer])
    wf.resume(latest.event_id, {'approve':True,'by':'controller.dana'})
    assert _days(wf) == 20
    result = wf.resume(older.event_id, {'approve':True,'by':'controller.dana'})
    assert _days(wf) == 20
    assert any('stale' in x for x in result.trace)


def test_condition_only_amendment_requires_confirmation_and_persists(wf):
    from workflow.terms import terms_for
    original = next(d for d in wf.deps.rails.documents('keyway') if d.kind == SourceKind.agreement)
    amended = original.model_copy(update={'id':'condition-amendment',
        'text': original.text.replace('until the work passes inspection', 'until the owner signs off')})
    [result] = wf.replay([document_event(amended)])
    assert result.waiting is not None
    wf.resume(result.event_id, {'approve':True,'by':'controller.dana'})
    confirmed = terms_for(wf.deps.rails, wf.deps.agents, 'keyway')
    assert confirmed.holdback_condition.value == 'until the owner signs off'
    assert confirmed.holdback_condition.citation.source_id == 'condition-amendment'
