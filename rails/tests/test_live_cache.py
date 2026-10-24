"""Reusing answers to identical questions: fewer model calls, never a stale or degraded answer."""

from datetime import date

import pytest

from agents import RealAgents
from contracts.models import Verdict, VerdictStatus
from rails import RealRails
from rails.live.cache import AnswerCache, CachedAgents, CachedVerifier
from rails.live.system import LiveSystem
from world import generate
from world.generator import ALL_SCENARIOS


class ModelAgents(RealAgents):
    """Rules under the hood, but it presents as model-backed and counts its 'model' calls."""

    llm = True

    def __init__(self):
        super().__init__(None)
        self.calls = 0

    def classify_cause(self, line, trail, terms):
        self.calls += 1
        return super().classify_cause(line, trail, terms)

    def build_trail(self, line, docs):
        self.calls += 1
        t = super().build_trail(line, docs)
        # like a real model, the wording differs on every call
        return t.model_copy(update={"reasoning": f"{t.reasoning} [call {self.calls}]"})


class ModelVerifier:
    def __init__(self, fail_closed=False):
        self.calls, self.fail_closed = 0, fail_closed

    def verify(self, draft, evidence):
        self.calls += 1
        if self.fail_closed:
            return Verdict(alert_id=draft.id, status=VerdictStatus.blocked,
                           reasons=["verifier unavailable: Error", "failing closed: held"],
                           meta={"model": "m"})
        return Verdict(alert_id=draft.id, status=VerdictStatus.approved, reasons=["ok"],
                       meta={"model": "m"})


@pytest.fixture
def rails(schema):
    r = RealRails(schema=schema)
    r.load(generate(13, ALL_SCENARIOS, 30).events())
    return r


def test_the_same_question_is_answered_once(rails):
    cache = AnswerCache(rails.store.conn)
    inner = ModelAgents()
    agents = CachedAgents(inner, cache)
    line = next(l for l in rails.ledger("keyway") if l.invoice_id == "INV-KW-0301")
    docs = rails.documents("keyway", "INV-KW-0301")
    trail = inner.build_trail(line, docs)
    from workflow.terms import terms_for
    terms = terms_for(rails, inner, "keyway")
    base = inner.calls                       # the trail above was built directly, not through the cache
    a = agents.classify_cause(line, trail, terms)
    b = agents.classify_cause(line, trail, terms)
    assert inner.calls == base + 1 and a == b
    assert cache.counts() == {"answers_reused": 1, "answers_computed": 1}
    # a changed fact is a different question
    moved = line.model_copy(update={"due_date": date(2026, 3, 20)})
    agents.classify_cause(moved, trail, terms)
    assert inner.calls == base + 2


def test_it_survives_a_restart(rails):
    inner, line = ModelAgents(), next(l for l in rails.ledger("keyway") if l.invoice_id == "INV-KW-0301")
    from workflow.terms import terms_for
    terms, trail = terms_for(rails, inner, "keyway"), inner.build_trail(line, rails.documents("keyway", "INV-KW-0301"))
    CachedAgents(inner, AnswerCache(rails.store.conn)).classify_cause(line, trail, terms)
    again = ModelAgents()
    CachedAgents(again, AnswerCache(rails.store.conn)).classify_cause(line, trail, terms)
    assert again.calls == 0


def test_a_failed_closed_verdict_is_never_remembered(rails):
    cache = AnswerCache(rails.store.conn)
    draft_src = LiveSystem(rails, agents=ModelAgents(), verifier=ModelVerifier(), reuse_answers=False)
    draft_src.warm()
    draft = next(a.draft for a in draft_src._Book.load(rails).alerts.values())
    flaky = ModelVerifier(fail_closed=True)
    v = CachedVerifier(flaky, cache)
    v.verify(draft, []); v.verify(draft, [])
    assert flaky.calls == 2                                  # not remembered: asks again
    good = ModelVerifier()
    v = CachedVerifier(good, cache)
    v.verify(draft, []); v.verify(draft, [])
    assert good.calls == 1


def test_days_past_due_wording_does_not_split_the_same_alert(rails):
    cache, good = AnswerCache(rails.store.conn), ModelVerifier()
    s = LiveSystem(rails, agents=ModelAgents(), verifier=ModelVerifier(), reuse_answers=False)
    s.warm()
    d = next(a.draft for a in s._Book.load(rails).alerts.values() if "days past due" in a.draft.headline)
    later = d.model_copy(update={"headline": d.headline.replace(
        d.headline.split("days past due")[0].split()[-1], "99")})
    v = CachedVerifier(good, cache)
    v.verify(d, []); v.verify(later, [])
    assert good.calls == 1


def _calls_for_the_demo(schema_name, reuse):
    r = RealRails(schema=schema_name)
    r.load(generate(13, ALL_SCENARIOS, 30).events())
    agents, verifier = ModelAgents(), ModelVerifier()
    live = LiveSystem(r, agents=agents, verifier=verifier, reuse_answers=reuse)
    live.warm()
    warm = agents.calls + verifier.calls
    live.inject_preset("invoice_received_correction")
    for _ in range(3):
        live.advance_clock()
    return warm, agents.calls + verifier.calls - warm


def test_time_jumps_ask_the_model_far_less_with_reuse():
    import uuid
    from rails.db import connect
    names = [f"t_{uuid.uuid4().hex[:10]}" for _ in range(2)]
    try:
        _, without = _calls_for_the_demo(names[0], reuse=False)
        _, with_reuse = _calls_for_the_demo(names[1], reuse=True)
    finally:
        c = connect()
        for n in names:
            c.execute(f'DROP SCHEMA IF EXISTS "{n}" CASCADE')
    print(f"\nmodel calls after warm-up (fact + 3 time jumps): without reuse {without}, with reuse {with_reuse}")
    assert with_reuse < without
    assert with_reuse <= without * 0.6


def test_the_trail_is_remembered_so_the_cause_question_repeats_exactly(rails):
    """A trail whose wording varies per call must not make every cause question new."""
    cache = AnswerCache(rails.store.conn)
    inner = ModelAgents()
    agents = CachedAgents(inner, cache)
    line = next(l for l in rails.ledger("keyway") if l.invoice_id == "INV-KW-0301")
    docs = rails.documents("keyway", "INV-KW-0301")
    from workflow.terms import terms_for
    terms = terms_for(rails, inner, "keyway")
    first = agents.build_trail(line, docs)
    again = agents.build_trail(line, docs)
    assert first == again and "[call" in first.reasoning
    agents.classify_cause(line, first, terms)
    agents.classify_cause(line, again, terms)
    assert inner.calls == 2                          # one trail + one cause, asked once each
