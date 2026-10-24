"""Briefing agent tests. Offline: the demo world replayed through the workflow, a stub client."""

from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from agents import RealAgents
from agents.briefing.briefer import Briefer, Record, _BriefingOut, _ClaimOut, code_briefing
from contracts.fakes import FakeApprovals, FakeVerifier
from workflow import Deps, Workflow
from workflow.memrails import MemoryRails
from workflow.run import scan_events
from world import generate
from world.generator import ALL_SCENARIOS

Q = "What's open with Keyway?"


@pytest.fixture(scope="module")
def rails():
    w = generate(13, ALL_SCENARIOS, 30)
    r = MemoryRails()
    Workflow(Deps(r, RealAgents(None), FakeVerifier(), FakeApprovals())).replay(
        w.events() + scan_events([m.id for m in w.managers], w.as_of))
    return r


class Stub:
    def __init__(self, out=None, stop_reason="end_turn", error=None):
        self.out, self.stop_reason, self.error, self.calls = out, stop_reason, error, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return SimpleNamespace(model=kw["model"], stop_reason=self.stop_reason, parsed_output=self.out,
                               usage=SimpleNamespace(input_tokens=7000, output_tokens=900))


def test_code_briefing_states_the_moment_total_and_cites_everything(rails):
    b = code_briefing(Record(rails, "keyway"), Q)
    assert "3 unexplained" in b.answer and "$3,140.00" in b.answer
    assert "not the manager's fault" in b.answer
    assert b.claims and all(c.citations for c in b.claims)
    assert any("withdrawn" in r for r in b.prior_rulings)


def test_no_client_means_code_briefing(rails):
    briefer = Briefer(rails, use_model=False)
    assert briefer.brief("keyway", Q).answer == code_briefing(Record(rails, "keyway"), Q).answer
    assert briefer.last["mode"] == "code"


def test_model_briefing_keeps_cited_claims_and_drops_the_rest(rails):
    out = _BriefingOut(
        answer="Keyway has $3,140.00 in unexplained unpaid vendor money across three invoices.",
        claims=[
            _ClaimOut(text="INV-KW-0301 is unpaid; the bank shows no payment to Rio Grande Plumbing",
                      source_ids=["doc-bank-kw-2026-03", "doc-notice-riogrande-0420"]),
            _ClaimOut(text="Keyway is late on everything", source_ids=[]),                 # uncited
            _ClaimOut(text="The vendor emailed us", source_ids=["doc-made-up"]),           # unknown doc
            _ClaimOut(text="This looks like fraud", source_ids=["doc-bank-kw-2026-03"]),  # accusation
        ],
        prior_rulings=["INV-KW-0402 was withdrawn after its receipt date was corrected."])
    briefer = Briefer(rails, client=Stub(out), model="claude-haiku-5-5")
    b = briefer.brief("keyway", Q)
    assert briefer.last["mode"] == "model"
    assert [c.text for c in b.claims] == [out.claims[0].text]
    assert len(briefer.last["dropped_claims"]) == 3
    assert b.claims[0].citations[0].kind.value == "manager_bank"


def test_accusatory_answer_falls_back_to_code(rails):
    out = _BriefingOut(answer="Keyway committed fraud.", prior_rulings=[], claims=[
        _ClaimOut(text="INV-KW-0301 unpaid", source_ids=["doc-bank-kw-2026-03"])])
    briefer = Briefer(rails, client=Stub(out), model="claude-haiku-5-5")
    b = briefer.brief("keyway", Q)
    assert briefer.last["mode"] == "code" and "fraud" not in b.answer


def test_api_error_and_refusal_fall_back_to_code(rails):
    err = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))
    for stub in (Stub(error=err), Stub(None, stop_reason="refusal")):
        briefer = Briefer(rails, client=stub, model="claude-haiku-5-5")
        b = briefer.brief("keyway", Q)
        assert briefer.last["mode"] == "code" and "$3,140.00" in b.answer


def test_sonnet_gets_server_side_fallback(rails):
    out = _BriefingOut(answer="ok", prior_rulings=[], claims=[
        _ClaimOut(text="INV-KW-0301 unpaid", source_ids=["doc-bank-kw-2026-03"])])
    stub = Stub(out)
    Briefer(rails, client=stub, model="claude-sonnet-5-5").brief("keyway", Q)
    assert stub.calls[0]["fallbacks"] == "default"


def test_registry_loads_real_briefer(monkeypatch):
    from contracts import registry
    monkeypatch.setenv("BRIEFING", "real")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(registry.briefer(), Briefer)
    monkeypatch.setenv("BRIEFING", "fake")
    assert registry.briefer().brief("keyway", Q).claims
