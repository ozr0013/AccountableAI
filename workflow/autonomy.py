"""Database-driven case ownership and bounded, leased autonomous investigations."""
from datetime import timedelta
from uuid import uuid4

from agents.supervisor import Supervisor
from rails.cases import aware
from workflow.cases import CaseStore
from workflow.human import HumanApprovals, evidence_fingerprint
from workflow.runtime_lock import runtime_lock
from workflow.scheduler import Scheduler


class RuntimeActions:
    def __init__(self, rails):
        self.rails = rails
        self.cases = CaseStore(rails)
        self.approvals = HumanApprovals(rails)

    def _draft(self, case, body, now, key):
        return self.request_approval(case, 'send_message',
            {'recipient': 'management:' + case.manager_id, 'body': body}, now, key)

    def request_statement(self, case, now, key):
        return self._draft(case, f'Please provide the financial statement and remittance '
                           f'for invoice {case.invoice_id}, vendor {case.vendor_id}.', now, key)

    def request_payment_proof(self, case, now, key):
        proof = Supervisor(self.rails)._proof(case, now)
        if proof['valid_bank_proof']:
            return {'error': 'payment_already_proven'}
        line = proof['line']
        if not line.get('due_date'):
            return {'error': 'confirmed_payment_deadline_required'}
        from datetime import date
        if date.fromisoformat(line['due_date']) > now.date():
            return {'error': 'payment_not_due'}
        return self._draft(case, f'Please provide payment evidence for invoice {case.invoice_id}, '
                           f'vendor {case.vendor_id}; payable {proof["expected_amount"]}, '
                           f'deadline {line["due_date"]}.', now, key)

    def request_approval(self, case, kind, payload, now, key):
        # Suppress repeated requests while the same business question is still pending.
        if kind == 'case_review':
            payload = {**payload, 'proof_fingerprint': Supervisor(self.rails)._proof(case, now)['proof_fingerprint']}
        fingerprint = evidence_fingerprint(self.rails, case)
        pending = next((t for t in self.approvals.list(case.id)
                        if t['status'] == 'pending' and t['kind'] == kind
                        and t['payload'] == payload and t['evidence'] == fingerprint), None)
        if pending:
            try:
                self.approvals.validate(pending, now)
                return pending
            except ValueError:
                pass
        return self.approvals.request(case, kind, payload, now, key)

    def notify_parties(self, case, payload, now, key):
        event = self.cases.record(case.id, 'parties_notified',
            {'recipients': [case.owner_id, case.manager_id], 'notification': payload,
             'channel': 'internal_case_feed'}, now, key + ':notification')
        ticket = self.request_approval(case, 'case_review', payload, now, key + ':review')
        return {'status': 'human_review_required', 'event_id': event.id, 'approval_id': ticket['id']}


class AutonomyRuntime:
    def __init__(self, rails, client=None, worker_id=None, max_jobs=20):
        if not 1 <= max_jobs <= 100:
            raise ValueError('max_jobs must be between 1 and 100')
        self.rails = rails
        self.cases, self.scheduler = CaseStore(rails), Scheduler(rails)
        self.worker_id = worker_id or 'worker:' + str(uuid4())
        self.max_jobs = max_jobs
        self.supervisor = Supervisor(rails, client=client, actions=RuntimeActions(rails))

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
                        from workflow.feedback import FeedbackStore
                        feedback = FeedbackStore(self.rails)
                        self.supervisor.skill_override = feedback.active_override('case-investigation')
                        self.supervisor.skill_overrides = {name: feedback.active_override(name)
                            for name in ('agreement', 'chasing', 'payment_review', 'escalation')}
                        result = self.supervisor.run(case.id, now)
                        results.append(result)
                        handled.add(case.id)
                    self.scheduler.complete(job['id'], self.worker_id, now)
            except Exception as exc:  # noqa: BLE001 - persist retries at the worker boundary
                self.scheduler.fail(job['id'], self.worker_id, type(exc).__name__,
                                    now + timedelta(minutes=5), now=now)
                results.append({'case_id': job['case_id'], 'status': 'retry_scheduled',
                                'error': type(exc).__name__})
        return results
