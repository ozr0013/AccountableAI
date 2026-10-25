"""Connected HTTP intake changes ledger and advances the same durable cases."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from chaser_agent import api
from chaser_agent.tests.test_api import Mirror
from workflow.memrails import MemoryRails


@pytest.fixture
def connected(monkeypatch):
    rails = MemoryRails.from_world((3,), n_clean=0)
    rails.now = lambda: date(2026, 4, 27)
    mirror = Mirror()
    monkeypatch.setattr(api, '_rails', lambda: rails)
    monkeypatch.setattr(api, '_mirror', lambda: mirror)
    return rails, mirror, TestClient(api.app)


def test_evidence_requires_controller_and_ingests_invoice(connected):
    rails, _, client = connected
    body = {'kind':'vendor_invoice','invoice_id':'INV-KW-9998','vendor_id':'v-riogrande',
            'amount':400,'on':'2026-04-01'}
    response = client.post('/workflow/evidence', json=body)
    assert response.status_code == 200, response.text
    assert any(line.invoice_id == body['invoice_id'] for line in rails.ledger('keyway'))
    assert any(case['invoice_id'] == body['invoice_id'] for case in response.json()['cases'])
    count = len(rails.events())
    assert client.post('/workflow/evidence', json=body).status_code == 200
    assert len(rails.events()) == count


def test_bank_and_statement_intake_advances_case_without_manual_tick(connected):
    _, _, client = connected
    bank = {'kind':'manager_bank','invoice_id':'INV-KW-0301','vendor_id':'v-riogrande',
            'amount':1240,'on':'2026-04-02','payee':'Rio Grande Plumbing LLC'}
    response = client.post('/workflow/evidence', json=bank)
    assert response.status_code == 200, response.text
    case = next(row for row in response.json()['cases'] if row['invoice_id'] == bank['invoice_id'])
    assert case['state'] != 'chasing'
    assert any(row['kind'] == 'request_statement' for row in case['outbox'])
    statement = {'kind':'manager_statement','invoice_id':bank['invoice_id'],'amount':1240,
                 'on':'2026-04-03'}
    response = client.post('/workflow/evidence', json=statement)
    assert response.status_code == 200, response.text
    case = next(row for row in response.json()['cases'] if row['invoice_id'] == bank['invoice_id'])
    assert case['state'] == 'closed', case
    assert client.get('/workflow/cases').json()['cases']


def test_rejects_unknown_invoice_vendor_future_and_duplicate_conflict(connected):
    _, _, client = connected
    body = {'kind':'vendor_invoice','invoice_id':'INV-KW-9998','vendor_id':'v-missing',
            'amount':400,'on':'2026-04-01'}
    assert client.post('/workflow/evidence',json=body).status_code == 422
    body['vendor_id']='v-riogrande'
    body['on']='2099-01-01'
    assert client.post('/workflow/evidence',json=body).status_code == 422
    body.update(kind='manager_bank', on='2026-04-01')
    assert client.post('/workflow/evidence',json=body).status_code == 404
    body.update(kind='vendor_invoice')
    assert client.post('/workflow/evidence',json=body).status_code == 200
    body['amount']=401
    assert client.post('/workflow/evidence',json=body).status_code == 409


def test_tick_is_connected_and_requires_no_external_mail(connected):
    _, _, client = connected
    response = client.post('/workflow/tick',json={})
    assert response.status_code == 200, response.text
    assert response.json()['cases']
    assert 'worker' in response.json()


def test_bank_correction_supersedes_instead_of_double_counting(connected):
    rails, _, client = connected
    body={'kind':'manager_bank','invoice_id':'INV-KW-0301','vendor_id':'v-riogrande',
          'amount':1240,'on':'2026-04-02','reference':'bank-ref-1'}
    first=client.post('/workflow/evidence',json=body)
    assert first.status_code==200
    body['payee']='Rio Grande Plumbing LLC'
    assert client.post('/workflow/evidence',json=body).status_code==409
    body['replaces']=first.json()['document_id']
    fixed=client.post('/workflow/evidence',json=body)
    assert fixed.status_code==200,fixed.text
    assert next(line for line in rails.ledger('keyway') if line.invoice_id==body['invoice_id']).paid_amount==1240
    assert client.post('/workflow/evidence',json=body).status_code==200


def test_queued_is_not_delivered_and_cadence_starts_at_receipt(connected):
    rails, _, client=connected
    rails.now=lambda:date(2026,4,22)
    first=client.post('/workflow/tick').json()
    case=next(c for c in first['cases'] if c['invoice_id']=='INV-KW-0301')
    outbox=next(o for o in case['outbox'] if o['kind']=='chase_reminder')
    rails.now=lambda:date(2026,4,27)
    waiting=client.post('/chaser/run').json()['decisions'][0]
    assert waiting['action']=='wait' and 'delivery' in waiting['reason']
    response=client.post(f"/workflow/outbox/{outbox['id']}/ack",
                          json={'reference':'external-message-1'})
    assert response.status_code==200,response.text
    assert client.post('/chaser/run').json()['decisions'][0]['action']=='wait'
    rails.now=lambda:date(2026,5,2)
    assert client.post('/chaser/run').json()['decisions'][0]['level']=='firm'


def test_historical_run_cannot_rewrite_current_cases(connected):
    _,_,client=connected
    client.post('/workflow/tick')
    before=client.get('/workflow/cases').json()['cases']
    response=client.post('/chaser/run?as_of=2026-03-01')
    assert response.status_code==422
    assert client.get('/workflow/cases').json()['cases']==before


def test_changed_rules_cancel_old_message_and_allow_fresh_draft(connected):
    _,mirror,client=connected
    first=client.post('/chaser/run').json()['decisions'][0]
    mirror.rules[0]['params']='{"days":20,"basis":"calendar"}'
    second=client.post('/chaser/run').json()['decisions'][0]
    assert second['action']=='reminder',second
    assert second['action_id']!=first['action_id']
    case=client.get('/workflow/cases').json()['cases'][0]
    statuses={o['action_id']:o['status'] for o in case['outbox'] if o.get('action_id')}
    assert statuses[first['action_id']]=='cancelled'
    assert statuses[second['action_id']]=='queued'


def test_disputed_and_fixed_price_conflict_do_not_queue_communications(connected):
    rails,mirror,client=connected
    from contracts.models import Document
    from rails.events.ingest import document_event
    source=next(d for d in rails.documents('keyway') if d.kind=='vendor_invoice' and d.data.get('invoice_id')=='INV-KW-0301')
    updated=Document(**{**source.model_dump(),'id':'disputed-correction', 'data':{**source.data,'disputed':True}})
    rails.append_event(document_event(updated).model_copy(update={'supersedes':'ev-doc-'+source.id}))
    mirror.rules[0]['params']='{"days":30,"basis":"calendar","undisputed_only":true}'
    case=client.post('/workflow/tick').json()['cases'][0]
    assert case['state']=='needs_controller'
    assert not any(o['kind']=='chase_reminder' and o['status']=='queued' for o in case['outbox'])


def test_native_multi_invoice_bank_correction_preserves_other_rows(connected):
    rails,_,client=connected
    from contracts.models import Document
    from rails.events.ingest import document_event
    native=Document(id='native-multi-bank',kind='manager_bank',manager_id='keyway',
        dated=date(2026,4,2),data={'payments':[
        {'invoice_id':'INV-KW-0301','vendor_id':'v-riogrande','amount':1240,'on':'2026-04-02'},
        {'invoice_id':'INV-KW-9997','vendor_id':'v-other','amount':99,'on':'2026-04-02'}]})
    rails.append_event(document_event(native))
    body={'kind':'manager_bank','invoice_id':'INV-KW-0301','vendor_id':'v-riogrande',
        'amount':1240,'on':'2026-04-02','payee':'Rio Grande Plumbing LLC','replaces':native.id}
    response=client.post('/workflow/evidence',json=body)
    assert response.status_code==200,response.text
    corrected=next(d for d in rails.documents('keyway') if d.id==response.json()['document_id'])
    assert any(p['invoice_id']=='INV-KW-9997' for p in corrected.data['payments'])


def test_multi_vendor_statement_correction_keeps_envelope_vendor_neutral(connected):
    rails,_,client=connected
    from contracts.models import Document
    from rails.events.ingest import document_event
    native=Document(id='native-multi-statement',kind='manager_statement',manager_id='keyway',
        dated=date(2026,4,2),data={'lines':[
        {'invoice_id':'INV-KW-0301','vendor_id':'v-riogrande','amount':1240,'status':'paid'},
        {'invoice_id':'INV-KW-9997','vendor_id':'v-other','amount':99,'status':'paid'}]})
    rails.append_event(document_event(native))
    body={'kind':'manager_statement','invoice_id':'INV-KW-0301','vendor_id':'v-riogrande',
        'amount':1240,'on':'2026-04-02','replaces':native.id}
    response=client.post('/workflow/evidence',json=body)
    assert response.status_code==200,response.text
    corrected=next(d for d in rails.documents('keyway') if d.id==response.json()['document_id'])
    assert corrected.vendor_id is None
    assert any(row['vendor_id']=='v-other' for row in corrected.data['lines'])
