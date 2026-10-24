"""Deterministic payment identity and contractual payable amount checks."""
import re

from contracts.models import Citation, SourceKind
from rails.matcher import gather_evidence


def payable_cents(amount, terms, invoice_data):
    """Unknown release conditions never authorize retaining money."""
    get = terms.get if isinstance(terms, dict) else lambda k, default=None: getattr(terms, k, default)
    pct = get('holdback_pct') or 0
    condition = get('holdback_condition')
    pct = getattr(pct, 'value', pct)
    condition = getattr(condition, 'value', condition)
    # Generated legacy worlds omitted the inspection condition from scalar confirmed terms.
    if condition is None and isinstance(terms, dict) and 'terms' not in terms:
        condition = 'until the work passes inspection'
    pending = inspection_condition(condition) and invoice_data.get('inspection') == 'pending'
    hold = round(round(amount * 100) * float(pct) / 100) if pending else 0
    return round(amount * 100) - hold


def payment_proof(invoice_ids, docs):
    """Only identified recipients and sufficient amounts prove a whole invoice paid."""
    result = gather_evidence(docs)
    out = {}
    for doc in docs:
        if doc.kind != SourceKind.vendor_invoice or doc.data.get('invoice_id') not in invoice_ids:
            continue
        iid = doc.data['invoice_id']
        # Unknown recipients remain useful reconciliation evidence but cannot stop chasing.
        bank = [(bank, p) for matched_iid, did, p in result.matched_payments
                for bank in docs if bank.id == did and matched_iid == iid
                and p.get('vendor_id') == doc.vendor_id and bank.manager_id == doc.manager_id]
        bank += [(bank, p) for bank in docs if bank.kind == SourceKind.manager_bank
                 and bank.manager_id == doc.manager_id for p in bank.data.get('payments', [])
                 if p.get('invoice_id') == iid and p.get('vendor_id') == doc.vendor_id]
        unique = {(bank.id, str(p)): (bank, p) for bank, p in bank}
        if sum(round(p['amount'] * 100) for _, p in unique.values()) >= round(doc.data['amount'] * 100):
            for bank, _ in unique.values():
                out[bank.id] = Citation(source_id=bank.id, kind=bank.kind)
        for confirmation in docs:
            if (confirmation.kind == SourceKind.vendor_confirmation
                    and confirmation.manager_id == doc.manager_id
                    and confirmation.vendor_id == doc.vendor_id
                    and confirmation.data.get('invoice_id') == iid
                    and confirmation.data.get('status') == 'paid'
                    and round(confirmation.data.get('amount', 0) * 100) >= round(doc.data['amount'] * 100)):
                out[confirmation.id] = Citation(source_id=confirmation.id, kind=confirmation.kind)
    return list(out.values())


def identified_payment_documents(docs):
    """Remove unidentified/mismatched payees before trusted ledger/trail calculations."""
    invoices = {d.data.get('invoice_id'): d for d in docs if d.kind == SourceKind.vendor_invoice}
    out = []
    for doc in docs:
        if doc.kind == SourceKind.manager_bank:
            payments = []
            for payment in doc.data.get('payments', []):
                invoice = invoices.get(payment.get('invoice_id'))
                if not payment.get('vendor_id'):
                    continue
                if invoice and (payment['vendor_id'] != invoice.vendor_id or doc.manager_id != invoice.manager_id):
                    continue
                payments.append(payment)
            doc = doc.model_copy(update={'data': {**doc.data, 'payments': payments}})
        elif doc.kind == SourceKind.vendor_confirmation:
            invoice = invoices.get(doc.data.get('invoice_id'))
            if not invoice or doc.vendor_id != invoice.vendor_id or doc.manager_id != invoice.manager_id:
                continue
        out.append(doc)
    return out


def inspection_condition(condition):
    """Only supported inspection-release wording permits the inspection evidence shortcut."""
    return bool(condition and re.fullmatch(
        r"(?:until (?:the work passes inspection|inspection (?:passes|is complete|is completed))|pending inspection)",
        str(condition).strip().lower().rstrip('.')))
