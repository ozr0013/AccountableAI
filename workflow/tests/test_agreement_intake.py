from datetime import UTC, datetime

from fastapi.testclient import TestClient

from contracts.models import Document
from rails.events.ingest import document_event
from rails.facts import terms_at
from workflow.agreement_intake import propose_agreements
from workflow.autonomy_api import create_app
from workflow.cases import CaseStore
from workflow.human import HumanApprovals
from workflow.memrails import MemoryRails

NOW = datetime(2026,4,22,12,tzinfo=UTC)


def test_agreement_proposal_is_durable_and_only_exact_human_confirmation_binds(monkeypatch):
    r = MemoryRails.from_world((3,),n_clean=0)
    case = CaseStore(r).ensure(r.ledger('keyway')[0],NOW)
    doc = Document(id='agreement-amended',kind='agreement',manager_id='keyway',dated=NOW.date(),
        text='7.2 Manager shall pay each vendor invoice within fifteen (15) days of receipt of a complete invoice.\n8.1 Owner approval is required above $5,000.')
    r.append_event(document_event(doc,NOW))
    propose_agreements(r,[case],NOW)
    tickets = [t for t in HumanApprovals(r).list(case.id) if t['kind']=='agreement_terms' and t['payload']['source_id']==doc.id]
    assert len(tickets)==1
    assert terms_at(r.events(),'keyway',NOW.date())['vendor_payment_days'] == 10
    monkeypatch.setenv('CONTROLLER_API_TOKEN','test-intake')
    api = TestClient(create_app(r,clock=lambda:NOW))
    ticket=tickets[0]
    response=api.post('/autonomy/approvals/'+ticket['id']+'/decide',headers={'Authorization':'Bearer test-intake'},json={'approve':True,'digest':ticket['digest']})
    assert response.status_code==200,response.text
    assert terms_at(r.events(),'keyway',NOW.date())['vendor_payment_days']==15
    replay=api.post('/autonomy/approvals/'+ticket['id']+'/decide',headers={'Authorization':'Bearer test-intake'},json={'approve':True,'digest':ticket['digest']})
    assert replay.status_code==200,replay.text


def test_agreement_intake_can_precede_first_invoice():
    r = MemoryRails()
    doc=Document(id='first-agreement',kind='agreement',manager_id='new-manager',dated=NOW.date(),
        text='7.2 Manager shall pay each vendor invoice within fifteen (15) days of receipt of a complete invoice.\n8.1 Owner approval is required above $5,000.')
    r.append_event(document_event(doc,NOW))
    propose_agreements(r,[],NOW)
    case=CaseStore(r).list()[0]
    assert case.kind=='agreement'
    assert HumanApprovals(r).list(case.id)[0]['kind']=='agreement_terms'
    assert terms_at(r.events(),'new-manager',NOW.date()) is None
