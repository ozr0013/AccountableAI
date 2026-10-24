"""What one system run reports, per scenario. The workflow (or a baseline) fills this in;
evals/scorer.py grades it against evals/answer_key.json."""

from __future__ import annotations

from pydantic import BaseModel, Field

from contracts.models import Alert, AlertKind, Cause, Citation

# Alerts that point at the middleman. owner_pending_approval and paperwork_request do not.
MANAGER_ALERT_KINDS = {AlertKind.unexplained_unpaid, AlertKind.recurring_pattern,
                       AlertKind.duplicate_billing}


class ScenarioResult(BaseModel):
    scenario_id: int
    cause: Cause | None = None
    cause_citations: list[Citation] = Field(default_factory=list)  # from the CauseFinding
    alerts: list[Alert] = Field(default_factory=list)   # final state after the run
    actions: list[str] = Field(default_factory=list)    # e.g. "start_chase", "draft_document_request"
    prohibited_attempted: list[str] = Field(default_factory=list)  # tried and blocked by a gate
    prohibited_executed: list[str] = Field(default_factory=list)   # got through: must stay empty
    recon_diff: float = 0.0                             # funded - (paid + held + owed) for the manager
    cost_usd: float = 0.0
    latency_s: float = 0.0


class MomentResult(BaseModel):
    """The judge moment: a corrected fact should withdraw exactly one alert and keep the rest."""

    rechecked: list[str] = Field(default_factory=list)  # alert ids rerun after the new fact
    withdrawn: list[Alert] = Field(default_factory=list)
    kept: list[Alert] = Field(default_factory=list)


class RunResult(BaseModel):
    system: str                                         # "full", "no_verifier", "single_model"
    scenarios: list[ScenarioResult]
    moment: MomentResult | None = None
