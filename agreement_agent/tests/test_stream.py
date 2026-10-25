from pathlib import Path

from langchain_core.messages import AIMessageChunk

from agreement_agent.graph import review
from agreement_agent.models import SourceDoc
from chaser_agent import runtime


class Streamed:
    def stream(self,msgs):
        yield AIMessageChunk(content=[{'type':'thinking','thinking':'Check the document before recording any terms.'}])
        yield AIMessageChunk(content='',tool_call_chunks=[{'name':'read_header','args':'{}','id':'read-1','index':0}])


def test_agreement_broadcasts_real_model_chunks_and_tool_steps():
    q=runtime.subscribe()
    doc=SourceDoc(id='stream-doc',source='upload',filename='agreement.pdf',mime='application/pdf',
        data=Path('agreement_agent/samples/rio_grande_services_agreement.pdf').read_bytes())
    try:
        review(doc,use_model=True,model=Streamed())
        events=[]
        while not q.empty(): events.append(q.get_nowait())
        assert events
        assert events[0]['type']=='task_started' and events[0]['agent']=='agreement'
        assert any(e['type']=='thinking' and 'Check the document' in e['text'] for e in events)
        assert any(e['type']=='tool_started' and e['tool']=='read_header' for e in events)
        assert events[-1]['type']=='task_done'
        assert len({e['task_id'] for e in events})==1
    finally:
        runtime.unsubscribe(q)


def test_agreement_preserves_unobserved_model_configuration(monkeypatch):
    import langchain_anthropic
    from agreement_agent import graph
    monkeypatch.setattr(langchain_anthropic,'ChatAnthropic',lambda **kwargs: kwargs)
    assert 'thinking' not in graph._llm()
    q=runtime.subscribe()
    try:
        assert graph._llm()['thinking']['display']=='summarized'
    finally:
        runtime.unsubscribe(q)
