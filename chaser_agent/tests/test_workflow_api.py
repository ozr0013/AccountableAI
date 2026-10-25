"""Agreement API -> saved rules -> chaser API, without touching shared SpacetimeDB."""
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from agreement_agent import api as agreements
from agreement_agent.tests.test_controller import FakeSpacetime
from chaser_agent import api as chaser
from chaser_agent.tests.test_api import Mirror
from contracts.models import Document
from rails.events.ingest import document_event
from workflow.memrails import MemoryRails


class SharedMirror(FakeSpacetime):
    def __init__(self):
        super().__init__()
        self.chases=Mirror()

    def call(self, reducer, args):
        if reducer in ('record_chase','decide_chase'):
            return self.chases.call(reducer,args)
        result=super().call(reducer,args)
        if reducer == "set_agreement_rules":
            self.rules[args[0]]=[{**row,"vendor_name":args[1]} for row in self.rules[args[0]]]
        return result

    def query(self, sql):
        if 'chase_' in sql: return self.chases.query(sql)
        if 'agreement_rule' in sql and 'WHERE' not in sql:
            return [rule for rules in self.rules.values() for rule in rules]
        return super().query(sql)


def test_uploaded_agreement_controls_chasing_and_payment_handoff(monkeypatch):
    monkeypatch.setattr(agreements, 'wake_workflow', lambda: {'triggered': False})
    mirror=SharedMirror()
    rails=MemoryRails.from_world((3,),n_clean=0)
    rails.now=lambda:date(2026,4,27)
    monkeypatch.setattr(agreements,'_mirror',lambda:mirror)
    monkeypatch.setattr(chaser,'_mirror',lambda:mirror)
    monkeypatch.setattr(chaser,'_rails',lambda:rails)
    a,c=TestClient(agreements.app),TestClient(chaser.app)
    sample=Path('agreement_agent/samples/rio_grande_services_agreement.pdf').read_bytes()
    upload=a.post('/review?model=false',files={'file':('rio.pdf',sample,'application/pdf')})
    assert upload.status_code==200,upload.text
    assert upload.json()['saved_to_spacetime']
    assert upload.json()['status']=='active'
    detail=c.get('/chaser/invoices/INV-KW-0301').json()
    assert detail['chaser_today']['due_date']=='2026-04-02'
    assert c.post('/chaser/preview?as_of=2026-03-30').json()['decisions'][0]['action']=='not_due'
    assert mirror.chases.actions=={}
    rails.now=lambda:date(2026,4,22)
    polite=c.post('/chaser/run?as_of=2026-04-22').json()['decisions'][0]
    assert polite['level']=='polite' and polite['status']=='queued'
    case=c.get('/workflow/cases').json()['cases'][0]
    outbox=next(o for o in case['outbox'] if o['kind']=='chase_reminder' and o['status']=='queued')
    assert c.post(f"/workflow/outbox/{outbox['id']}/ack",json={'reference':'test-delivery'}).status_code==200
    rails.now=lambda:date(2026,4,27)
    firm=c.post('/chaser/run?as_of=2026-04-27').json()['decisions'][0]
    assert firm['level']=='firm' and firm['status']=='needs_approval'
    route='/chaser/actions/'+firm['action_id']+'/decide'
    approved=c.post(route,json={'approve':True,'note':'Evidence reviewed'})
    assert approved.status_code==200,approved.text
    assert approved.json()['status']=='approved'
    bank=Document(id='workflow-bank-proof',kind='manager_bank',manager_id='keyway',
        dated=date(2026,4,29),data={'payments':[{'invoice_id':'INV-KW-0301',
        'vendor_id':'v-riogrande','amount':1240,'on':'2026-04-29'}]})
    rails.append_event(document_event(bank))
    rails.now=lambda:date(2026,4,30)
    handoff=c.post('/chaser/run').json()['decisions'][0]
    assert handoff['action']=='handoff_anomaly'
    # The branch records a handoff; this test does not pretend an anomaly consumer exists.
    assert handoff['status']=='recorded'
    history=c.get('/chaser/actions?invoice_id=INV-KW-0301').json()
    assert any(row['action']=='handoff_anomaly' for row in history)
    assert c.post('/chaser/run').json()['saved']==0
