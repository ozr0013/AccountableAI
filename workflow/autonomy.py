"""Database-driven case ownership: three agents, one router, durable leased jobs.

    no confirmed terms        -> agreement agent proposes terms for a controller to confirm
    no payment seen yet       -> chaser agent (wait until due, then the reminder ladder)
    money left for the invoice -> anomaly agent (statement, amount, recipient, timing)
"""
from datetime import timedelta
from uuid import uuid4

from agents.anomaly import AnomalyAgent
from agents.chaser import ChaserAgent, DailyChaser
from rails.cases import aware
from workflow.cases import CaseStore
from workflow.human import HumanApprovals, evidence_fingerprint
from workflow.runtime_lock import runtime_lock
from workflow.scheduler import Scheduler


class AutonomyRuntime:
    def __init__(self, rails, llm=None, worker_id=None, max_jobs=20):
        if not 1 <= max_jobs <= 100:
            raise ValueError('max_jobs must be between 1 and 100')
        self.rails = rails
        self.cases, self.scheduler = CaseStore(rails), Scheduler(rails)
        self.approvals = HumanApprovals(rails)
        self.worker_id = worker_id or 'worker:' + str(uuid4())
        self.max_jobs = max_jobs
        self.chaser = DailyChaser(rails, self.cases, self.approvals, self.scheduler,
                                  reader=ChaserAgent(llm) if llm else None)
        self.anomaly = AnomalyAgent(rails, self.cases, self.approvals, self.scheduler, llm=llm)

    def sync(self, now):
        aware(now)
        managers = sorted({d.manager_id for d in self.rails.documents() if d.manager_id})
        for manager in managers:
            for line in self.rails.ledger(manager):
                if line.duplicate_of:
                    continue
                case = self.cases.ensure(line, now)
                with runtime_lock(self.rails, case.id):
                    case = self.cases.get(case.id)
                    fingerprint = evidence_fingerprint(self.rails, case)
                    changed = case.last_observation.get('evidence') != fingerprint
                    if changed:
                        # Persist the job first: a crash before the projection update safely replays it.
                        self.scheduler.schedule(case.id, now, 'evidence:' + fingerprint)
                        changes = {'last_observation': {'evidence': fingerprint}}
                        if case.state == 'resolved':
                            changes['state'] = 'monitoring'
                        self.cases.update(case.id, case.version, now, **changes)
                    # A deterministic daily backstop catches time passing without new rows.
                    if case.state != 'resolved':
                        self.scheduler.schedule(case.id, now, 'daily:' + now.date().isoformat())

    def route(self, case, now):
        """Pick the agent for this case from the evidence; code decides, not a model."""
        if case.kind == 'agreement':
            return {'case_id': case.id, 'agent': 'agreement', 'action': 'awaiting_review'}
        if case.state == 'resolved':
            return {'case_id': case.id, 'agent': None, 'action': 'resolved'}
        result = self.chaser.run(case, now)
        if result['action'] != 'handoff_anomaly':
            return result
        return {**self.anomaly.run(self.cases.get(case.id), now), 'replies': result['replies']}

    def tick(self, now):
        aware(now)
        self.sync(now)
        from workflow.agreement_intake import propose_agreements
        propose_agreements(self.rails, self.cases.list(), now)
        results = []
        handled = set()
        for _ in range(self.max_jobs):
            job = self.scheduler.claim(self.worker_id, now, lease_seconds=600)
            if job is None:
                break
            try:
                with runtime_lock(self.rails, job['case_id']):
                    case = self.cases.get(job['case_id'])
                    # Coalesce daily and evidence jobs claimed during the same poll.
                    if case.id not in handled:
                        results.append(self.route(case, now))
                        handled.add(case.id)
                    self.scheduler.complete(job['id'], self.worker_id, now)
            except Exception as exc:  # noqa: BLE001 - persist retries at the worker boundary
                self.scheduler.fail(job['id'], self.worker_id, type(exc).__name__,
                                    now + timedelta(minutes=5), now=now)
                results.append({'case_id': job['case_id'], 'status': 'retry_scheduled',
                                'error': type(exc).__name__})
        return results
