"""Decision records (reasoning per agent call), live streaming, and the 15-day timeline."""

from datetime import UTC, datetime

import pytest

from agents import RealAgents
from agents.cause.agent import CauseAnswer
from agents.testing import ScriptedLLM, invoice, world_rails
from agents.trail import TrailAgent
from contracts.fakes import FakeAgents
from contracts.models import Cause, Event
from rails.console import JudgeConsole
from workflow.memrails import MemoryRails
from workflow.runner import RunResult
from workflow.terms import terms_for
from workflow.timeline import timeline
from workflow.trace import DecisionRecord, TracedAgents, Tracer
from world import generate
from world.generator import ALL_SCENARIOS

from .conftest import make_workflow

TRIGGER = Event(id="ev-test", type="scan_requested", manager_id="keyway",
                valid_from=datetime(2026, 4, 22, tzinfo=UTC), learned_at=datetime(2026, 4, 22, tzinfo=UTC))


def _cause_inputs(inv="INV-KW-0301"):
    line, docs = invoice(inv, "keyway")
    return line, TrailAgent(None).build_trail(line, docs), terms_for(world_rails(), FakeAgents(), "keyway")


def _record_one(llm, inv="INV-KW-0301"):
    tracer, got = Tracer(None), []
    tracer.subscribe(got.append)
    with tracer.run(TRIGGER):
        TracedAgents(RealAgents(llm), tracer).classify_cause(*_cause_inputs(inv))
    [rec] = got
    return rec


def test_rules_decision_shows_facts_rule_and_result():
    rec = _record_one(None)
    assert rec.agent == "cause" and rec.mode == "rules" and rec.invoice_id == "INV-KW-0301"
    kinds = [s.kind for s in rec.steps]
    assert kinds[0] == "fact" and kinds[-1] == "result" and "thinking" not in kinds
    assert rec.answer == {"cause": "unexplained"} and rec.citations
    assert rec.day.isoformat() == "2026-04-22" and rec.trigger == "ev-test"


def test_claude_thinking_and_usage_are_recorded():
    answer = CauseAnswer(cause=Cause.unexplained, confidence=0.9,
                         explanation="Funded on Mar 5, still unpaid; bank and vendor agree.",
                         citations=[{"source_id": "doc-funding-kw-0305"}])
    rec = _record_one(ScriptedLLM(answer, thinking="Bank shows no payment; vendor says past due; "
                                                   "no hold or paperwork reason."))
    assert rec.mode == "llm" and rec.model == "scripted" and rec.tokens_in == 100
    [thinking] = [s for s in rec.steps if s.kind == "thinking"]
    assert "vendor says past due" in thinking.text
    assert any(s.kind == "guard" and s.text.startswith("Guards passed") for s in rec.steps)


def test_rejected_claude_answer_shows_the_guard_and_the_fallback():
    bad = CauseAnswer(cause=Cause.owner_side, confidence=0.9, explanation="Oakline's fault.",
                      citations=[{"source_id": "doc-funding-kw-0305"}])
    rec = _record_one(ScriptedLLM(bad, bad, thinking="Maybe the owner?"))
    assert rec.mode == "fallback" and rec.answer == {"cause": "unexplained"}
    guards = [s.text for s in rec.steps if s.kind == "guard"]
    assert len(guards) == 2 and all("cannot be owner_side" in g for g in guards)
    assert [s.kind for s in rec.steps][-2:] == ["fallback", "result"]


@pytest.fixture(scope="module")
def world():
    rails = MemoryRails()
    wf = make_workflow(rails, agents=RealAgents(None))
    wf.replay(generate(scenarios=ALL_SCENARIOS, n_clean=30).events())
    return rails, wf


def _decisions(rails):
    return [e.payload for e in rails.events() if e.type == "agent_decision"]


def test_every_run_keeps_its_decisions_in_the_event_log(world):
    rails, _ = world
    decisions = _decisions(rails)
    agents = {d["agent"] for d in decisions}
    assert {"agreement", "trail", "cause", "verifier", "chaser"} <= agents
    order = [d["agent"] for d in sorted((d for d in decisions if d["invoice_id"] == "INV-KW-0301"),
                                         key=lambda d: (d["trigger"], d["seq"]))]
    assert "trail" in order and order.index("trail") < order.index("cause")


def test_stream_yields_decisions_live_then_the_result(world):
    rails, wf = world
    res = JudgeConsole(rails).apply_preset("manager_replies_paid", {"invoice_id": "INV-KW-0301"})
    items = list(wf.stream(res.events[0], ingest=False))
    *records, result = items
    assert isinstance(result, RunResult) and records
    assert all(isinstance(r, DecisionRecord) for r in records)
    assert records[0].agent == "chaser"                         # the reply is read first
    assert [r.seq for r in records] == sorted(r.seq for r in records)


def test_replay_adds_no_duplicate_decisions(world):
    rails, wf = world
    n = len(_decisions(rails))
    wf.replay(generate(scenarios=ALL_SCENARIOS, n_clean=30).events())
    assert len(_decisions(rails)) == n


def test_timeline_tells_the_moment_day_by_day():
    rails = MemoryRails()
    wf = make_workflow(rails, agents=RealAgents(None))
    wf.replay(generate(scenarios=ALL_SCENARIOS, n_clean=30).events())
    res = JudgeConsole(rails).apply_preset("invoice_received_correction")
    for e in res.events:
        wf.handle(e)
    t = timeline(rails, days=15)
    assert len(t["days"]) == 15 and t["days"][-1]["label"] == "Day 15"
    today = t["days"][-1]
    assert any(f["type"] == "fact_corrected" for f in today["facts"])
    assert any(o["type"] == "alert_withdrawn" and "INV-KW-0402" in o["invoice_ids"]
               for o in today["outcomes"])
    assert (today["unexplained_start"], today["unexplained_end"]) == (3140.0, 2240.0)
    assert any(d["agent"] == "cause" and d["invoice_id"] == "INV-KW-0402" for d in today["decisions"])
