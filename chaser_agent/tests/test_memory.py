"""Agents remember across runs: their own earlier decisions, and lessons with evidence."""
import json
from datetime import date

import pytest

from chaser_agent import books, memory, pipeline
from chaser_agent.tests.test_pipeline import INV, QueueMirror
from workflow.memrails import MemoryRails


class MemoryMirror(QueueMirror):
    def __init__(self):
        super().__init__()
        self.lessons = {}

    def query(self, sql):
        if "agent_lesson" in sql:
            return list(self.lessons.values())
        return super().query(sql)

    def call(self, reducer, args):
        if reducer == "remember_lesson":
            k, book, subject, lesson, evidence, by, as_of = args
            self.lessons.setdefault(k, {"key": k, "book": book, "subject": subject, "lesson": lesson,
                                        "evidence": evidence, "by_agent": by, "as_of": as_of})
        else:
            super().call(reducer, args)


@pytest.fixture
def world(monkeypatch):
    from chaser_agent import api
    rails = MemoryRails.from_world((3,), n_clean=0)
    mirror = MemoryMirror()
    monkeypatch.setattr(api, "_mirror", lambda: mirror)
    return rails, mirror


def _done(mirror, agent, subject, day, outcome, created_by="daily_schedule"):
    tid = f"{agent}:{subject}:{day}"
    mirror.tasks[tid] = {"task_id": tid, "agent": agent, "invoice_id": subject, "status": "done",
                         "as_of": day, "created_at": day, "created_by": created_by, "reason": "",
                         "payload": "{}", "trace": "{}", "outcome": json.dumps(outcome)}


def test_an_agent_sees_what_it_decided_on_earlier_days_and_not_today(world):
    rails, mirror = world
    _done(mirror, "chaser", INV, "2026-04-21", {"decision": "wait", "rationale": "waiting for the bank reference"})
    _done(mirror, "chaser", INV, "2026-04-22", {"decision": "send_reminder", "rationale": "polite sent"})
    runs = memory.previous_runs(mirror, "chaser", INV, date(2026, 4, 22))
    assert [r["said"] for r in runs] == ["waiting for the bank reference"]


def test_the_chaser_context_carries_its_memory(world):
    rails, mirror = world
    _done(mirror, "chaser", INV, "2026-04-20", {"decision": "wait", "rationale": "Keyway says paid; no bank row yet"})
    from chaser_agent import llm_chaser
    tools = llm_chaser.ChaserTools(rails, mirror, INV, date(2026, 4, 22), [], lambda *a: "t", lambda d, r: None)
    doc = books.run(rails, "SELECT document_id FROM documents LIMIT 1")["rows"][0][0]
    out = memory.remember(mirror, rails, "chaser", tools.vendor, "This vendor's invoices keep slipping past the deadline.",
                          [doc], date(2026, 4, 22))
    assert "remembered" in out
    ctx = tools.get_invoice_context()
    assert ctx["your_previous_runs"][0]["said"].startswith("Keyway says paid")
    assert ctx["lessons"][0]["lesson"].startswith("This vendor's invoices")


def test_a_lesson_needs_a_known_subject_and_real_evidence(world):
    rails, mirror = world
    today = date(2026, 4, 22)
    assert "error" in memory.remember(mirror, rails, "chaser", "Nobody Inc", "A pattern long enough to keep.", ["x"], today)
    assert "error" in memory.remember(mirror, rails, "chaser", "Keyway Property Management",
                                      "A pattern long enough to keep.", ["made-up-doc"], today)


def test_the_investigator_starts_from_its_last_sweep_and_open_flags(world):
    rails, mirror = world
    from chaser_agent import investigator
    _done(mirror, "investigator", "portfolio", "2026-04-21",
          {"decision": "finish_sweep", "summary": "Matched an unreferenced Sparkline payment to INV-KW-2004."})
    today = date(2026, 4, 22)
    tools = investigator.InvestigatorTools(rails, mirror, today, pipeline._record_chase(mirror, today), lambda *a: "t")
    brief = investigator.briefing(tools)
    assert "Matched an unreferenced Sparkline payment" in brief and "your_open_flags" in brief
