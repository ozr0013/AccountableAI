"""The end-to-end eval on 16 generated worlds, not just the demo seed.

Same 9 planted problems, different vendors, amounts and background invoices. Catches bugs that
only show up when the background data shifts (the per-manager chase ladder hid behind seed 13).
Rules mode, no API key, about 10 seconds.
"""

import pytest

from evals.harness import run_system
from evals.scorer import score

SEEDS = (13, 1, 2, 3, 7, 42, 99, 2026)
SIZES = (30, 100)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.delenv("LIVE", raising=False)


@pytest.mark.parametrize("n_clean", SIZES)
@pytest.mark.parametrize("seed", SEEDS)
def test_every_world_passes(seed, n_clean):
    s = score(run_system("full", n_clean=n_clean, seed=seed))
    assert not s.failures, s.failures
    assert s.metrics["prohibited_executed"] == 0
