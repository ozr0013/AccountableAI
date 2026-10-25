"""HTTP contracts used by the live dashboard; fixtures never touch shared databases."""
from datetime import date
from types import SimpleNamespace
from urllib.error import URLError

import pytest
from fastapi.testclient import TestClient

from chaser_agent import api
from contracts.models import Document
from rails.events.ingest import document_event
from workflow.memrails import MemoryRails

COLUMNS = ('action_id','invoice_id','vendor_name','action','level','due_date','days_overdue',
           'amount_owed','message','reason','rule_id','rule_quote','status','as_of')
RULE = {'rule_id':'agreement:deadline','vendor_name':'Rio Grande Plumbing LLC',
        'rule_type':'pay_within_days','params':'{"days":30,"basis":"calendar"}',
        'status':'active','check':'Pay within30days','source_quote':'within thirty days'}
AUTH = {'Authorization':'Bearer test-controller'}


def plain(value):
    return value.get('some') if isinstance(value,dict) and set(value) <= {'some','none'} else value


class Mirror:
    def __init__(self):
        self.rules = [dict(RULE)]
        self.actions = {}
        self.decisions = {}
        self.calls = []

    def query(self, sql):
        if 'agreement_rule' in sql: return self.rules
        if 'chase_decision' in sql: return list(self.decisions.values())
        if 'chase_action' in sql: return list(self.actions.values())
        return []

    def call(self, reducer, args):
        self.calls.append((reducer,args))
        if reducer == 'record_chase':
            self.actions.setdefault(args[0],dict(zip(COLUMNS,map(plain,args),strict=True)))
        elif reducer == 'decide_chase':
            self.decisions[args[0]] = dict(zip(('action_id','status','decided_by','note'),map(plain,args),strict=True))


@pytest.fixture
def fixture(monkeypatch):
    rails=MemoryRails.from_world((3,),n_clean=0)
    rails.now=lambda:date(2026,4,27)
    mirror=Mirror()
    monkeypatch.setattr(api,'_rails',lambda:rails)
    monkeypatch.setattr(api,'_mirror',lambda:mirror)
    monkeypatch.setenv('CONTROLLER_API_TOKEN','test-controller')
    api._last.clear()
    return rails,mirror,TestClient(api.app)


def test_invoice_list_detail_and_validation(fixture):
    _,_,client=fixture
    response=client.get('/chaser/invoices?status=all&limit=50')
    assert response.status_code==200,response.text
    assert response.json()['count']>=1
    detail=client.get('/chaser/invoices/INV-KW-0301')
    assert detail.status_code==200,detail.text
    assert detail.json()['chaser_today']['due_date']=='2026-04-02'
    assert client.get('/chaser/invoices/INV-KW-9999').status_code==404
    assert client.get('/chaser/invoices?limit=500').status_code==422
    assert client.get('/chaser/invoices?status=imaginary').status_code==422


def test_preview_does_not_write_and_run_is_idempotent(fixture):
    rails,mirror,client=fixture
    rails.now=lambda:date(2026,4,22)
    out=client.post('/chaser/preview?as_of=2026-04-22')
    assert out.status_code==200,out.text
    assert out.json()['as_of']=='2026-04-22'
    assert out.json()['decisions'][0]['action']=='reminder'
    assert mirror.calls==[]
    assert client.post('/chaser/run?as_of=2026-04-22').status_code==200
    count=len(mirror.actions)
    assert client.post('/chaser/run?as_of=2026-04-22').status_code==200
    assert len(mirror.actions)==count
    assert client.post('/chaser/preview?as_of=invalid').status_code==422


def firm(client):
    rails=api._rails()
    rails.now=lambda:date(2026,4,22)
    client.post('/chaser/run?as_of=2026-04-22')
    case=client.get('/workflow/cases').json()['cases'][0]
    outbox=next(o for o in case['outbox'] if o['kind']=='chase_reminder' and o['status']=='queued')
    assert client.post(f"/workflow/outbox/{outbox['id']}/ack",json={'reference':'test-delivery'},headers=AUTH).status_code==200
    rails.now=lambda:date(2026,4,27)
    out=client.post('/chaser/run?as_of=2026-04-27').json()
    return next(d for d in out['decisions'] if d['level']=='firm')


def test_controller_decides_exact_action_and_repeat_is_blocked(fixture):
    _,_,client=fixture
    action=firm(client)
    route=f"/chaser/actions/{action['action_id']}/decide"
    assert client.post(route,json={'approve':True}).status_code==401
    approved=client.post(route,json={'approve':True,'note':'Reviewed'},headers=AUTH)
    assert approved.status_code==200,approved.text
    assert approved.json()['status']=='approved'
    assert client.post(route,json={'approve':True},headers=AUTH).status_code==409


def test_payment_arriving_blocks_stale_approval_but_allows_rejection(fixture):
    rails,_,client=fixture
    action=firm(client)
    bank=Document(id='payment-after-draft',kind='manager_bank',manager_id='keyway',
                  dated=date(2026,4,29),data={'payments':[{'invoice_id':'INV-KW-0301',
                  'vendor_id':'v-riogrande','amount':1240,'on':'2026-04-29'}]})
    rails.append_event(document_event(bank))
    route=f"/chaser/actions/{action['action_id']}/decide"
    response=client.post(route,json={'approve':True},headers=AUTH)
    assert response.status_code==409,response.text
    assert client.post(route,json={'approve':False,'note':'Payment found'},headers=AUTH).status_code==200


def test_dependency_failure_is_structured_for_dashboard(fixture,monkeypatch):
    _,_,client=fixture
    def unavailable(): raise URLError('local database offline')
    monkeypatch.setattr(api,'_mirror',unavailable)
    response=client.get('/chaser/actions')
    assert response.status_code==502,response.text
    assert 'unavailable' in response.json()['detail'].lower()


def test_owned_postgres_connection_is_closed(fixture,monkeypatch):
    rails,_,client=fixture
    closed=[]
    class OwnedRails:
        store=SimpleNamespace(conn=SimpleNamespace(close=lambda:closed.append(True)))
        def __getattr__(self, name):
            return getattr(rails,name)
    monkeypatch.setattr(api,'_rails',OwnedRails)
    assert client.get('/chaser/invoices').status_code==200
    assert closed==[True]


def test_changed_cadence_invalidates_pending_reminder(fixture):
    _,mirror,client=fixture
    action=firm(client)
    mirror.rules.append({**RULE,'rule_id':'agreement:cadence','rule_type':'reminder_cadence',
                         'params':'{"days":365}','check':'Wait365days'})
    response=client.post(f"/chaser/actions/{action['action_id']}/decide",json={'approve':True},headers=AUTH)
    assert response.status_code==409,response.text


def test_future_draft_cannot_be_approved_before_its_date(fixture):
    rails,_,client=fixture
    action=firm(client)
    rails.now=lambda:date(2026,4,22)
    response=client.post(f"/chaser/actions/{action['action_id']}/decide",json={'approve':True},headers=AUTH)
    assert response.status_code==409,response.text


def test_still_eligible_draft_can_be_approved_later(fixture):
    rails,_,client=fixture
    action=firm(client)
    rails.now=lambda:date(2026,4,30)
    response=client.post(f"/chaser/actions/{action['action_id']}/decide",json={'approve':True},headers=AUTH)
    assert response.status_code==200,response.text


def test_future_dates_are_preview_only_and_cannot_suppress_todays_chasing(fixture):
    _,mirror,client=fixture
    response=client.post('/chaser/run?as_of=2026-06-01')
    assert response.status_code==422,response.text
    assert mirror.calls==[]
    assert client.post('/chaser/preview?as_of=2026-06-01').status_code==200
    assert mirror.calls==[]


def test_repeated_handoff_does_not_report_existing_records_as_new_saves(fixture):
    rails,mirror,client=fixture
    bank=Document(id='paid-handoff',kind='manager_bank',manager_id='keyway',
        dated=date(2026,4,27),data={'payments':[{'invoice_id':'INV-KW-0301',
        'vendor_id':'v-riogrande','amount':1000,'on':'2026-04-27'}]})
    rails.append_event(document_event(bank))
    first=client.post('/chaser/run').json()
    assert first['saved']==1
    second=client.post('/chaser/run').json()
    assert second['saved']==0
    assert len(mirror.actions)==1
