import pytest


@pytest.fixture(autouse=True)
def approval_mode(monkeypatch):
    """The tests cover the approval ladder: only the polite reminder goes out on its own and an
    escalation waits for the controller (CHASER_AUTO_LEVELS=polite, CHASER_AUTO_ESCALATE=off)."""
    from chaser_agent import daily
    monkeypatch.setattr(daily, "AUTO_LEVELS", {"polite"})
    monkeypatch.setattr(daily, "AUTO_ESCALATE", False)
