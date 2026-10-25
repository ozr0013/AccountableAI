
from chaser_agent import runtime
from chaser_agent.tests.test_pipeline import Scripted, _use


def drain(q):
    out=[]
    while not q.empty(): out.append(q.get_nowait())
    return out


def test_native_stream_identifies_skill_turn_and_tool_arguments():
    q=runtime.subscribe()
    token=runtime.CONTEXT.set({'task_id':'stream-test','agent':'chaser','invoice_id':'INV-KW-0301'})
    try:
        skill=runtime.load_skill('chasing')
        trace=runtime.run_agent('chaser','Read evidence',skill,
            [runtime.Tool('lookup','Read one invoice',{},lambda invoice: {'amount':1240},terminal=True)],
            'Check invoice',client=Scripted([_use('lookup',invoice='INV-KW-0301')]))
        events=drain(q)
        assert not trace.error
        assert next(e for e in events if e['type']=='skill_loaded')['name']=='chasing'
        assert any(e['type']=='model_turn' and e['turn']==1 for e in events)
        started=next(e for e in events if e['type']=='tool_started')
        result=next(e for e in events if e['type']=='tool_result')
        assert started['input']=={'invoice':'INV-KW-0301'}
        assert started['call_id']==result['call_id']
        assert all(e['task_id']=='stream-test' for e in events)
    finally:
        runtime.CONTEXT.reset(token)
        runtime.unsubscribe(q)


def test_stream_response_delivers_events_and_unsubscribes_on_disconnect():
    import asyncio

    async def scenario():
        before=len(runtime._SUBSCRIBERS)
        response=runtime.event_response()
        iterator=response.body_iterator
        assert 'event: ready' in await anext(iterator)
        runtime.emit({'type':'task_started','agent':'agreement','task_id':'live-test'})
        payload=await anext(iterator)
        assert 'event: task_started' in payload and 'live-test' in payload
        await iterator.aclose()
        assert len(runtime._SUBSCRIBERS)==before
    asyncio.run(scenario())
