"""Agent pipeline: chaser (Claude) -> hand-off task -> anomaly (Claude) -> verdict.

Claude is scripted, so these tests check the wiring and the guardrails, not model judgment.
"""
import json
from datetime import date
from types import SimpleNamespace as NS

import pytest

from chaser_agent import pipeline
from chaser_agent.tests.test_api import Mirror, plain
from contracts.models import Document
from rails.events.ingest import document_event
from workflow.memrails import MemoryRails

TASK_COLS = ("task_id", "agent", "invoice_id", "created_by", "reason", "payload", "as_of")
INV = "INV-KW-0301"


class QueueMirror(Mirror):
    def __init__(self):
        super().__init__()
        self.tasks = {}

    def query(self, sql):
        if "agent_task" in sql:
            return list(self.tasks.values())
        if "FROM agreement " in sql + " ":
            return []
        return super().query(sql)

    def call(self, reducer, args):
        if reducer == "enqueue_task":
            row = dict(zip(TASK_COLS, args, strict=True))
            self.tasks.setdefault(args[0], {**row, "status": "pending", "outcome": "{}",
                                            "trace": "{}", "created_at": len(self.tasks)})
        elif reducer == "update_task":
            self.tasks[args[0]].update(status=args[1], outcome=args[2], trace=args[3])
        else:
            super().call(reducer, args)


def _use(name, **inp):
    return NS(type="tool_use", id=f"t-{name}-{len(inp)}", name=name, input=inp)


class Scripted:
    """Answers each create() with the next scripted turn (a list of content blocks)."""

    def __init__(self, *turns):
        self.turns, self.calls = list(turns), []
        self.beta = NS(messages=self)

    def create(self, **kw):
        self.calls.append(kw)
        content = self.turns.pop(0)
        stop = "tool_use" if any(b.type == "tool_use" for b in content) else "end_turn"
        return NS(content=content, stop_reason=stop, model="scripted",
                  usage=NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0))


def _think(text):
    return NS(type="thinking", thinking=text)


@pytest.fixture
def world(monkeypatch):
    from chaser_agent import api
    rails = MemoryRails.from_world((3,), n_clean=0)
    mirror = QueueMirror()
    monkeypatch.setattr(api, "_mirror", lambda: mirror)
    return rails, mirror


def test_dispatch_queues_one_chaser_task_per_covered_invoice_once_a_day(world):
    rails, mirror = world
    today = date(2026, 4, 22)
    assert pipeline.dispatch(rails, mirror, today) == [f"chaser:{INV}:2026-04-22"]
    assert pipeline.dispatch(rails, mirror, today) == [f"chaser:{INV}:2026-04-22"]
    assert len(mirror.tasks) == 1


def test_chaser_agent_sends_the_reminder_policy_allows(world):
    rails, mirror = world
    today = date(2026, 4, 22)
    pipeline.dispatch(rails, mirror, today)
    claude = Scripted(
        [_think("Read the invoice first."), _use("get_invoice_context")],
        [_think("20 days overdue, polite is allowed."),
         _use("send_reminder", rationale="20 days past the 2026-04-02 deadline (agreement:deadline); "
                                         "nothing sent yet, so the polite reminder goes out.")])
    [done] = pipeline.process(rails, mirror, today, client=claude)
    assert done["decision"] == "send_reminder" and done["agent"] == "chaser"
    task = mirror.tasks[f"chaser:{INV}:2026-04-22"]
    trace = json.loads(task["trace"])
    assert task["status"] == "done" and trace["thinking"][0] == "Read the invoice first."
    assert [s["tool"] for s in trace["steps"]] == ["get_invoice_context", "send_reminder"]
    policy = trace["steps"][0]["result"]["policy"]
    assert policy["allowed"] == ["send_reminder", "wait"] and policy["level"] == "polite"
    [action] = mirror.actions.values()
    assert action["level"] == "polite" and action["reason"].startswith("20 days past")
    assert "<skill>" in claude.calls[0]["system"][0]["text"]


def test_chaser_cannot_escalate_early_and_must_pick_again(world):
    rails, mirror = world
    today = date(2026, 4, 22)
    pipeline.dispatch(rails, mirror, today)
    claude = Scripted(
        [_use("escalate", rationale="Overdue, escalate straight away to be safe.")],
        [_use("wait", rationale="Escalation was refused; waiting for the polite reminder first.")])
    [done] = pipeline.process(rails, mirror, today, client=claude)
    trace = json.loads(mirror.tasks[f"chaser:{INV}:2026-04-22"]["trace"])
    assert trace["steps"][0]["refused"] and "not allowed" in trace["steps"][0]["result"]["error"]
    assert done["decision"] == "wait" and not mirror.actions


def _pay(rails, on="2026-04-29"):
    rails.append_event(document_event(Document(
        id="bank-x", kind="manager_bank", manager_id="keyway", dated=date(2026, 4, 29),
        data={"payments": [{"invoice_id": INV, "vendor_id": "v-riogrande", "amount": 1240.0,
                            "on": on, "payee": "Rio Grande Plumbing LLC"}]})))


def test_handoff_creates_an_anomaly_task_and_the_verdict_closes_it(world):
    rails, mirror = world
    today = date(2026, 4, 30)
    _pay(rails)
    pipeline.dispatch(rails, mirror, today)
    claude = Scripted(
        [_use("get_invoice_context")],
        [_use("handoff_to_anomaly", rationale="Bank shows $1,240 paid on 2026-04-29, after the "
                                              "2026-04-02 deadline; handing to the anomaly agent.")],
        [_think("Check the findings."), _use("get_payment_case")],
        [_use("record_verdict", verdict="clean", summary="Looks fine to me overall, nothing to do.",
              finding_codes=[])],
        [_use("record_verdict", verdict="needs_review",
              summary="Rio Grande was paid $1,240 on 2026-04-29, 27 days after the 2026-04-02 "
                      "deadline; no statement after the payment yet.",
              finding_codes=["late_payment", "statement_predates_payment"])])
    done = pipeline.process(rails, mirror, today, client=claude)
    assert [d["agent"] for d in done] == ["chaser", "anomaly"]
    anomaly = mirror.tasks[f"anomaly:{INV}:2026-04-30"]
    assert anomaly["created_by"] == "chaser_agent" and anomaly["status"] == "done"
    steps = json.loads(anomaly["trace"])["steps"]
    assert steps[1]["refused"] and "cannot be clean" in steps[1]["result"]["error"]
    assert done[1]["verdict"] == "needs_review"
    handoff = next(v for k, v in mirror.decisions.items() if ":handoff_anomaly:" in k)
    assert handoff["status"] == "reviewed" and handoff["decided_by"] == "anomaly_agent"
    assert pipeline.dispatch(rails, mirror, today) == []     # handed off: no more chasing


def test_flow_shows_the_chain_in_order(world):
    rails, mirror = world
    today = date(2026, 4, 22)
    pipeline.dispatch(rails, mirror, today)
    pipeline.process(rails, mirror, today, client=Scripted(
        [_use("wait", rationale="Waiting for the agreement deadline check to settle today.")]))
    f = pipeline.flow(rails, mirror, INV)
    assert f["vendor"] == "Rio Grande Plumbing" and [t["agent"] for t in f["tasks"]] == ["chaser"]
    assert f["rules"][0]["rule_type"] == "pay_within_days"


def test_failed_agent_run_is_recorded_and_the_queue_continues(world):
    rails, mirror = world
    today = date(2026, 4, 22)
    pipeline.dispatch(rails, mirror, today)
    [done] = pipeline.process(rails, mirror, today, client=Scripted(
        [NS(type="text", text="I am not sure.")]))
    assert mirror.tasks[f"chaser:{INV}:2026-04-22"]["status"] == "failed"
    assert "without a decision" in done["error"]
    assert plain({"some": 1}) == 1


def _tools(rails, mirror, today, history=()):
    from chaser_agent import llm_chaser
    recorded = []

    def record(d, reason):
        recorded.append((d, reason))
        pipeline._record_chase(mirror, today)(d, reason)
    tools = llm_chaser.ChaserTools(rails, mirror, INV, today, list(history), lambda *a: "task", record)
    return tools, recorded


def test_evidence_lists_the_documents_and_a_flag_must_cite_them(world):
    rails, mirror = world
    today = date(2026, 4, 22)
    tools, recorded = _tools(rails, mirror, today)
    evidence = tools.get_evidence()
    kinds = {d["kind"] for d in evidence["documents"]}
    assert {"vendor_invoice", "funding"} <= kinds
    assert tools.flag_for_controller("other_contradiction", ["made-up-doc"], "Claims disagree here.")["error"]
    assert tools.flag_for_controller("not_a_kind", [evidence["documents"][0]["document"]], "x" * 20)["error"]
    cited = evidence["documents"][0]["document"]
    first = tools.flag_for_controller("statement_claims_unbacked", [cited], "The statement says paid; no bank row.")
    assert first["status"] == "needs_controller" and recorded[0][0].action == "flag"
    again, _ = _tools(rails, mirror, today, history=[{**recorded[0][0].to_dict(), "as_of": "2026-04-22"}])
    assert "already_flagged" in again.flag_for_controller("statement_claims_unbacked", [cited], "Same again, once more.")


def test_a_payment_to_another_vendor_is_not_counted_as_paid(world):
    rails, mirror = world
    rails.append_event(document_event(Document(
        id="bank-wrong", kind="manager_bank", manager_id="keyway", dated=date(2026, 4, 21),
        data={"payments": [{"invoice_id": INV, "vendor_id": "v-someone-else", "amount": 1240.0,
                            "on": "2026-04-21"}]})))
    tools, _ = _tools(rails, mirror, date(2026, 4, 22))
    assert tools.line.paid_amount == 0 and tools.code.action == "handoff_anomaly"
    assert "different vendor" in tools.code.reason
    rows = tools.get_evidence()["documents"]
    bank = next(d for d in rows if d["document"] == "bank-wrong")
    assert bank["detail"][0]["to_this_invoices_vendor"] is False


def test_the_prompt_describes_the_live_sending_rules(monkeypatch):
    from chaser_agent import daily, llm_chaser
    monkeypatch.setattr(daily, "AUTO_LEVELS", {"polite", "firm", "escalation"})
    monkeypatch.setattr(daily, "AUTO_ESCALATE", True)
    assert "polite, firm, escalation reminders go out automatically" in llm_chaser.system_prompt()
    monkeypatch.setattr(daily, "AUTO_LEVELS", {"polite"})
    assert "firm, escalation reminders wait for controller approval" in llm_chaser.system_prompt()
