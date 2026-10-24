import pytest

from contracts.fakes import FakeAgents, FakeApprovals, FakeVerifier
from workflow import Deps, Workflow
from workflow.memrails import MemoryRails


@pytest.fixture(autouse=True)
def _stand_ins(monkeypatch):
    """Workflow tests run on stand-ins unless a test opts in to something real."""
    for var in ("RAILS", "AGENTS", "VERIFIER", "APPROVALS"):
        monkeypatch.setenv(var, "fake")
    monkeypatch.setenv("MEMORY", "postgres")


def make_workflow(rails=None, agents=None, verifier=None) -> Workflow:
    return Workflow(Deps(rails or MemoryRails(), agents or FakeAgents(),
                         verifier or FakeVerifier(), FakeApprovals()))
