from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient

from agreement_agent import api
from agreement_agent.tests.test_controller import FakeSpacetime


def test_review_and_confirmation_wake_connected_coordinator(monkeypatch):
    mirror=FakeSpacetime()
    wake=Mock(return_value={'triggered': True})
    monkeypatch.setattr(api,'_mirror',lambda:mirror)
    monkeypatch.setattr(api,'wake_workflow',wake,raising=False)
    monkeypatch.setenv('CONTROLLER_API_TOKEN','test')
    client=TestClient(api.app)
    pdf=Path('agreement_agent/samples/greenedge_landscaping_agreement.pdf').read_bytes()
    out=client.post('/review?model=false',files={'file':('agreement.pdf',pdf,'application/pdf')})
    assert out.status_code==200
    assert wake.call_count==1
    assert out.json()['workflow']['triggered']
    doc=out.json()['review']['doc_id']
    response=client.post(f'/agreements/{doc}/confirm',headers={'Authorization':'Bearer test'},
        json={'values':{'payment_due':'within 15 days of invoice receipt',
                       'payment_info':'ACH to Desert Bank, routing 123456789, account 99887766'}})
    assert response.status_code==200,response.text
    assert wake.call_count==2
