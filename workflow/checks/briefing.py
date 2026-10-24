"""Code briefing: "What's open with Keyway?" answered from the workflow's own record, cited.

Built straight from open alerts, past rulings and reminders. No model: every claim carries
the alert's own citations.
"""

from __future__ import annotations

from contracts.models import Alert, AlertKind, Briefing, Citation, Claim, Document
from contracts.steps import Rails

_NOT_MANAGER = {AlertKind.owner_pending_approval: "Oakline's own approval or funding is pending",
                AlertKind.paperwork_request: "waiting on vendor paperwork",
                AlertKind.bank_detail_change: "payment held for a vendor bank-detail check"}



def _cites(alert: Alert) -> list[Citation]:
    seen: dict[str, Citation] = {}
    for cl in alert.draft.claims:
        for c in cl.citations:
            seen.setdefault(c.source_id, c)
    return list(seen.values())


class Record:
    """Everything the briefing may say, for one manager."""

    def __init__(self, rails: Rails, manager_id: str):
        from workflow.book import WorkflowBook
        book = WorkflowBook.load(rails)
        self.manager_id = manager_id
        self.open = book.open_alerts(manager_id)
        self.withdrawn = sorted((a for a in book.alerts.values()
                                 if a.draft.manager_id == manager_id and a.state == "withdrawn"),
                                key=lambda a: a.draft.id)
        self.chases = book.chases.get(manager_id, [])
        self.recon = rails.reconcile(manager_id)
        cited = {c.source_id for a in self.open + self.withdrawn for c in _cites(a)}
        self.docs: list[Document] = [d for d in rails.documents(manager_id) if d.id in cited]

    @property
    def unexplained(self) -> list[Alert]:
        return [a for a in self.open if a.draft.kind == AlertKind.unexplained_unpaid and a.draft.amount > 0]


def code_briefing(rec: Record, question: str) -> Briefing:
    """Deterministic briefing: one cited claim per open alert, past rulings verbatim."""
    unexplained = rec.unexplained
    total = sum(a.draft.amount for a in unexplained)
    other = [a for a in rec.open if a.draft.kind in _NOT_MANAGER]
    pattern = [a for a in rec.open if a.draft.kind == AlertKind.recurring_pattern]
    dup = [a for a in rec.open if a.draft.kind == AlertKind.duplicate_billing]
    sentences = [f"{rec.manager_id.title()} has {len(unexplained)} unexplained unpaid vendor "
                 f"item(s) totaling ${total:,.2f}."]
    if pattern:
        sentences.append(pattern[0].draft.headline.rstrip(".") + ".")
    if dup:
        sentences.append(f"{len(dup)} duplicate billing item(s) flagged.")
    if other:
        sentences.append(f"{len(other)} open item(s) are not the manager's fault: "
                         + "; ".join(f"{a.draft.invoice_ids[0] if a.draft.invoice_ids else ''} "
                                     f"({_NOT_MANAGER[a.draft.kind]})" for a in other) + ".")
    claims = [Claim(text=a.draft.headline, citations=_cites(a)) for a in rec.open]
    rulings = [f"{a.draft.headline} -> withdrawn: {a.withdrawn_reason}" for a in rec.withdrawn]
    return Briefing(manager_id=rec.manager_id, question=question, answer=" ".join(sentences),
                    claims=claims, prior_rulings=rulings)


class CodeBriefer:
    """contracts.steps.Briefer over the code briefing."""

    def __init__(self, rails: Rails, **_):
        self.rails = rails
        self.last: dict = {}

    def brief(self, manager_id: str, question: str) -> Briefing:
        self.last = {"mode": "code"}
        return code_briefing(Record(self.rails, manager_id), question)


def build() -> CodeBriefer:
    from contracts import registry
    return CodeBriefer(registry.rails())
