from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from workflow.autonomy import AutonomyRuntime
from workflow.cases import CaseStore
from workflow.memrails import MemoryRails
from workflow.scheduler import Scheduler

NOW = datetime(2026, 4, 22, 12, tzinfo=UTC)


class WaitingClient:
    def __init__(self):
        self.messages = self
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(content=[{'type': 'tool_use', 'id': 'wait', 'name': 'wait',
            'input': {'until': (NOW + timedelta(days=1)).isoformat(), 'reason': 'Check tomorrow'}}])


def test_native_case_wakes_from_durable_job_and_deduplicates_poll():
    rails = MemoryRails.from_world((3,), n_clean=0)
    client = WaitingClient()
    runtime = AutonomyRuntime(rails, client=client)
    assert runtime.tick(NOW)
    assert client.calls > 0
    before = client.calls
    assert runtime.tick(NOW) == []
    assert client.calls == before
    assert all(c.next_wake_at for c in CaseStore(rails).list())
    assert any(j['status'] == 'pending' for j in Scheduler(rails).list())
    restored = MemoryRails(rails.events())
    assert AutonomyRuntime(restored, client=client).tick(NOW) == []


def test_resolving_case_does_not_cancel_its_running_job():
    from agents.supervisor import Supervisor
    from contracts.models import Document
    from rails.events.ingest import document_event
    rails = MemoryRails.from_world((3,), n_clean=0)
    line = rails.ledger('keyway')[0]
    case = CaseStore(rails).ensure(line, NOW)
    for doc in [Document(id='resolve-bank',kind='manager_bank',manager_id=case.manager_id,
                         dated=NOW.date(),data={'payments':[{'invoice_id':case.invoice_id,
                         'vendor_id':case.vendor_id,'amount':line.amount,'on':line.due_date.isoformat()}]}),
                Document(id='resolve-statement',kind='manager_statement',manager_id=case.manager_id,
                         dated=NOW.date(),data={'lines':[{'invoice_id':case.invoice_id,
                         'vendor_id':case.vendor_id,'amount':line.amount,'status':'paid'}]})]:
        rails.append_event(document_event(doc,NOW))
    assert Supervisor(rails)._proof(case,NOW)['valid_bank_proof']
    class Resolver:
        def __init__(self): self.messages = self
        def create(self, **kwargs):
            return SimpleNamespace(content=[{'type':'tool_use','id':'resolve','name':'resolve','input':{'reason':'Proof reconciled'}}])
    results = AutonomyRuntime(rails, client=Resolver()).tick(NOW)
    assert all(r['status'] != 'retry_scheduled' for r in results)
    assert CaseStore(rails).get(case.id).state == 'resolved'
