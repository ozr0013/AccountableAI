"""Chaser agent, daily run for one invoice case.

1. Payment found in the bank        -> hand off to the anomaly agent.
2. No confirmed agreement deadline  -> wait for the agreement to be confirmed.
3. Not due yet                      -> sleep until the day after the deadline.
4. Overdue and unpaid               -> next reminder on the approved ladder
                                       (polite -> firm -> escalation, 5 days apart),
                                       drafted for controller approval; after the
                                       escalation reminder the case goes to a human.
Manager replies are read on every run: a "paid" reply only stops chasing once bank proof exists.
Nothing is sent from here: every reminder is an exact draft a controller must approve.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from rails.guards import render
from rails.guards.templates import LEVEL_ORDER, MIN_DAYS_BETWEEN
from workflow.evidence import case_docs, payment_proof

from .rules import default_names


def _morning(day: date) -> datetime:
    return datetime.combine(day, time(9), tzinfo=UTC)


def payment_seen(proof: dict) -> bool:
    """Any money left for this invoice, right or wrong: the anomaly agent takes over."""
    codes = {a['code'] for a in proof['anomalies']}
    return (proof['valid_bank_proof'] or float(proof['paid_amount']) > 0
            or bool(codes & {'wrong_vendor', 'recipient_unknown', 'invalid_payment_details'}))


class DailyChaser:
    def __init__(self, rails, cases, approvals, scheduler, reader=None, names=default_names):
        self.rails, self.cases, self.approvals, self.scheduler = rails, cases, approvals, scheduler
        self.reader, self.names = reader, names

    def _chases(self, case):
        return [e.payload for e in self.rails.events(case.manager_id)
                if e.type == 'chase_drafted' and e.payload.get('case_id') == case.id]

    def _sleep(self, case, until, reason, state=None, key=''):
        self.scheduler.schedule(case.id, until, f'chaser:{until.isoformat()}:{key}')
        case = self.cases.get(case.id)
        changes = {'next_wake_at': until, 'next_action': reason}
        if state:
            changes['state'] = state
        self.cases.update(case.id, case.version, self._now, **changes)
        return {'case_id': case.id, 'agent': 'chaser', 'action': 'wait', 'until': until.isoformat(),
                'reason': reason}

    def read_replies(self, case, now):
        """Read each new manager reply once (Claude when available), record the verdict."""
        done = {e.payload.get('reply_event') for e in self.rails.events(case.manager_id)
                if e.type == 'reply_read' and e.payload.get('case_id') == case.id}
        notes = []
        for e in self.rails.events(case.manager_id):
            if (e.type != 'chase_reply' or e.id in done or e.learned_at > now
                    or case.invoice_id not in (e.payload.get('invoice_ids') or [e.invoice_id])):
                continue
            from contracts.models import Document
            docs = [Document.model_validate(d) for d in case_docs(self.rails, case, now)]
            if self.reader is None:
                from .rules import read_reply
                reply = read_reply(e.payload.get('text', ''), case.manager_id, docs, [case.invoice_id])
            else:
                reply = self.reader.read_reply(e.payload.get('text', ''), case.manager_id, docs,
                                               [case.invoice_id])
            verdict = ('reply does not claim payment' if not reply.claims_paid else
                       'payment claim backed by ' + ', '.join(c.source_id for c in reply.proof)
                       if reply.proof else
                       'holding position: reply claims payment with no bank proof')
            self.cases.record(case.id, 'reply_read', {'reply_event': e.id, 'verdict': verdict,
                              'claims_paid': reply.claims_paid}, now, f'reply-read:{e.id}')
            notes.append(verdict)
        return notes

    def run(self, case, now) -> dict:
        self._now = now
        replies = self.read_replies(case, now)
        proof = payment_proof(self.rails, case, now)
        if payment_seen(proof):
            return {'case_id': case.id, 'agent': 'chaser', 'action': 'handoff_anomaly',
                    'replies': replies}
        due = proof['line'].get('due_date')
        tomorrow = _morning(now.date() + timedelta(days=1))
        if not due:
            return self._sleep(case, tomorrow, 'Waiting for confirmed agreement terms',
                               'awaiting_terms', 'terms')
        due = date.fromisoformat(due)
        if due >= now.date():
            return self._sleep(case, _morning(due + timedelta(days=1)),
                               f'Not due until {due}; check the day after', 'monitoring', 'due')
        return self._chase(case, proof, now, tomorrow, replies)

    def _chase(self, case, proof, now, tomorrow, replies):
        prior = self._chases(case)
        tickets = {t['id']: t for t in self.approvals.list(case.id)}
        def live(c):
            t = tickets.get(c['approval_id'], {})
            return t.get('status') == 'pending' and datetime.fromisoformat(t['expires_at']) > now

        if any(live(c) for c in prior):
            return self._sleep(case, tomorrow, 'Reminder draft awaiting controller approval',
                               'awaiting_human', 'pending')
        if prior and tickets.get(prior[-1]['approval_id'], {}).get('status') == 'rejected' and \
                (now.date() - date.fromisoformat(prior[-1]['drafted_on'])).days < MIN_DAYS_BETWEEN:
            return self._sleep(case, tomorrow, 'Controller declined the last reminder; '
                               f'next try after {MIN_DAYS_BETWEEN} days', key='declined')
        sent = [c for c in prior if tickets.get(c['approval_id'], {}).get('status') == 'approved']
        levels = [c['level'] for c in sent]
        if levels and levels[-1] == LEVEL_ORDER[-1]:
            return self._escalate(case, proof, now, sent)
        level = LEVEL_ORDER[LEVEL_ORDER.index(levels[-1]) + 1] if levels else LEVEL_ORDER[0]
        last = date.fromisoformat(sent[-1]['drafted_on']) if sent else None
        ok, why = self.rails.policy_check('send_chase', 'chaser', {
            'template_id': f'tpl-{level}', 'prior_levels': levels,
            'days_since_last': (now.date() - last).days if last else None})
        if not ok:
            return self._sleep(case, tomorrow, f'Next reminder not due yet ({why})', key='spacing')
        attempt = sum(c['level'] == level for c in prior)
        body = render(f'tpl-{level}', manager_name=self.names(case.manager_id),
                      amount=f"${float(proof['required_amount']):,.2f}",
                      vendor_name=self.names(case.vendor_id), invoice_ref=case.invoice_id)
        ticket = self.approvals.request(case, 'send_message',
                                        {'recipient': 'management:' + case.manager_id, 'body': body},
                                        now, f'chase:{level}:{attempt}', requested_by='chaser')
        self.cases.record(case.id, 'chase_drafted', {'level': level, 'template_id': f'tpl-{level}',
                          'approval_id': ticket['id'], 'drafted_on': now.date().isoformat(),
                          'due_date': proof['line']['due_date']}, now, f'chase-drafted:{level}:{attempt}')
        case = self.cases.get(case.id)
        self.cases.update(case.id, case.version, now, state='awaiting_payment_proof',
                          pending_approval=ticket['id'], next_wake_at=tomorrow,
                          next_action=f'{level.title()} reminder drafted for approval')
        self.scheduler.schedule(case.id, tomorrow, f'chaser:{tomorrow.isoformat()}:after-{level}')
        return {'case_id': case.id, 'agent': 'chaser', 'action': 'chase', 'level': level,
                'approval_id': ticket['id'], 'replies': replies}

    def _escalate(self, case, proof, now, sent):
        if case.state == 'escalated':
            tomorrow = _morning(now.date() + timedelta(days=1))
            return self._sleep(case, tomorrow, 'Escalated; waiting for payment or a human decision')
        reason = (f'Invoice {case.invoice_id} is past its {proof["line"]["due_date"]} deadline with '
                  f'no bank payment after {len(sent)} approved reminders.')
        self.cases.record(case.id, 'parties_notified', {
            'recipients': [case.owner_id, case.manager_id], 'channel': 'internal_case_feed',
            'notification': {'agent': 'chaser', 'reason': reason}}, now, 'chaser-escalated')
        ticket = self.approvals.request(case, 'case_review', {
            'reason': reason, 'proof_fingerprint': proof['proof_fingerprint']}, now,
            'chaser-escalation', requested_by='chaser')
        case = self.cases.get(case.id)
        tomorrow = _morning(now.date() + timedelta(days=1))
        self.cases.update(case.id, case.version, now, state='escalated', pending_approval=ticket['id'],
                          next_wake_at=tomorrow, next_action=reason)
        self.scheduler.schedule(case.id, tomorrow, f'chaser:{tomorrow.isoformat()}:escalated')
        return {'case_id': case.id, 'agent': 'chaser', 'action': 'escalate', 'reason': reason}
