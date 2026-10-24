"""Case-scoped evidence and the deterministic payment proof the chaser and anomaly agents share.

Only rows for this invoice leave a shared bank or statement document. Payment counts as proven
only from a dated, positive bank row naming the invoice's vendor; a manager's "paid" never does.
"""
import json
from datetime import date
from decimal import Decimal
from hashlib import sha256

from contracts.models import SourceKind


def serial(value):
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    return value if isinstance(value, (dict, list, str, int, float, bool, type(None))) else str(value)


def history(rails, case, now):
    return [e for e in rails.events(case.manager_id)
            if e.valid_from <= now and e.learned_at <= now
            and (not e.payload.get('case_id') or e.payload['case_id'] == case.id)
            and (not e.payload.get('vendor_id') or e.payload['vendor_id'] == case.vendor_id)]

def case_docs(rails, case, now):
    result = []
    documents = rails.documents(case.manager_id)
    colliding_vendors = {d.vendor_id for d in documents
                         if d.kind == SourceKind.vendor_invoice
                         and d.data.get('invoice_id') == case.invoice_id
                         and d.vendor_id != case.vendor_id}
    for doc in documents:
        if doc.manager_id != case.manager_id:
            continue
        if doc.vendor_id not in (None, case.vendor_id) and doc.kind not in (
                SourceKind.manager_bank, SourceKind.manager_statement):
            continue
        if doc.dated and doc.dated > now.date():
            continue
        linked = doc.data.get('invoice_id') == case.invoice_id
        rows = doc.data.get('payments', []) + doc.data.get('lines', [])
        selected = [r for r in rows if r.get('invoice_id') == case.invoice_id
                    and r.get('vendor_id') not in colliding_vendors]
        if doc.kind == SourceKind.agreement or linked or selected:
            data = serial(doc)
            # Never return unrelated rows from a shared bank or statement document.
            data['data'] = {k: v for k, v in data['data'].items()
                            if k not in ('payments', 'lines')}
            for field in ('payments', 'lines'):
                if field in doc.data:
                    data['data'][field] = [r for r in doc.data[field] if r in selected]
            if doc.kind in (SourceKind.manager_bank, SourceKind.manager_statement) or (
                    not linked and doc.kind != SourceKind.agreement):
                data['text'] = ''
            if doc.kind in (SourceKind.manager_bank, SourceKind.manager_statement):
                data['data'] = {k: v for k, v in data['data'].items()
                                if k in ('invoice_id', 'payments', 'lines')}
            result.append(data)
    return result

def payment_proof(rails, case, now):
    line = next(l for l in rails.ledger(case.manager_id)
                if l.invoice_id == case.invoice_id and l.vendor_id == case.vendor_id)
    docs = case_docs(rails, case, now)
    linked_bank = [(d, p) for d in docs if d['kind'] == 'manager_bank'
                   for p in d['data'].get('payments', [])]

    def trusted_payment(doc, payment):
        try:
            amount = Decimal(str(payment.get('amount', 0)))
            return (doc.get('vendor_id') in (None, case.vendor_id)
                    and payment.get('vendor_id') == case.vendor_id
                    and amount.is_finite() and amount > 0
                    and date.fromisoformat(payment['on']) <= now.date())
        except (ValueError, TypeError, KeyError, ArithmeticError):
            return False

    payments = [(d, p) for d, p in linked_bank if trusted_payment(d, p)]
    amount = sum((Decimal(str(p['amount'])) for _, p in payments), Decimal(0))
    expected = Decimal(str(line.amount))
    from contracts.models import Document
    from rails.facts import terms_at
    from rails.guards.payments import payable_cents, payment_proof
    typed_docs = [Document.model_validate({**d, 'data': {**d['data'],
        'payments': [p for bank, p in payments if bank['id'] == d['id']]}})
        if d['kind'] == 'manager_bank' else Document.model_validate(d) for d in docs]
    terms = terms_at(history(rails, case, now), case.manager_id,
                     line.invoice_received or now.date(), case.vendor_id) or {}
    invoice = next((d for d in typed_docs if d.kind == SourceKind.vendor_invoice), None)
    payable = payable_cents(line.amount, terms, invoice.data if invoice else {})
    if amount < expected and terms.get('holdback_pct') and not terms.get('holdback_condition'):
        payable = round(line.amount * 100)
    # Shared proof establishes full-invoice provenance. Contractual partial coverage is
    # computed by the same payable helper using only positive dated identified rows.
    shared = payment_proof([case.invoice_id], typed_docs)
    bank_ids = {d['id'] for d, _ in payments}
    full_shared_proof = any(c.source_id in bank_ids for c in shared)
    holdback_coverage = bool(terms and invoice and payable < round(line.amount * 100))
    valid = bool(invoice and (full_shared_proof or holdback_coverage)
                 and amount * 100 >= payable > 0)
    statement = any(d['kind'] == 'manager_statement' and d.get('dated')
                    and payments and d['dated'] >= max(p['on'] for _, p in payments)
                    and d.get('vendor_id') in (None, case.vendor_id)
                    and any(r.get('status') == 'paid'
                            and r.get('vendor_id') in (None, case.vendor_id)
                            and Decimal(str(r.get('amount', 0))) == amount
                            for r in d['data'].get('lines', [])) for d in docs)
    reviews = [e for e in history(rails, case, now)
               if e.invoice_id == case.invoice_id and e.type == 'payment_reviewed']
    findings = reviews[-1].payload.get('findings', []) if reviews else []
    anomalies = [f for f in findings if f.get('code') not in ('statement_missing',)]
    def flag(code, rows):
        if rows:
            anomalies.append({'code': code, 'source_ids': sorted({d['id'] for d, _ in rows})})
    flag('wrong_vendor', [(d, p) for d, p in linked_bank
        if p.get('vendor_id') not in (None, case.vendor_id)
        or d.get('vendor_id') not in (None, case.vendor_id)])
    flag('recipient_unknown', [(d, p) for d, p in linked_bank if not p.get('vendor_id')])
    flag('invalid_payment_details', [(d, p) for d, p in linked_bank
        if p.get('vendor_id') == case.vendor_id and not trusted_payment(d, p)])
    blockers = []
    if any(d.kind == SourceKind.vendor_invoice and d.data.get('invoice_id') == case.invoice_id
           and d.vendor_id != case.vendor_id for d in rails.documents(case.manager_id)):
        blockers.append('invoice_namespace_conflict')
    if not terms:
        blockers.append('terms_missing')
    if amount < expected and terms.get('holdback_pct') and not terms.get('holdback_condition'):
        blockers.append('holdback_unverified')
    if holdback_coverage and amount < expected:
        blockers.append('holdback_pending')
    anomalies.extend({'code': code, 'source_ids': [invoice.id] if invoice else []}
                     for code in blockers)
    if amount > expected:
        anomalies.append({'code': 'overpayment', 'source_ids': [d['id'] for d, _ in payments]})
    if payments and line.due_date and max(p['on'] for _, p in payments) > line.due_date.isoformat():
        anomalies.append({'code': 'late_payment', 'source_ids': [d['id'] for d, _ in payments]})
    fingerprint = sha256(json.dumps({'payments': payments, 'statement': statement,
        'expected': str(expected), 'payable': payable, 'anomalies': anomalies,
        'statements': [d for d in docs if d['kind'] == 'manager_statement']},
        sort_keys=True, default=str).encode()).hexdigest()
    acknowledged = any(e.type == 'human_anomaly_acknowledged'
        and e.invoice_id == case.invoice_id and e.payload.get('case_id') == case.id
        and e.payload.get('proof_fingerprint') == fingerprint
        and e.payload.get('authorized') is True
        and e.valid_from <= now and e.learned_at <= now
        for e in history(rails, case, now))
    return {'proof_fingerprint': fingerprint, 'anomalies_acknowledged': acknowledged,
            'resolution_blockers': blockers, 'required_amount': str(Decimal(payable) / 100), 'valid_bank_proof': valid, 'paid_amount': str(amount),
            'expected_amount': str(expected), 'statement_matches': statement,
            'anomalies': anomalies, 'source_ids': sorted({d['id'] for d, _ in payments}),
            'line': serial(line)}
