"""A native-tool investigation survives PostgreSQL and process restarts."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from contracts.models import Document
from rails.events.ingest import document_event
from rails.real import RealRails
from workflow.autonomy import AutonomyRuntime
from workflow.autonomy_api import create_app
from workflow.cases import CaseStore
from workflow.human import HumanApprovals
from workflow.outbox import Outbox
from workflow.scheduler import Scheduler
from world import generate

NOW = datetime(2026, 4, 22, 12, tzinfo=UTC)


class ScriptedClient:
    def __init__(self):
        self.messages = self
        self.choices = [('read', {'kind': 'invoice'}),
                        ('request', {'kind': 'statement', 'reason': 'Need supporting statement'}),
                        ('wait', {'until': (NOW + timedelta(days=1)).isoformat(), 'reason': 'Await evidence'})]
        self.calls = 0

    def create(self, **kwargs):
        name, args = self.choices[self.calls % len(self.choices)]
        self.calls += 1
        return SimpleNamespace(content=[{'type':'tool_use','id':str(self.calls),'name':name,'input':args}])


def test_postgres_native_triggers_human_and_outbox_restart(schema, monkeypatch):
    rails = RealRails(schema)
    for event in generate(13, (3,), 0).events():
        rails.append_event(event)
    runtime = AutonomyRuntime(rails, client=ScriptedClient())
    assert runtime.tick(NOW)
    case = CaseStore(rails).list()[0]
    ticket = HumanApprovals(rails).list(case.id)[0]
    restarted = RealRails(schema)
    assert CaseStore(restarted).get(case.id).next_wake_at
    assert AutonomyRuntime(restarted, client=ScriptedClient()).tick(NOW) == []
    monkeypatch.setenv('CONTROLLER_API_TOKEN', 'test-controller-token')
    api = TestClient(create_app(restarted, clock=lambda: NOW))
    result = api.post('/autonomy/approvals/' + ticket['id'] + '/decide',
        headers={'Authorization':'Bearer test-controller-token'},
        json={'approve':True,'digest':ticket['digest']})
    assert result.status_code == 200, result.text
    assert Outbox(restarted).list(case.id)[0]['status'] == 'queued'
    assert any(j['status'] == 'pending' and j['due_at'] == NOW for j in Scheduler(restarted).list(case.id))
    doc = Document(id='runtime-new-payment', kind='manager_bank', manager_id=case.manager_id,
        dated=NOW.date(), data={'payments':[{'invoice_id':case.invoice_id,'vendor_id':case.vendor_id,
                                         'amount':1240,'on':NOW.date().isoformat()}]})
    restarted.append_event(document_event(doc, NOW))
    AutonomyRuntime(restarted, client=ScriptedClient()).sync(NOW)
    assert CaseStore(restarted).get(case.id).last_observation != case.last_observation
    rails.store.conn.close()
    restarted.store.conn.close()


def test_postgres_resolution_completes_own_lease(schema):
    rails = RealRails(schema)
    for event in generate(13, (3,), 0).events():
        rails.append_event(event)
    line = rails.ledger('keyway')[0]
    case = CaseStore(rails).ensure(line,NOW)
    for doc in [Document(id='resolve-bank',kind='manager_bank',manager_id=case.manager_id,
                         dated=NOW.date(),data={'payments':[{'invoice_id':case.invoice_id,
                         'vendor_id':case.vendor_id,'amount':line.amount,'on':line.due_date.isoformat()}]}),
                Document(id='resolve-statement',kind='manager_statement',manager_id=case.manager_id,
                         dated=NOW.date(),data={'lines':[{'invoice_id':case.invoice_id,
                         'vendor_id':case.vendor_id,'amount':line.amount,'status':'paid'}]})]:
        rails.append_event(document_event(doc,NOW))
    class Resolver:
        def __init__(self): self.messages = self
        def create(self, **kwargs):
            return SimpleNamespace(content=[{'type':'tool_use','id':'resolve','name':'resolve','input':{'reason':'Proof reconciled'}}])
    results = AutonomyRuntime(rails,client=Resolver()).tick(NOW)
    assert all(r['status'] != 'retry_scheduled' for r in results)
    assert CaseStore(rails).get(case.id).state == 'resolved'
    assert not any(j['status'] == 'leased' for j in Scheduler(rails).list(case.id))
    rails.store.conn.close()
