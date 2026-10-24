"""Exact-proposal approvals bound to business evidence and a trusted human identity."""
import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta

from rails.cases import aware, identity
from workflow.cases import CaseStore
from workflow.runtime_lock import runtime_lock


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def evidence_fingerprint(rails, case):
    # Scope and redact shared documents exactly as the investigation tool does.
    from datetime import UTC, datetime

    from agents.supervisor import Supervisor
    docs = Supervisor(rails)._docs(case, datetime.max.replace(tzinfo=UTC))
    facts = [e.model_dump(mode='json') for e in rails.events(case.manager_id)
             if e.type in ('terms_confirmed', 'agreement_confirmed')]
    return digest({'documents': docs, 'terms': facts})


@dataclass(frozen=True)
class Principal:
    id: str
    role: str


class HumanApprovals:
    def __init__(self, rails):
        self.rails = rails
        self.cases = CaseStore(rails)

    def list(self, case_id=None):
        tickets = {}
        for event in self.rails.events():
            if event.type in ('human_approval_requested', 'human_approval_decided'):
                ticket = deepcopy(event.payload['ticket'])
                tickets[ticket['id']] = ticket
        return [t for t in tickets.values() if case_id is None or t['case_id'] == case_id]

    def get(self, ticket_id):
        return next(t for t in self.list() if t['id'] == ticket_id)

    def request(self, case, kind, payload, now, key, requested_by='supervisor'):
        aware(now)
        ticket_id = 'approval_' + identity(case.id, key)
        with runtime_lock(self.rails, 'approval:' + ticket_id):
            existing = next((t for t in self.list() if t['id'] == ticket_id), None)
            if existing:
                if existing['payload'] != payload or existing['kind'] != kind:
                    raise ValueError('approval key already bound to different proposal')
                return existing
            proposal = {'case_id': case.id, 'kind': kind, 'payload': deepcopy(payload),
                        'evidence': evidence_fingerprint(self.rails, case)}
            ticket = {**proposal, 'id': ticket_id, 'digest': digest(proposal),
                      'requested_by': requested_by, 'status': 'pending',
                      'expires_at': (now + timedelta(days=7)).isoformat()}
            self.cases.record(case.id, 'human_approval_requested', {'ticket': ticket}, now, ticket_id)
            return deepcopy(ticket)

    def validate(self, ticket, now):
        from datetime import datetime
        aware(now)
        if datetime.fromisoformat(ticket['expires_at']) < now:
            raise ValueError('approval expired')
        case = self.cases.get(ticket['case_id'])
        if evidence_fingerprint(self.rails, case) != ticket['evidence']:
            raise ValueError('approval stale: business evidence changed')

    def decide(self, ticket_id, principal, approve, now, proposal_digest):
        if principal.role != 'controller' or not principal.id:
            raise PermissionError('controller identity required')
        with runtime_lock(self.rails, 'approval:' + ticket_id):
            ticket = self.get(ticket_id)
            if principal.id == ticket['requested_by']:
                raise PermissionError('requester cannot approve own proposal')
            if proposal_digest != ticket['digest']:
                raise ValueError('proposal digest mismatch')
            status = 'approved' if approve else 'rejected'
            if ticket['status'] != 'pending':
                if ticket['status'] != status:
                    raise ValueError('decision already recorded')
                return deepcopy(ticket)
            if approve:
                self.validate(ticket, now)
            ticket = {**ticket, 'status': status, 'decided_by': principal.id,
                      'decided_at': now.isoformat()}
            self.cases.record(ticket['case_id'], 'human_approval_decided', {'ticket': ticket}, now,
                              ticket_id + ':decision')
            return deepcopy(ticket)
