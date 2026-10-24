"""Anomaly agent: once money has left for an invoice, check that the transaction is right.

Code decides every finding; Claude (when available) only writes the plain-language note, and
the note must name each finding and use neutral wording or the template note is used instead.

1. Read the deterministic payment proof (bank rows for this invoice, agreement payable amount).
2. Findings: wrong or unknown recipient, invalid payment row, underpaid, overpaid, late,
   holdback unverified, terms missing, statement disagrees with the bank.
3. No management statement yet  -> draft a statement request for approval and wait.
4. Any finding                   -> escalate: notify owner and management, open a controller
                                    review bound to this exact proof, record the anomaly.
5. Clean and statement matches   -> record the verified payment and close the case.
A controller acknowledgment of the exact proof lets an anomalous case close.
"""
from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

from pydantic import BaseModel, Field

from workflow.evidence import case_docs, payment_proof

from ..guards import wording_problems
from ..llm import StructuredLLM, guarded_call

SYSTEM = """You write a short note for Oakline Homes' controller about one vendor payment that \
its property manager made. The findings are already decided by code; do not add, drop or \
soften any. Name every finding code given, state the amounts given, and use neutral wording \
(never "fraud", "theft", "misuse" or similar). Two to four sentences."""

LABELS = {
    'wrong_vendor': 'the bank payment went to a different recipient than the invoice vendor',
    'recipient_unknown': 'the bank record does not name the recipient',
    'invalid_payment_details': 'a bank row for this vendor has an invalid amount or date',
    'underpayment': 'less than the payable amount was paid',
    'overpayment': 'more than the invoice amount was paid',
    'late_payment': 'the payment was made after the agreement deadline',
    'holdback_unverified': 'part of the invoice was held back without a confirmed release condition',
    'holdback_pending': 'the contractual holdback is still outstanding',
    'terms_missing': 'no confirmed agreement terms cover this invoice',
    'invoice_namespace_conflict': 'another vendor uses the same invoice number',
    'statement_mismatch': "the manager's statement does not match the bank payment",
}


class AnomalyNote(BaseModel):
    note: str = Field(description="2-4 neutral sentences naming every finding code")


def _morning(day) -> datetime:
    return datetime.combine(day, time(9), tzinfo=UTC)


def findings_for(proof: dict, statement_seen: bool) -> list[dict]:
    rows = [dict(a) for a in proof['anomalies']]
    paid, required = Decimal(proof['paid_amount']), Decimal(proof['required_amount'])
    if Decimal(0) < paid < required and not any(r['code'] == 'underpayment' for r in rows):
        rows.append({'code': 'underpayment', 'source_ids': proof['source_ids']})
    if statement_seen and proof['valid_bank_proof'] and not proof['statement_matches']:
        rows.append({'code': 'statement_mismatch', 'source_ids': proof['source_ids']})
    seen, out = set(), []
    for r in rows:
        if r['code'] not in seen:
            seen.add(r['code'])
            out.append(r)
    return out


def template_note(case, proof, findings) -> str:
    parts = [LABELS.get(f['code'], f['code'].replace('_', ' ')) for f in findings]
    return (f"Invoice {case.invoice_id} ({case.vendor_id}): paid {proof['paid_amount']} of "
            f"{proof['expected_amount']} (payable now {proof['required_amount']}). "
            f"Findings: {'; '.join(parts)}.")


class AnomalyAgent:
    def __init__(self, rails, cases, approvals, scheduler, llm: StructuredLLM | None = None):
        self.rails, self.cases, self.approvals, self.scheduler = rails, cases, approvals, scheduler
        self.llm = llm

    def _note(self, case, proof, findings) -> str:
        fallback = template_note(case, proof, findings)
        codes = [f['code'] for f in findings]

        def check(a: AnomalyNote) -> list[str]:
            p = [f'note must name finding {c}' for c in codes if c not in a.note]
            return p + wording_problems(a.note)

        prompt = (f"<invoice>{case.invoice_id} vendor {case.vendor_id}</invoice>\n"
                  f"<amounts>expected {proof['expected_amount']}, payable now "
                  f"{proof['required_amount']}, paid {proof['paid_amount']}, due "
                  f"{proof['line'].get('due_date')}</amounts>\n<findings>{', '.join(codes)}</findings>")
        ans, _ = guarded_call(self.llm, AnomalyNote, SYSTEM, prompt, check)
        return ans.note if ans else fallback

    def _wait(self, case, now, until, reason, state):
        self.scheduler.schedule(case.id, until, f'anomaly:{until.isoformat()}:{state}')
        case = self.cases.get(case.id)
        self.cases.update(case.id, case.version, now, state=state, next_wake_at=until,
                          next_action=reason)
        return {'case_id': case.id, 'agent': 'anomaly', 'action': 'wait', 'reason': reason}

    def run(self, case, now) -> dict:
        proof = payment_proof(self.rails, case, now)
        statement_seen = any(d['kind'] == 'manager_statement' for d in case_docs(self.rails, case, now))
        findings = findings_for(proof, statement_seen)
        if findings and not proof['anomalies_acknowledged']:
            return self._escalate(case, proof, findings, now)
        if not proof['statement_matches']:
            return self._request_statement(case, proof, now)
        return self._verify(case, proof, findings, now)

    def _request_statement(self, case, proof, now):
        body = (f'Please provide the management statement and remittance for invoice '
                f'{case.invoice_id}, vendor {case.vendor_id}, covering the bank payment of '
                f"{proof['paid_amount']}.")
        ticket = self.approvals.request(case, 'send_message',
                                        {'recipient': 'management:' + case.manager_id, 'body': body},
                                        now, 'statement-request', requested_by='anomaly')
        case = self.cases.get(case.id)
        if ticket['status'] == 'pending':
            self.cases.update(case.id, case.version, now, pending_approval=ticket['id'])
        return self._wait(case, now, _morning(now.date() + timedelta(days=3)),
                          'Payment found; waiting for the management statement', 'awaiting_statement')

    def _escalate(self, case, proof, findings, now):
        fp = proof['proof_fingerprint']
        if any(e.type == 'anomaly_found' and e.payload.get('proof_fingerprint') == fp
               and e.payload.get('case_id') == case.id for e in self.rails.events(case.manager_id)):
            return self._wait(case, now, _morning(now.date() + timedelta(days=1)),
                              'Anomaly escalated; waiting for the controller', 'escalated')
        note = self._note(case, proof, findings)
        key = 'anomaly:' + fp[:16]
        self.cases.record(case.id, 'anomaly_found', {
            'proof_fingerprint': fp, 'findings': findings, 'note': note,
            'paid_amount': proof['paid_amount'], 'expected_amount': proof['expected_amount'],
            'required_amount': proof['required_amount'], 'source_ids': proof['source_ids']},
            now, key)
        self.cases.record(case.id, 'parties_notified', {
            'recipients': [case.owner_id, case.manager_id], 'channel': 'internal_case_feed',
            'notification': {'agent': 'anomaly', 'note': note,
                             'codes': [f['code'] for f in findings]}}, now, key + ':notified')
        ticket = self.approvals.request(case, 'case_review', {'reason': note, 'proof_fingerprint': fp},
                                        now, key + ':review', requested_by='anomaly')
        case = self.cases.get(case.id)
        self.cases.update(case.id, case.version, now, pending_approval=ticket['id'])
        out = self._wait(case, now, _morning(now.date() + timedelta(days=1)), note, 'escalated')
        return {**out, 'action': 'escalate', 'findings': [f['code'] for f in findings]}

    def _verify(self, case, proof, findings, now):
        self.cases.record(case.id, 'payment_verified', {
            'proof_fingerprint': proof['proof_fingerprint'], 'paid_amount': proof['paid_amount'],
            'source_ids': proof['source_ids'], 'acknowledged_findings': findings},
            now, 'verified:' + proof['proof_fingerprint'][:16])
        self.cases.record(case.id, 'parties_notified', {
            'recipients': [case.owner_id, case.manager_id], 'channel': 'internal_case_feed',
            'notification': {'agent': 'anomaly', 'note': f'Invoice {case.invoice_id} payment '
                             'verified against bank and statement.'}},
            now, 'verified:' + proof['proof_fingerprint'][:16] + ':notified')
        case = self.cases.get(case.id)
        self.cases.update(case.id, case.version, now, state='resolved', next_wake_at=None,
                          next_action=None, pending_approval=None)
        self.scheduler.cancel(case.id, preserve_leased=True)
        return {'case_id': case.id, 'agent': 'anomaly', 'action': 'resolve'}
