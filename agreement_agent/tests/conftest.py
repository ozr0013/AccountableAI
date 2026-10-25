import sys
from pathlib import Path

# the agent is standalone (not in pyproject's package list); make it importable for its tests
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


import pytest


@pytest.fixture(autouse=True)
def no_external_coordinator_wake(monkeypatch):
    from agreement_agent import api
    monkeypatch.setattr(api, 'wake_workflow', lambda: {'triggered': False, 'error': 'isolated test'})
