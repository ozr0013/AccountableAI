import test from 'node:test';
import assert from 'node:assert/strict';
import { createAgentApi } from './agent-api.ts';

function capture(result = {}) {
  const calls = [];
  const api = createAgentApi({ fetch: async (url, init) => {
    calls.push({ url, ...init });
    return new Response(JSON.stringify(result), { headers: { 'Content-Type': 'application/json' } });
  }});
  return { api, calls };
}
test('reads use independent same-origin services and send no credentials', async () => {
  const {api,calls}=capture([]);
  await api.agreements(); await api.agreement('a/b'); await api.health('chaser');
  assert.deepEqual(calls.map(c=>c.url), ['/agreement-agent-api/agreements','/agreement-agent-api/agreements/a%2Fb','/chaser-agent-api/health']);
  assert(calls.every(c=>!new Headers(c.headers).has('Authorization')));
});
test('invoice filters, encoded action identifiers, date queries and decision bodies match backend', async () => {
  const {api,calls}=capture();
  await api.invoices('unpaid','A & B'); await api.invoice('INV-AA-001');
  await api.preview('2026-10-24'); await api.run('2026-10-24');
  await api.decide('INV-AA-001:reminder:firm:0',true,'checked');
  assert.equal(calls[0].url,'/chaser-agent-api/chaser/invoices?status=unpaid&vendor=A+%26+B&limit=50');
  assert.equal(calls[1].url,'/chaser-agent-api/chaser/invoices/INV-AA-001');
  assert.equal(calls[2].url,'/chaser-agent-api/chaser/preview?as_of=2026-10-24');
  assert.equal(calls[2].method,'POST'); assert.equal(calls[3].url,'/chaser-agent-api/chaser/run?as_of=2026-10-24');
  assert.match(calls[4].url, /INV-AA-001%3Areminder%3Afirm%3A0\/decide$/);
  assert(!new Headers(calls[4].headers).has('Authorization'));
  assert.deepEqual(JSON.parse(calls[4].body),{approve:true,note:'checked'});
});
test('controller confirmation and rejection send their terms and notes, no credentials', async () => {
  const {api,calls}=capture();
  await api.confirm('doc',{payment_due:'30 days'},'reviewed');
  await api.reject('doc','wrong agreement');
  assert(calls.every(call=>!new Headers(call.headers).has('Authorization')));
  assert.deepEqual(JSON.parse(calls[0].body),{values:{payment_due:'30 days'},note:'reviewed'});
  assert.deepEqual(JSON.parse(calls[1].body),{note:'wrong agreement'});
});
test('uploads use multipart and explicit rules mode without JSON content-type', async () => {
  const {api,calls}=capture(); const file=new File(['terms'],'terms.txt',{type:'text/plain'});
  await api.review(file,false); await api.review(file,true);
  assert.equal(calls[0].url,'/agreement-agent-api/review?model=false');
  assert.equal(calls[1].url,'/agreement-agent-api/review?model=true');
  assert.equal(calls[0].body.get('file').name,'terms.txt');
  assert(!new Headers(calls[0].headers).has('Content-Type'));
});
test('ask, reply, action and pattern requests preserve supplied data', async () => {
 const {api,calls}=capture(); await api.ask('Why is this late?'); await api.reply('INV-AA-001','Paid yesterday');
 await api.actions('INV-AA-001'); await api.patterns('A & B');
 assert.deepEqual(JSON.parse(calls[0].body),{question:'Why is this late?'});
 assert.deepEqual(JSON.parse(calls[1].body),{invoice_id:'INV-AA-001',text:'Paid yesterday'});
 assert.equal(calls[2].url,'/chaser-agent-api/chaser/actions?invoice_id=INV-AA-001');
 assert.equal(calls[3].url,'/chaser-agent-api/chaser/patterns?vendor=A+%26+B');
});
test('connected case reads and reconciliation use the workflow endpoints without controller credentials', async () => {
 const {api,calls}=capture({cases:[],worker:{enabled:true,last_error:null,last_run:null}});
 const result=await api.cases(); await api.tick();
 assert.deepEqual(result.cases,[]);
 assert.equal(result.worker.enabled,true);
 assert.equal(calls[0].url,'/chaser-agent-api/workflow/cases');
 assert.equal(calls[0].method,'GET');
 assert.equal(calls[1].url,'/chaser-agent-api/workflow/tick');
 assert.equal(calls[1].method,'POST');
 assert.deepEqual(JSON.parse(calls[1].body),{});
 assert(calls.every(call=>!new Headers(call.headers).has('Authorization')));
});
test('structured evidence preserves invoice, payment and statement facts', async () => {
 const {api,calls}=capture({document_id:'evidence-1',cases:[]});
 const body={kind:'manager_bank',invoice_id:'INV/001',amount:1000,on:'2026-05-08',payee:'Vendor A',account:'1234',routing:'5678',bank:'Test bank',reference:'BANK-44'};
 const result=await api.evidence(body);
 assert.equal(result.document_id,'evidence-1');
 assert.equal(calls[0].url,'/chaser-agent-api/workflow/evidence');
 assert.equal(calls[0].method,'POST');
 assert(!new Headers(calls[0].headers).has('Authorization'));
 assert.deepEqual(JSON.parse(calls[0].body),{kind:'manager_bank',invoice_id:'INV/001',amount:1000,on:'2026-05-08',payee:'Vendor A',account:'1234',routing:'5678',bank:'Test bank',reference:'BANK-44'});
});
test('delivery acknowledgment and case resolution preserve their evidence notes', async () => {
 const {api,calls}=capture({status:'delivered'});
 await api.acknowledgeOutbox('outbox/id','external email receipt #42');
 await api.resolveCase('INV/001','Statement reconciled; late payment acknowledged');
 assert.equal(calls[0].url,'/chaser-agent-api/workflow/outbox/outbox%2Fid/ack');
 assert.deepEqual(JSON.parse(calls[0].body),{reference:'external email receipt #42'});
 assert.equal(calls[1].url,'/chaser-agent-api/workflow/cases/INV%2F001/resolve');
 assert.deepEqual(JSON.parse(calls[1].body),{note:'Statement reconciled; late payment acknowledged'});
 assert(calls.every(call=>!new Headers(call.headers).has('Authorization')));
});
test('HTTP detail and non-JSON gateway errors remain visible', async () => {
 let api=createAgentApi({fetch:async()=>new Response(JSON.stringify({detail:'stale approval'}),{status:409})});
 await assert.rejects(()=>api.health('agreement'), /409.*stale approval/);
 api=createAgentApi({fetch:async()=>new Response('<h1>Bad gateway</h1>',{status:502})});
 await assert.rejects(()=>api.agreements(), /502.*Bad gateway/);
 api=createAgentApi({fetch:async()=>new Response('not-json')});
 await assert.rejects(()=>api.agreements(), /invalid JSON/i);
});
test('timeouts abort outstanding requests and surface a usable error', async () => {
 const api=createAgentApi({timeoutMs:10,fetch:async (_url,{signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(signal.reason)))});
 await assert.rejects(()=>api.agreements(), /timed out/i);
});

test('confirmation accepts only missing payment terms, and active agreements can be rejected', async () => {
  const { missingPaymentTerms, canRejectAgreement, deadlineConflict } = await import('./agent-api.ts');
  assert.deepEqual(missingPaymentTerms({missing:['effective_date','payment_due','owner_name','payment_info']}), ['payment_due','payment_info']);
  assert.equal(canRejectAgreement({status:'active'}),true);
  assert.equal(canRejectAgreement({status:'confirmed'}),true);
  assert.equal(canRejectAgreement({status:'rejected'}),false);
  assert.deepEqual(deadlineConflict({ledger:{due_date:'2026-03-13'},chaser_today:{due_date:'2026-04-02'}}),{ledger:'2026-03-13',agreement:'2026-04-02'});
  assert.equal(deadlineConflict({ledger:{due_date:'2026-04-02'},chaser_today:{due_date:'2026-04-02'}}),null);
});

test('saved runs allow the current world date or blank, while historical and future dates remain previews', async () => {
 const {canPersistRunDate}=await import('./agent-api.ts');
 assert.equal(canPersistRunDate('',undefined),true);
 assert.equal(canPersistRunDate('2026-05-08','2026-05-08'),true);
 assert.equal(canPersistRunDate('2026-05-07','2026-05-08'),false);
 assert.equal(canPersistRunDate('2026-05-09','2026-05-08'),false);
 assert.equal(canPersistRunDate('2026-05-08',undefined),false);
});

test('a saved agreement with a deferred workflow keeps the retry warning visible', async () => {
 const {deferredWorkflowMessage}=await import('./agent-api.ts');
 assert.equal(deferredWorkflowMessage(undefined),null);
 assert.equal(deferredWorkflowMessage({triggered:true}),null);
 assert.match(deferredWorkflowMessage({triggered:false,error:'Saved; coordinator will retry on its next polling tick.'}),/deferred.*retry/i);
 assert.match(deferredWorkflowMessage({triggered:false}),/Reconcile now/);
});

test('delivery errors explain known recovery conditions without exposing arbitrary transport error text', async () => {
 const {deliveryErrorMessage}=await import('./agent-api.ts');
 assert.match(deliveryErrorMessage('recipient_not_configured'),/recipient.*configured/i);
 assert.match(deliveryErrorMessage('prior_attempt_requires_human_receipt'),/receipt/i);
 const fallback=deliveryErrorMessage('server password=secret');
 assert.match(fallback,/receipt/i);
 assert.doesNotMatch(fallback,/password|secret/);
});
