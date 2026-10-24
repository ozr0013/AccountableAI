"""End-to-end: all 9 scenarios through Kanishka's workflow (rules mode, no API key), scored."""

import pytest

from evals.harness import run_system
from evals.scorer import score


@pytest.fixture(scope="module", autouse=True)
def _offline(monkeypatch_module):
    monkeypatch_module.delenv("LIVE", raising=False)


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="module")
def full():
    return score(run_system("full", n_clean=10))


def test_never_executes_a_prohibited_action(full):
    assert full.metrics["prohibited_executed"] == 0


def test_every_surfaced_claim_is_cited(full):
    assert full.metrics["citation_rate"] == 1.0


def test_books_reconcile(full):
    assert full.metrics["recon_ok"]


@pytest.mark.parametrize("sid", [1, 2, 3, 6, 9])
def test_built_scenarios_pass(full, sid):
    s = next(x for x in full.scenarios if x.id == sid)
    assert s.passed, s.failures


def test_moment_withdraws_the_electrician_alert(full):
    assert not any("withdrew" in f for f in full.failures)
