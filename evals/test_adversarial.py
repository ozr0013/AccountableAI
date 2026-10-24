"""Planted bad drafts, offline: no verifier catches nothing; the verifier's code checks catch the
format mistakes and never block a true alert. (The judgment cases need the live model.)"""

import pytest

from evals.adversarial import CASES, CodeRulesOnly, run, summarize
from evals.harness import ApproveAll
from workflow.memrails import MemoryRails
from world.generator import ALL_SCENARIOS


@pytest.fixture(scope="module")
def rails():
    return MemoryRails.from_world(ALL_SCENARIOS)


def test_every_cited_document_exists(rails):
    known = {d.id for d in rails.documents("keyway")}
    ghosts = {"doc-conf-riogrande-0301"}            # the nonexistent_source case, on purpose
    for c in CASES:
        for cl in c.draft.claims:
            for cite in cl.citations:
                assert cite.source_id in known | ghosts, (c.name, cite.source_id)


def test_no_verifier_lets_everything_through(rails):
    s = summarize(run(ApproveAll(), rails))
    assert s["caught"] == 0 and s["controls_ok"] == s["controls"]


def test_code_rules_catch_format_mistakes_and_pass_true_alerts(rails):
    r = run(CodeRulesOnly(), rails)
    for name in ("accusation", "uncited_claim", "made_up_quote", "nonexistent_source"):
        assert r[name][0] == "blocked", name
    assert summarize(r)["wrongly_blocked"] == []
