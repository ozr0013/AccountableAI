"""HTTP boundary for the durable finance coordinator and controller evidence intake."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from contracts.models import Document
from rails.events.ingest import document_event

from . import api as service
from . import connected

router = APIRouter(prefix='/workflow', tags=['Connected finance workflow'])
Rails = Annotated[Any, Depends(service.rails_dependency)]
Controller = Annotated[str, Depends(service._controller)]


def worker_status() -> dict:
    return {'enabled': service.worker_enabled(), 'last_error': service._last.get('error'),
            'last_run': service._last.get('as_of'), 'delivery': service._last.get('delivery')}


@router.get('/cases')
def list_cases(rails: Rails) -> dict:
    return {'cases': connected.cases(rails, service.MANAGER), 'worker': worker_status()}


@router.post('/tick')
def tick(rails: Rails) -> dict:
    result = service.run_once(rails=rails)
    return {'cases': result['cases'], 'worker': worker_status()}


class EvidenceIn(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    kind: Literal['vendor_invoice', 'manager_bank', 'manager_statement']
    invoice_id: str = Field(pattern=r'^INV-[A-Z]{2}-\d{3,4}$')
    vendor_id: str | None = Field(default=None, min_length=1, max_length=100)
    amount: float = Field(gt=0, le=1_000_000_000, multiple_of=0.01)
    on: date
    invoice_date: date | None = None
    payee: str | None = Field(default=None, min_length=1, max_length=200)
    account: str | None = Field(default=None, pattern=r'^\d{4,17}$')
    routing: str | None = Field(default=None, pattern=r'^\d{9}$')
    bank: str | None = Field(default=None, min_length=1, max_length=200)
    reference: str | None = Field(default=None, min_length=1, max_length=200)
    replaces: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,100}$')


@router.post('/evidence')
def evidence(body: EvidenceIn, rails: Rails, by: Controller) -> dict:
    if body.on > rails.now():
        raise HTTPException(422, 'Evidence cannot be dated beyond the world clock.')
    if body.invoice_date and body.invoice_date > body.on:
        raise HTTPException(422, 'Invoice date cannot be after its receipt date.')
    if body.kind in ('vendor_invoice', 'manager_bank') and not body.vendor_id:
        raise HTTPException(422, 'vendor_id is required for invoice and bank evidence.')
    known_vendors = {event.payload['id'] for event in rails.events()
                     if event.type == 'entity_registered' and event.payload.get('id', '').startswith('v-')}
    if body.kind == 'vendor_invoice' and body.vendor_id not in known_vendors:
        raise HTTPException(422, 'Choose an existing registered vendor_id.')
    payload = body.model_dump(mode='json', exclude_none=True)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]
    doc_id = 'workflow-' + digest
    with service._lock, service.workflow_lock(rails):
        existing = next((line for line in rails.ledger(service.MANAGER)
                         if line.invoice_id == body.invoice_id), None)
        already = next((doc for doc in rails.documents(service.MANAGER) if doc.id == doc_id), None)
        replaced = None
        if body.replaces and not already:
            replaced = next((doc for doc in rails.documents(service.MANAGER)
                             if doc.id == body.replaces), None)
            matches_invoice = replaced and (replaced.data.get('invoice_id') == body.invoice_id
                or any(row.get('invoice_id') == body.invoice_id for row in
                       replaced.data.get('payments', []) + replaced.data.get('lines', [])))
            if replaced is None or replaced.kind != body.kind or not matches_invoice:
                raise HTTPException(409, 'Correction must replace current evidence of the same kind and invoice.')
            if body.kind == 'manager_bank' and sum(p.get('invoice_id') == body.invoice_id
                    for p in replaced.data.get('payments', [])) > 1:
                raise HTTPException(409, 'Multiple payments for this invoice in the source require a full source correction.')
        if body.kind != 'vendor_invoice' and existing is None:
            raise HTTPException(404, 'Unknown invoice; ingest the invoice first.')
        if body.kind == 'vendor_invoice' and existing and not already and not replaced:
            raise HTTPException(409, 'Invoice already exists; use a correction workflow instead of overwriting it.')
        if body.kind == 'manager_bank' and not already and not replaced:
            for prior in rails.documents(service.MANAGER):
                for payment in prior.data.get('payments', []) if prior.kind == 'manager_bank' else []:
                    same_reference = body.reference and payment.get('reference') == body.reference
                    same_unreferenced = (not body.reference and not payment.get('reference')
                        and payment.get('on') == body.on.isoformat() and payment.get('amount') == body.amount)
                    if payment.get('invoice_id') == body.invoice_id and (same_reference or same_unreferenced):
                        raise HTTPException(409, 'Payment already exists; provide replaces to correct its evidence.')
        if body.kind == 'vendor_invoice':
            data = {'invoice_id': body.invoice_id, 'amount': body.amount,
                    'received': body.on.isoformat(), 'invoice_date': (body.invoice_date or body.on).isoformat()}
        elif body.kind == 'manager_bank':
            payment = {key: value for key, value in payload.items() if key not in ('kind', 'invoice_date', 'replaces')}
            preserved = [p for p in replaced.data.get('payments', []) if p.get('invoice_id') != body.invoice_id] if replaced else []
            data = {'invoice_id': body.invoice_id, 'payments': [*preserved, payment]}
        else:
            assert existing is not None
            preserved = [p for p in replaced.data.get('lines', []) if p.get('invoice_id') != body.invoice_id] if replaced else []
            data = {'invoice_id': body.invoice_id, 'lines': [
                *preserved,
                {'invoice_id': body.invoice_id, 'amount': body.amount, 'line': 'L1',
                 'vendor_id': body.vendor_id or existing.vendor_id, 'status': 'paid'}]}
        doc = Document(id=doc_id, kind=body.kind, manager_id=service.MANAGER,
                       vendor_id=None if body.kind in ('manager_bank', 'manager_statement') and replaced else body.vendor_id or (existing.vendor_id if existing else None),
                       dated=body.on, data={**data, 'entered_by': by},
                       text=f'Controller-entered {body.kind} evidence for {body.invoice_id}')
        if not already:
            event = document_event(doc)
            if replaced:
                event = event.model_copy(update={'supersedes': 'ev-doc-' + replaced.id})
            rails.append_event(event)
        result = service.run_once(rails=rails)
    return {'document_id': doc_id, 'cases': result['cases']}


class DeliveryIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reference: str = Field(min_length=1, max_length=500)


@router.post('/outbox/{outbox_id}/ack')
def acknowledge(outbox_id: str, body: DeliveryIn, rails: Rails, by: Controller) -> dict:
    if not body.reference.strip():
        raise HTTPException(422, 'A delivery reference is required.')
    with service._lock, service.workflow_lock(rails):
        try:
            result = connected.acknowledge(rails, service._mirror(), outbox_id,
                                           body.reference.strip(), by, service.MANAGER)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, 'Unknown outbox item.') from exc
    return result


class ResolveIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    note: str = Field(min_length=1, max_length=1000)


@router.post('/cases/{invoice_id}/resolve')
def resolve_case(invoice_id: str, body: ResolveIn, rails: Rails, by: Controller) -> dict:
    if not body.note.strip():
        raise HTTPException(422, 'A resolution note is required.')
    with service._lock, service.workflow_lock(rails):
        try:
            return connected.resolve(rails, invoice_id, body.note.strip(), by,
                                     service._mirror(), service.MANAGER)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, 'Unknown workflow case.') from exc
