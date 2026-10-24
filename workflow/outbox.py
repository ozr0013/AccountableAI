"""Durable approved drafts. Real mail transport is deliberately unconfigured."""
from copy import deepcopy

from rails.cases import identity
from workflow.cases import CaseStore
from workflow.human import HumanApprovals
from workflow.runtime_lock import runtime_lock


class TestTransport:
    __test__ = False

    def __init__(self):
        self.deliveries = {}

    def send(self, message):
        self.deliveries.setdefault(message['id'], dict(message))
        return {'status': 'delivered_test', 'receipt': 'test:' + message['id']}


class Outbox:
    def __init__(self, rails):
        self.rails, self.cases = rails, CaseStore(rails)

    def list(self, case_id=None):
        rows = {}
        for e in self.rails.events():
            if e.type == 'outbox_changed':
                message = deepcopy(e.payload['message'])
                rows[message['id']] = message
        return [m for m in rows.values() if case_id is None or m['case_id'] == case_id]

    def _save(self, message, now, key):
        self.cases.record(message['case_id'], 'outbox_changed', {'message': message}, now, key)
        return deepcopy(message)

    def _approve(self, case_id, recipient, body, approval_id, now):
        if not approval_id:
            raise PermissionError('exact draft requires human approval')
        approvals = HumanApprovals(self.rails)
        ticket = approvals.get(approval_id)
        approvals.validate(ticket, now)
        if (ticket['status'] != 'approved' or ticket['kind'] != 'send_message'
                or ticket['case_id'] != case_id
                or ticket['payload'] != {'recipient': recipient, 'body': body}):
            raise PermissionError('approved proposal does not match exact draft')

    def enqueue(self, case_id, recipient, body, now, key, approval_id=None):
        message_id = 'message_' + identity(case_id, key)
        with runtime_lock(self.rails, 'approval:' + str(approval_id)):
            prior = next((m for m in self.list() if m['id'] == message_id), None)
            if prior:
                if any(prior[k] != v for k, v in {'recipient': recipient, 'body': body,
                                                  'approval_id': approval_id}.items()):
                    raise ValueError('message key already bound to different content')
                return prior
            self._approve(case_id, recipient, body, approval_id, now)
            # A ticket authorizes a single message, even if a caller invents another key.
            if any(m['approval_id'] == approval_id for m in self.list()):
                raise PermissionError('approval already consumed by another message')
            message = {'id': message_id, 'case_id': case_id, 'recipient': recipient,
                       'body': body, 'approval_id': approval_id, 'status': 'queued'}
            return self._save(message, now, message_id + ':queued')

    def deliver(self, message_id, now, transport=None):
        with runtime_lock(self.rails, 'outbox:' + message_id):
            message = next(m for m in self.list() if m['id'] == message_id)
            if message['status'] in ('delivered_test', 'uncertain', 'sending'):
                return message
            self._approve(message['case_id'], message['recipient'], message['body'],
                          message['approval_id'], now)
            if transport is None:
                message = {**message, 'status': 'waiting_transport'}
                return self._save(message, now, message_id + ':waiting_transport')
            if not isinstance(transport, TestTransport):
                raise TypeError('only explicit simulated transport is supported')
            # Test transport is idempotent; no external network side effects occur.
            result = transport.send(message)
            return self._save({**message, **result}, now, message_id + ':receipt')
