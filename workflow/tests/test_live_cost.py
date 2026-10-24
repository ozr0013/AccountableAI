"""Keep live runs cheap and honest: no model calls for paid invoices, and no alert cites a
document the verifier was not given."""

from datetime import date

import pytest

from agents import RealAgents
from contracts.fakes import FakeApprovals
from contracts.models import AlertKind, Verdict, VerdictStatus
from workflow import Deps, Workflow
from workflow.book import WorkflowBook
from workflow.memrails import MemoryRails
from workflow.run import scan_events
from world import generate
from world.generator import ALL_SCENARIOS


class SpyVerifier:
    """Approves everything; records what it was asked and what it was given."""

    def __init__(self):
        self.drafts, self.missing = [], []

    def verify(self, draft, evidence):
        self.drafts.append(draft)
        have = {d.id for d in evidence}
        self.missing += [(draft.id, c.source_id) for cl in draft.claims for c in cl.citations
                         if c.source_id not in have]
        return Verdict(alert_id=draft.id, status=VerdictStatus.approved, reasons=["spy"])


class CountingAgents(RealAgents):
    def __init__(self):
        super().__init__(None)
        self.classified = []

    def classify_cause(self, line, trail, terms):
        self.classified.append(line.invoice_id)
        return super().classify_cause(line, trail, terms)


def _run(preloaded: bool, n_clean: int = 30):
    """preloaded=True is how `RAILS=real python -m workflow.run` behaves: the whole world is in
    the log before the first event is handled, so early events can see later documents."""
    w = generate(13, ALL_SCENARIOS, n_clean)
    events = w.events() + scan_events(["keyway"], w.as_of)
    spy, agents = SpyVerifier(), CountingAgents()
    if preloaded:
        rails = MemoryRails(events)
        wf = Workflow(Deps(rails, agents, spy, FakeApprovals()))
        results = [wf.handle(e) for e in events]
    else:
        rails = MemoryRails()
        wf = Workflow(Deps(rails, agents, spy, FakeApprovals()))
        results = wf.replay(events)
    return w, rails, spy, agents, results


@pytest.fixture(scope="module", params=["replay", "preloaded"])
def run(request):
    return _run(request.param == "preloaded")


def test_paid_late_invoices_cost_no_model_calls(run):
    w, _, spy, agents, _ = run
    paid_late = {l.invoice_id for l in w.ledger
                 if l.paid_amount >= l.amount and l.paid_on and l.due_date and l.paid_on > l.due_date}
    assert paid_late, "the demo world has late-but-paid invoices"
    # Only an invoice that was really unpaid and overdue at some point gets a cause: INV-KW-1008
    # was unpaid from Apr 6 to its Apr 17 payment. The other late-but-paid ones cost nothing.
    assert paid_late & set(agents.classified) <= {"INV-KW-1008"}
    # No zero-dollar item alerts, except refreshing the one alert that was open while that
    # invoice was really unpaid (INV-KW-1008). The pattern alert is the other $0 alert.
    zero = {i for d in spy.drafts if d.amount == 0 and d.kind != AlertKind.recurring_pattern
            for i in d.invoice_ids}
    assert zero <= {"INV-KW-1008"}


def test_the_late_months_are_still_reported_by_one_pattern_alert(run):
    _, _, spy, _, _ = run
    pattern = [d for d in spy.drafts if d.kind == AlertKind.recurring_pattern]
    assert len(pattern) == 1 and "3 of 4 months" in pattern[0].headline


def test_every_citation_is_in_the_evidence_the_verifier_gets(run):
    _, _, spy, _, _ = run
    assert spy.missing == []


def test_duplicate_alert_waits_until_the_duplicate_is_funded(run):
    w, _, spy, _, _ = run
    dups = [d for d in spy.drafts if d.kind == AlertKind.duplicate_billing]
    assert len({d.id for d in dups}) == 1
    cited = {c.source_id for cl in dups[0].claims for c in cl.citations}
    assert {"doc-funding-kw-0801", "doc-funding-kw-0802"} <= cited      # both fundings, first time
    assert len(dups) <= 2          # at most one refresh, when the March statement line arrives


def test_a_paid_late_invoice_with_an_open_alert_is_still_explained():
    """If an alert is already open on it, the cause agent still states why it no longer holds."""
    from workflow.steps import Steps
    w = generate(13, (3,), 5)
    rails = MemoryRails(w.events())
    steps = Steps(Deps(rails, RealAgents(None), SpyVerifier(), FakeApprovals()))
    line = next(l for l in rails.ledger("keyway") if l.invoice_id == "INV-KW-0301")
    late_paid = line.model_copy(update={"paid_amount": line.amount, "paid_on": date(2026, 4, 1)})
    assert steps._assess_one(late_paid, date(2026, 4, 22)).finding is None
    assert steps._assess_one(late_paid, date(2026, 4, 22), explain=True).finding is not None


def test_a_blocked_paid_late_refresh_does_not_leave_the_unpaid_alert_standing():
    """Live bug (Haiku): after the bank line proved the plumber paid, the refreshed 'paid 40 days
    late' record was blocked by the verifier, and the old 'unpaid' alert stayed open until the
    next scan. Now it is withdrawn at once, with the payment as the reason."""
    from rails.console import JudgeConsole

    class BlockPaidLate(SpyVerifier):
        def verify(self, draft, evidence):
            v = super().verify(draft, evidence)
            if draft.amount == 0:                        # the zero-dollar 'paid late' refresh
                return Verdict(alert_id=draft.id, status=VerdictStatus.blocked, reasons=["blocked"])
            return v

    w = generate(13, ALL_SCENARIOS, 30)
    rails = MemoryRails()
    wf = Workflow(Deps(rails, RealAgents(None), BlockPaidLate(), FakeApprovals()))
    wf.replay(w.events() + scan_events(["keyway"], w.as_of))
    assert any(a.draft.invoice_ids == ["INV-KW-0301"] for a in WorkflowBook.load(rails).open_alerts())

    rec = JudgeConsole(rails).apply_preset("bank_shows_payment",
                                           {"invoice_id": "INV-KW-0301", "on": "2026-04-22"})
    results = [wf.ingest(e) for e in rec.events]
    assert not any(a.draft.invoice_ids == ["INV-KW-0301"] for a in WorkflowBook.load(rails).open_alerts())
    why = [e.payload["reason"] for e in rails.events()
           if e.type == "alert_withdrawn" and e.invoice_id == "INV-KW-0301"][-1]
    assert why.startswith("payment is now proven by bank or vendor records on 2026-04-22")
    assert "days after the due date" in why and results[0].withdrawn


def test_a_refuted_refresh_for_something_still_owed_never_withdraws_the_original():
    """The narrow fix must not let a verifier wobble close a true unpaid alert."""
    class RefuteEverythingNew(SpyVerifier):
        def __init__(self):
            super().__init__()
            self.live = False

        def verify(self, draft, evidence):
            v = super().verify(draft, evidence)
            if self.live:
                return Verdict(alert_id=draft.id, status=VerdictStatus.refuted, reasons=["no"])
            return v

    w = generate(13, ALL_SCENARIOS, 30)
    rails, spy = MemoryRails(), RefuteEverythingNew()
    wf = Workflow(Deps(rails, RealAgents(None), spy, FakeApprovals()))
    wf.replay(w.events() + scan_events(["keyway"], w.as_of))
    before = {a.draft.id for a in WorkflowBook.load(rails).open_alerts()}
    spy.live = True
    wf.replay(scan_events(["keyway"], date(2026, 4, 27)))        # figures change; all refreshes refuted
    after = {a.draft.id for a in WorkflowBook.load(rails).open_alerts()}
    unpaid = {a.draft.id for a in WorkflowBook.load(rails).open_alerts()
              if a.draft.kind == AlertKind.unexplained_unpaid and a.draft.amount > 0}
    assert len(unpaid) == 3 and unpaid <= before and unpaid <= after
