"""Single-model baseline, offline: a stub model, then the same scorer the system gets."""

from types import SimpleNamespace

import pytest

from evals.baseline import Baseline, Finding, Findings, to_run
from evals.scorer import score
from workflow.memrails import MemoryRails
from world import generate
from world.generator import ALL_SCENARIOS


def F(item, cause, alert, actions=(), ids=()):
    return Finding(item_id=item, cause=cause, alert=alert, actions=list(actions),
                   source_ids=list(ids), why=f"{item}: {cause}")


class Stub:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kw):
        self.calls.append(kw)
        out = self.answers.pop(0)
        msg = SimpleNamespace(parsed_output=out, stop_reason="end_turn", usage=SimpleNamespace(
            input_tokens=100, output_tokens=2000, cache_read_input_tokens=0,
            cache_creation_input_tokens=20000))

        class Ctx:
            def __enter__(self_):
                return SimpleNamespace(get_final_message=lambda: msg)

            def __exit__(self_, *a):
                return False
        return Ctx()


@pytest.fixture(scope="module")
def world():
    w = generate(13, ALL_SCENARIOS, 30)
    return w, MemoryRails(w.events())


BEFORE = Findings(findings=[
    F("INV-KW-0301", "unexplained", "unexplained_unpaid", ["start_chase"],
      ["doc-stmt-kw-2026-03", "doc-bank-kw-2026-03", "doc-notice-riogrande-0420"]),
    F("INV-KW-0402", "unexplained", "unexplained_unpaid", ["start_chase"], ["doc-funding-kw-0402"]),
    F("INV-KW-0101", "unexplained", "unexplained_unpaid", [], ["doc-funding-kw-0101"]),  # false alert
])
AFTER = Findings(findings=[BEFORE.findings[0], BEFORE.findings[2],
                           F("INV-KW-0402", "vendor_side", "none", [], ["doc-funding-kw-0402"])])


def test_baseline_two_calls_second_reuses_cached_documents(world):
    stub = Stub([BEFORE, AFTER])
    b = Baseline(client=stub, model="claude-opus-5-5", budget_usd=1.0)
    b.run("<document/>", world[0].as_of)
    assert b.calls == 2 and b.cost > 0
    first = stub.calls[0]["messages"][0]["content"][0]
    assert first["cache_control"] == {"type": "ephemeral"}
    assert stub.calls[1]["messages"][2]["content"].startswith("New facts")


def test_mapping_is_graded_like_the_system(world):
    w, rails = world
    s = score(to_run(w, rails, BEFORE, AFTER, cost=0.5, latency=60.0))
    by = {x.id: x for x in s.scenarios}
    assert by[3].passed, by[3].failures                    # right alert, cause, citations, chase
    assert "false alert on manager" in by[1].failures       # blamed the authorized holdback
    assert by[4].failures                                  # no pattern finding
    assert not any("withdrew" in f for f in s.failures)     # INV-KW-0402 withdrawn once
    assert s.metrics["cost_usd"] == 0.5


def test_budget_stops_before_a_call(world):
    b = Baseline(client=Stub([BEFORE, AFTER]), model="claude-opus-5-5", budget_usd=0.0)
    with pytest.raises(RuntimeError, match="budget"):
        b.run("<document/>", world[0].as_of)
