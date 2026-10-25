"use client";

import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { SectionTitle } from "@/components/ui";
import { createAgentApi, missingPaymentTerms, canRejectAgreement, canPersistRunDate, deferredWorkflowMessage, deliveryErrorMessage, deadlineConflict, type Agreement, type Answer, type ReviewResult, type Run, type Rule, type Decision, type EvidenceInput, type WorkflowCases, type WorkflowOutbox } from "@/lib/agent-api";

const api = createAgentApi();
const message = (error: unknown) => error instanceof Error ? error.message : String(error);

/** A changed resource key invalidates every earlier response, including failed requests. */
function useRead<T>(key: string, read: () => Promise<T>, enabled = true) {
  const [state, setState] = useState<{key: string; data?: T; error?: string}>({key});
  useEffect(() => {
    let current = true;
    if (enabled) read().then(data => { if (current) setState({key,data}); }, error => { if (current) setState({key,error:message(error)}); });
    return () => { current = false; };
    // The key fully identifies the resource and refresh revision.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, enabled]);
  return state.key === key ? state : {key};
}
/** Keep case details and form drafts visible while idle polling refreshes their records. */
function useWorkflowCases(revision: number, busy: RefObject<boolean>) {
  const [state, setState] = useState<{data?: WorkflowCases; error?: string}>({});
  useEffect(() => {
    let current = true;
    let request = 0;
    let inFlight = false;
    async function read() {
      if (inFlight) return;
      inFlight = true;
      const sequence = ++request;
      try {
        const data = await api.cases();
        if (current && sequence === request) setState({data});
      } catch (error) {
        if (current && sequence === request) setState(previous => ({...previous,error:message(error)}));
      } finally { inFlight = false; }
    }
    void read();
    const timer = setInterval(() => {
      if (!busy.current && document.visibilityState === "visible") void read();
    }, 15_000);
    return () => { current = false; clearInterval(timer); };
  }, [revision,busy]);
  return state;
}
function Json({value}: {value: unknown}) {
  return <pre className="doc mono" style={{whiteSpace:"pre-wrap",overflowWrap:"anywhere",fontSize:12,maxHeight:340,overflow:"auto"}}>{JSON.stringify(value,null,2)}</pre>;
}
function ReadState({error, loading, children}: {error?: string; loading: boolean; children: ReactNode}) {
  if (error) return <p className="notice" role="alert">{error}</p>;
  if (loading) return <p className="muted" role="status">Loading live data…</p>;
  return children;
}
function Rules({rules}: {rules: Rule[]}) {
  return <div className="stack" style={{gap:10}}>{rules.length ? rules.map(rule => <article key={rule.rule_id} className="doc">
    <div className="between"><strong>{rule.rule_type}</strong><span className="tag neutral">{rule.status}</span></div>
    <p>{rule.check}</p>{rule.source_quote && <blockquote>{rule.source_quote}{rule.location && <small> · {rule.location}</small>}</blockquote>}
    <details><summary>Rule parameters</summary><Json value={rule.params}/></details>
  </article>) : <p className="muted">No saved rules.</p>}</div>;
}
function DecisionView({decision}: {decision: Decision}) {
  return <article className="doc"><div className="between"><strong>{decision.invoice_id} · {decision.action}</strong><span className="tag neutral">{decision.status === "queued" ? "Queued · not delivered" : decision.status}</span></div>
    <p>{decision.reason}</p>{decision.action === "handoff_anomaly" && <p className="notice">Payment evidence is routed to the connected case review below for findings, evidence requests and statement reconciliation.</p>}
    {decision.due_date && <p className="micro muted">Due {decision.due_date} · {decision.days_overdue ?? 0} days overdue</p>}
    {decision.message && <blockquote style={{whiteSpace:"pre-wrap"}}>{decision.message}</blockquote>}
    {decision.rule_quote && <p className="micro muted">Rule: {decision.rule_quote}</p>}
  </article>;
}
function EvidenceIntake({invoiceId,vendorId,busy,onSubmit}: {invoiceId: string; vendorId?: string; busy: boolean; onSubmit: (body: EvidenceInput) => void}) {
  const [kind,setKind] = useState<EvidenceInput["kind"]>("vendor_invoice");
  const [fields,setFields] = useState({invoice_id:invoiceId,vendor_id:vendorId ?? "",amount:"",on:"",payee:"",account:"",routing:"",bank:"",reference:"",invoice_date:"",replaces:""});
  function field(name: keyof typeof fields, label: string, required = false, type = "text") {
    const pattern=name==="invoice_id" ? "INV-[A-Z]{2}-[0-9]{3,4}" : name==="account" ? "[0-9]{4,17}" : name==="routing" ? "[0-9]{9}" : undefined;
    return <label className="body" key={name}>{label}<input className="input" type={type} required={required} pattern={pattern} title={name==="invoice_id" ? "Use an invoice ID such as INV-AA-001" : name==="account" ? "4 to 17 digits" : name==="routing" ? "9 digits" : undefined} placeholder={name==="invoice_id" ? "INV-AA-001" : name==="vendor_id" ? "v-vendor-id" : undefined} maxLength={name==="vendor_id" ? 100 : 200} disabled={busy} value={fields[name]} onChange={event=>setFields(current=>({...current,[name]:event.target.value}))}/></label>;
  }
  return <form className="stack" style={{gap:14}} onSubmit={event=>{
    event.preventDefault();
    const amount=Number(fields.amount);
    if (!Number.isFinite(amount) || amount<=0) return;
    const body: EvidenceInput={kind,invoice_id:fields.invoice_id.trim(),amount,on:fields.on};
    for (const name of ["vendor_id","payee","account","routing","bank","reference","invoice_date","replaces"] as const) {
      const value=fields[name].trim();
      if (value && (name!=="invoice_date" || kind==="vendor_invoice") && (!["payee","account","routing","bank"].includes(name) || kind==="manager_bank")) body[name]=value;
    }
    onSubmit(body);
  }}>
    <h2 className="h4 display">Add structured evidence</h2>
    <p className="muted">Record invoice, bank or statement facts. Each save reconciles the linked case. Agreement uploads above extract terms; add invoice evidence here to start monitoring.</p>
    <label className="body">Evidence type<select className="input" value={kind} disabled={busy} onChange={event=>setKind(event.target.value as EvidenceInput["kind"])}><option value="vendor_invoice">Vendor invoice</option><option value="manager_bank">Manager bank payment</option><option value="manager_statement">Manager statement</option></select></label>
    {kind==="manager_statement" && <p className="micro muted">This statement entry marks the specified amount as paid. Enter the amount shown as paid on the statement.</p>}
    <div className="grid g2">{field("invoice_id","Invoice ID",true)}{field("vendor_id",kind==="manager_statement" ? "Known vendor ID (optional)" : "Known vendor ID",kind!=="manager_statement")}
      <label className="body">{kind==="vendor_invoice" ? "Invoice amount" : kind==="manager_bank" ? "Payment amount" : "Statement amount marked paid"}<input className="input" type="number" min="0.01" max="1000000000" step="0.01" required value={fields.amount} disabled={busy} onChange={event=>setFields(current=>({...current,amount:event.target.value}))}/></label>
      {field("on",kind==="vendor_invoice" ? "Date received" : kind==="manager_bank" ? "Payment date" : "Statement date",true,"date")}
      {kind==="vendor_invoice" && field("invoice_date","Invoice date (optional)",false,"date")}
      {kind==="manager_bank" && <>{field("payee","Payee (as shown in evidence)")}{field("bank","Bank")}{field("account","Account")}{field("routing","Routing")}</>}
    </div>
    {field("reference","Source or payment reference")}
    {field("replaces","Corrects document ID (optional)")}
    <p className="micro muted">For a correction, paste the prior document ID from the case timeline. The replacement preserves the original evidence history.</p>
    <p className="micro muted">Use the exact recipient details from the evidence. Missing or conflicting proof remains an open finding for review.</p>
    <button className="pill" type="submit" disabled={busy}>Save evidence and reconcile</button>
  </form>;
}
function OutboxItem({item,busy,onAcknowledge}: {item: WorkflowOutbox; busy: boolean; onAcknowledge: (id: string, reference: string) => void}) {
  const [reference,setReference] = useState("");
  return <article className="doc stack" style={{gap:10}}>
    <div className="between"><strong>{item.kind} · {item.audience}</strong><span className="tag neutral">{item.status==="queued" ? "Queued · awaiting delivery" : item.status==="delivered" ? "Delivery acknowledged" : item.status.replaceAll("_"," ")}</span></div>
    <p className="micro muted">Created {item.created_on}</p><p style={{whiteSpace:"pre-wrap"}}>{item.body}</p>
    {item.reference && <p className="micro">Delivery reference: {item.reference}</p>}
    {item.delivered_on && <p className="micro muted">Delivered on {item.delivered_on}</p>}
    {item.status==="needs_approval" && <p className="micro muted">{item.kind==="review_anomaly" ? "Review this case’s findings and use the controller resolution note above when all required evidence is verified." : <a href="#controller-approvals">Review the pending controller decision below.</a>}</p>}
    {item.status==="queued" && <form className="stack" style={{gap:8}} onSubmit={event=>{event.preventDefault();onAcknowledge(item.id,reference.trim());}}>
      <label className="body">External delivery reference<input className="input" required maxLength={500} value={reference} disabled={busy} onChange={event=>setReference(event.target.value)} placeholder="Receipt or message ID after delivering outside this app"/></label>
      <button className="pill" type="submit" disabled={busy || !reference.trim()}>Record completed external delivery</button>
    </form>}
  </article>;
}

export default function AgentWorkflow() {
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(""); const busyRef = useRef(false);
  const [notice, setNotice] = useState(""); const [error, setError] = useState("");
  const [docId, setDocId] = useState(""); const [invoiceId, setInvoiceId] = useState("");
  const [vendor, setVendor] = useState(""); const [status, setStatus] = useState("all");
  const [asOf, setAsOf] = useState(""); const [runResult, setRunResult] = useState<{kind:string; data:Run} | null>(null);
  const [file, setFile] = useState<File | null>(null); const [model, setModel] = useState(true);
  const [review, setReview] = useState<ReviewResult | null>(null);
  const [values, setValues] = useState<Record<string,string>>({}); const [note, setNote] = useState("");
  const [actionNote, setActionNote] = useState("");
  const [question, setQuestion] = useState(""); const [answer, setAnswer] = useState<Answer | null>(null);
  const [reply, setReply] = useState(""); const [replyResult, setReplyResult] = useState<{id:string; result:unknown} | null>(null);
  const [resolveNote,setResolveNote] = useState("");
  const agreements = useRead(`agreements:${revision}`, () => api.agreements());
  const agreementHealth = useRead(`ah:${revision}`, () => api.health("agreement"));
  const chaserHealth = useRead(`ch:${revision}`, () => api.health("chaser"));
  const agreement = useRead(`agreement:${docId}:${revision}`, () => api.agreement(docId), !!docId);
  const invoices = useRead(`invoices:${status}:${vendor}:${revision}`, () => api.invoices(status,vendor));
  const invoice = useRead(`invoice:${invoiceId}:${revision}`, () => api.invoice(invoiceId), !!invoiceId);
  const actions = useRead(`actions:${revision}`, () => api.actions());
  const patterns = useRead(`patterns:${revision}`, () => api.patterns());
  const cases = useWorkflowCases(revision,busyRef);
  const refresh = () => setRevision(value => value + 1);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  async function perform(label: string, operation: () => Promise<void | string>) {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(label); setNotice(""); setError("");
    try { const result=await operation(); if (mounted.current) { setNotice(result || `${label} completed.`); refresh(); } }
    catch (failure) { if (mounted.current) { setError(`${label}: ${message(failure)}`); refresh(); } }
    finally { busyRef.current = false; if (mounted.current) setBusy(""); }
  }
  function selectAgreement(id: string) { setDocId(id); setValues({}); setNote(""); }
  function selectInvoice(id: string) { setInvoiceId(id); setReply(""); setReplyResult(null); setResolveNote(""); }
  const pending = actions.data?.filter(action => ["needs_approval","needs_controller"].includes(action.status)) ?? [];
  const selectedAgreement: Agreement | undefined = agreement.data;
  const missingTerms = selectedAgreement ? missingPaymentTerms(selectedAgreement) : [];
  const conflict = invoice.data ? deadlineConflict(invoice.data) : null;
  const selectedCase = cases.data?.cases.find(item=>item.invoice_id===invoiceId);
  const canSaveRun = canPersistRunDate(asOf,invoices.data?.as_of);
  const deferredReview = deferredWorkflowMessage(review?.workflow);
  const delivery = cases.data?.worker.delivery;
  return <>
    <div className="top"><div className="stack" style={{gap:6}}><h1 className="display h2">Agent workflow</h1><p className="body muted">Agreement terms connect invoice monitoring, payment evidence, findings and statement reconciliation. Queued drafts await delivery.</p></div><div className="top-meta"><span className="chip live">Live agent APIs</span>{invoices.data?.as_of && <span className="chip">World date: {invoices.data.as_of}</span>}</div></div>
    <div className="between" style={{gap:16,marginBottom:20,flexWrap:"wrap"}}>
      <button className="pill" disabled={!!busy} onClick={refresh}>Refresh live data</button>
      
    </div>
    <div className="grid g2">
      {([["Agreement service",agreementHealth],["Chaser service",chaserHealth]] as const).map(([label,health]) => <div key={label} className="card"><strong>{label}</strong><ReadState error={health.error} loading={!health.data}><p>{health.data?.ok ? "API reachable" : "API not healthy"} · Claude key {health.data?.model_available ? "configured" : "not configured"}</p><small className="muted">Data dependencies are checked by each read below.</small></ReadState></div>)}
    </div>
    <div aria-live="polite">{busy && <p className="notice">{busy}…</p>}{notice && <p className="notice">{notice}</p>}</div>
    {error && <p className="notice" role="alert">{error}</p>}

    <SectionTitle>Agreements and rules</SectionTitle>
    <div className="grid g2">
      <section className="card stack" style={{gap:14}}>
        <h2 className="h4 display">Saved agreements</h2>
        <ReadState error={agreements.error} loading={!agreements.data}>
          {agreements.data?.length === 0 && <p className="muted">No saved agreements.</p>}
          {agreements.data?.map(doc => <button key={doc.doc_id} className="pill" disabled={!!busy} aria-pressed={docId === doc.doc_id} onClick={()=>selectAgreement(doc.doc_id)}>{doc.filename} · {doc.status}</button>)}
        </ReadState>
        <h3>Upload a document</h3>
        <label className="body">PDF or text, up to 10 MB<input className="input" type="file" accept="application/pdf,text/plain,.pdf,.txt" disabled={!!busy} onChange={event=>{setFile(event.target.files?.[0] ?? null); setReview(null);}}/></label>
        <label className="body"><input type="checkbox" checked={model} disabled={!!busy} onChange={event=>setModel(event.target.checked)}/> Use Claude (metered model call). Unchecked uses fixed rules.</label>
        <button className="pill" disabled={!!busy || !file || file.size === 0 || file.size > 10*1024*1024} onClick={()=>perform("Document review",async()=>{if(file) setReview(await api.review(file,model));})}>Review and save document</button>
        {review && <div><p>{review.review.filename} · {review.status} · {review.saved_to_spacetime ? "Saved to SpacetimeDB" : "NOT SAVED"}</p>{review.save_error && <p className="notice" role="alert">Persistence failed: {review.save_error}</p>}{deferredReview && <p className="notice" role="alert">{deferredReview}</p>}<Rules rules={review.rules}/><details><summary>Extracted fields and review steps</summary><Json value={review.review}/></details></div>}
      </section>
      <section className="card stack" style={{gap:14}}><h2 className="h4 display">Agreement detail</h2>
        {!docId ? <p className="muted">Select an agreement to inspect its rules.</p> : <ReadState error={agreement.error} loading={!selectedAgreement}>
          {selectedAgreement && <><p>{selectedAgreement.filename} · {selectedAgreement.vendor_name} · {selectedAgreement.status}</p><Rules rules={selectedAgreement.rules}/>
            {selectedAgreement.status === "needs_controller" && <><h3>Controller sign-off</h3>{missingTerms.map(term=><label key={term} className="body">{term}<input className="input" maxLength={500} value={values[term] ?? ""} disabled={!!busy} onChange={event=>setValues(current=>({...current,[term]:event.target.value}))}/></label>)}
              <div className="row"><button className="pill" disabled={!!busy || missingTerms.some(term=>!values[term]?.trim())} onClick={()=>perform("Confirm agreement",async()=>{const result=await api.confirm(docId,values,note);return ["Agreement confirmed.",deferredWorkflowMessage(result.workflow)].filter(Boolean).join(" ");})}>Confirm missing terms</button>
                </div></>}
            {selectedAgreement.missing.some(term=>!missingTerms.includes(term)) && <p className="micro muted">Other missing metadata: {selectedAgreement.missing.filter(term=>!missingTerms.includes(term)).join(", ")}. These fields are not accepted by controller confirmation.</p>}
            {canRejectAgreement(selectedAgreement) && <><label className="body">Review note (required to reject)<textarea className="input" maxLength={1000} value={note} disabled={!!busy} onChange={event=>setNote(event.target.value)}/></label><button className="pill" disabled={!!busy || !note.trim()} onClick={()=>perform("Reject agreement",async()=>{const result=await api.reject(docId,note);return ["Agreement rejected.",deferredWorkflowMessage(result.workflow)].filter(Boolean).join(" ");})}>Reject agreement</button></>}
            <details><summary>Saved agreement record</summary><Json value={selectedAgreement}/></details></>}
        </ReadState>}
      </section>
    </div>

    <SectionTitle right={`${cases.data?.cases.length ?? 0} cases`}>Connected payment review</SectionTitle>
    <section className="card stack" style={{gap:14}}>
      <div className="between" style={{gap:14,flexWrap:"wrap"}}><p className="muted">Cases refresh every 15 seconds while this page is idle. Select an invoice to follow its evidence, findings and communications.</p><button className="pill" disabled={!!busy} onClick={()=>perform("Reconcile connected cases",async()=>{await api.tick();})}>Reconcile now</button></div>
      {cases.error && <p className="notice" role="alert">Case refresh: {cases.error}{cases.data && " Showing the last successful snapshot."}</p>}
      {!cases.data && !cases.error && <p className="muted" role="status">Loading connected cases…</p>}
      {cases.data && <>
        <p className="micro muted">Worker {cases.data.worker.enabled ? "enabled" : "paused"}{cases.data.worker.last_run ? ` · Last run ${cases.data.worker.last_run}` : " · No run recorded"}</p>
        {cases.data.worker.last_error && <p className="notice" role="alert">Worker error: {cases.data.worker.last_error}</p>}
        {delivery && <p className="micro muted">Automatic delivery {delivery.enabled ? delivery.configured ? "enabled and configured" : "enabled; configuration incomplete" : "disabled"} · {delivery.delivered.length} deliveries acknowledged in the latest check</p>}
        {!!delivery?.errors.length && <div className="notice" role="alert"><strong>{delivery.errors.length} delivery issue{delivery.errors.length===1 ? "" : "s"} need review</strong><ul>{delivery.errors.slice(0,5).map((failure,index)=><li key={`${failure.outbox_id}:${index}`}>{failure.outbox_id ? `${failure.outbox_id}: ` : "Delivery setup: "}{deliveryErrorMessage(failure.error)}</li>)}</ul>{delivery.errors.length>5 && <p>{delivery.errors.length-5} more issues. Review the affected outbox items.</p>}</div>}
        {!cases.data.cases.length && <p>No connected cases yet. Add invoice evidence below to start a case.</p>}
        <div style={{overflowX:"auto"}}><table className="table" style={{width:"100%"}}><thead><tr><th>Invoice</th><th>Vendor</th><th>Case state</th><th>Findings</th><th>Communications</th></tr></thead><tbody>{cases.data.cases.map(item=><tr key={item.invoice_id}><td><button className="pill" disabled={!!busy} aria-pressed={invoiceId===item.invoice_id} onClick={()=>selectInvoice(item.invoice_id)}>{item.invoice_id}</button></td><td>{item.vendor}</td><td>{item.state.replaceAll("_"," ")}</td><td>{item.findings.length}</td><td>{item.outbox.filter(outbox=>outbox.status==="queued").length} awaiting delivery</td></tr>)}</tbody></table></div>
      </>}
    </section>
    <div className="grid g2" style={{marginTop:16}}>
      <section className="card stack" style={{gap:14}}>
        <EvidenceIntake key={`${invoiceId}:${selectedCase?.vendor_id ?? ""}`} invoiceId={invoiceId} vendorId={selectedCase?.vendor_id} busy={!!busy} onSubmit={body=>perform("Save evidence",async()=>{const result=await api.evidence(body);selectInvoice(body.invoice_id);return `Evidence ${result.document_id} saved; the linked case was reconciled.`;})}/>
      </section>
      <section className="card stack" style={{gap:14}}><h2 className="h4 display">{selectedCase ? `${selectedCase.invoice_id} · ${selectedCase.state.replaceAll("_"," ")}` : "Case detail"}</h2>
        {!selectedCase ? <p className="muted">{invoiceId ? "No connected case is available for the selected invoice. Reconcile after adding its evidence." : "Select an invoice from the case list or invoice table."}</p> : <>
          <h3>Findings</h3>{selectedCase.findings.length ? selectedCase.findings.map((finding,index)=><article className="doc" key={`${finding.code}:${index}`}><strong>{finding.code.replaceAll("_"," ")}</strong><p>{finding.message}</p></article>) : <p className="muted">No current findings.</p>}
          <h3>Evidence sources</h3>{selectedCase.citations?.length ? selectedCase.citations.map(source=><p className="micro" key={source.source_id}>{source.kind.replaceAll("_"," ")} · <code>{source.source_id}</code></p>) : <p className="micro muted">{selectedCase.source_ids?.join(", ") || "No source documents recorded."}</p>}
          <h3>Case timeline</h3>{selectedCase.history.length ? <ol className="stack" style={{gap:10,paddingLeft:20}}>{selectedCase.history.map(event=><li key={event.id}><strong>{event.kind.replaceAll("_"," ")}</strong><p className="micro muted">{event.at}</p><details><summary>Event evidence</summary><Json value={event.payload}/></details></li>)}</ol> : <p className="muted">No case events recorded.</p>}
          {selectedCase.state!=="closed" && <><label className="body">Controller resolution note<textarea className="input" maxLength={1000} value={resolveNote} disabled={!!busy} onChange={event=>setResolveNote(event.target.value)}/></label><p className="micro muted">Closure requires full verified payment and a reconciled statement. Your note acknowledges remaining findings; required missing evidence and unpaid amounts must be cleared first.</p><button className="pill" disabled={!!busy || !resolveNote.trim() || selectedCase.closure_eligible===false} onClick={()=>perform("Resolve case",async()=>{await api.resolveCase(selectedCase.invoice_id,resolveNote.trim());setResolveNote("");})}>Acknowledge findings and close case</button></>}
        </>}
      </section>
    </div>
    {selectedCase && <section className="card stack" style={{gap:14,marginTop:16}}><h2 className="h4 display">{selectedCase.invoice_id} · communication outbox</h2><p className="muted">Queued communications await delivery. For an item delivered outside the app, record the receipt below. A controller approval authorizes delivery; the receipt confirms completion.</p>{selectedCase.outbox.length ? selectedCase.outbox.map(item=><OutboxItem key={item.id} item={item} busy={!!busy} onAcknowledge={(id,reference)=>perform("Record external delivery",async()=>{await api.acknowledgeOutbox(id,reference);return "External delivery acknowledgment recorded.";})}/>) : <p className="muted">No communication records for this case.</p>}</section>}

    <SectionTitle>Invoice chasing</SectionTitle>
    <section className="card stack" style={{gap:14}}>
      <div className="row" style={{flexWrap:"wrap"}}><label>Status <select className="input" value={status} disabled={!!busy} onChange={event=>setStatus(event.target.value)}>{["all","unpaid","overdue","not_due","paid"].map(value=><option key={value}>{value}</option>)}</select></label><label>Vendor <input className="input" value={vendor} disabled={!!busy} onChange={event=>setVendor(event.target.value)} placeholder="Filter vendor"/></label></div>
      <ReadState error={invoices.error} loading={!invoices.data}>
        <p className="micro muted">Showing {invoices.data?.invoices.length ?? 0} of {invoices.data?.count ?? 0} matching invoices (up to 50).</p>
        <div style={{overflowX:"auto"}}><table className="table" style={{width:"100%"}}><thead><tr><th>Invoice</th><th>Vendor</th><th>Amount / paid</th><th>Deadline</th><th>Status / next action</th></tr></thead><tbody>{invoices.data?.invoices.map(row=><tr key={row.invoice_id}><td><button className="pill" disabled={!!busy} aria-pressed={invoiceId===row.invoice_id} onClick={()=>selectInvoice(row.invoice_id)}>{row.invoice_id}</button></td><td>{row.vendor}</td><td>{row.amount.toLocaleString()} / {row.paid_amount.toLocaleString()}</td><td>{row.deadline ?? "No rule"}</td><td>{row.status} / {row.chaser_today ?? "None"}</td></tr>)}</tbody></table></div>
      </ReadState>
      <div className="row" style={{flexWrap:"wrap"}}><label>As of (blank uses world date; all other dates are preview only)<input className="input" type="date" value={asOf} disabled={!!busy} onChange={event=>{setAsOf(event.target.value);setRunResult(null);}}/></label>
        <button className="pill" disabled={!!busy} onClick={()=>perform("Preview (no writes)",async()=>setRunResult({kind:"Preview · no writes",data:await api.preview(asOf)}))}>Preview decisions</button>
        <button className="pill" disabled={!!busy || !canSaveRun} onClick={()=>perform("Run and persist daily check",async()=>setRunResult({kind:"Persisted run",data:await api.run(asOf)}))}>Run and save actions</button></div>
      {!canSaveRun && <p className="micro muted">Use Preview decisions for this date. Saved runs use the current world date{invoices.data?.as_of ? ` (${invoices.data.as_of})` : ""}.</p>}
      {runResult && <div className="stack" style={{gap:12}}><strong>{runResult.kind} · {runResult.data.as_of}</strong>{runResult.data.saved !== undefined && <p>{runResult.data.saved} actions saved. Queued drafts are not delivered.</p>}{runResult.data.decisions.length ? runResult.data.decisions.map((decision,index)=><DecisionView key={`${decision.invoice_id}:${index}`} decision={decision}/>) : <p>No decisions.</p>}</div>}
    </section>
    {invoiceId && <section className="card stack" style={{gap:14,marginTop:16}}><h2 className="h4 display">{invoiceId} · live detail</h2><ReadState error={invoice.error} loading={!invoice.data}>
      {invoice.data && <><p>{invoice.data.vendor}</p>{conflict && <p className="notice">Deadline discrepancy: ledger {conflict.ledger}; agreement rule {conflict.agreement}. Chasing uses the agreement rule. Review the conflicting records.</p>}{invoice.data.chaser_today && <DecisionView decision={invoice.data.chaser_today}/>}<details><summary>Ledger and evidence</summary><Json value={invoice.data.ledger}/></details><Rules rules={invoice.data.rules}/><details><summary>Invoice action history ({invoice.data.actions.length})</summary>{invoice.data.actions.map(action=><DecisionView key={action.action_id} decision={action}/>)}</details>
        <label>Manager reply to read (records the reading)<textarea className="input" maxLength={4000} value={reply} disabled={!!busy} onChange={event=>setReply(event.target.value)}/></label><button className="pill" disabled={!!busy || !reply.trim()} onClick={()=>perform("Read manager reply",async()=>{const id=invoiceId; const result=await api.reply(id,reply);setReplyResult({id,result});})}>Read reply and check proof</button>
        {replyResult?.id===invoiceId && <Json value={replyResult.result}/>}</>}
    </ReadState></section>}

    <SectionTitle right={`${pending.length} pending`}>Controller approvals</SectionTitle>
    <section className="card stack" id="controller-approvals" style={{gap:14}}>
      <p className="muted">Approvals record a controller decision. They do not deliver email or move money. Refresh before deciding if another controller may have acted.</p>
      <label>Decision note<textarea className="input" maxLength={1000} value={actionNote} disabled={!!busy} onChange={event=>setActionNote(event.target.value)}/></label>
      <ReadState error={actions.error} loading={!actions.data}>{pending.length===0 && <p>No pending controller actions.</p>}{pending.map(action=><div key={action.action_id}><DecisionView decision={action}/><div className="row" style={{marginTop:8}}>{[true,false].map(approve=><button key={String(approve)} className="pill" disabled={!!busy} onClick={()=>perform(`${approve ? "Approve" : "Reject"} ${action.invoice_id}`,async()=>{await api.decide(action.action_id,approve,actionNote);})}>{approve ? "Approve draft / proposal" : "Reject"}</button>)}</div></div>)}</ReadState>
      <details><summary>All recorded actions</summary>{actions.data?.map(action=><DecisionView key={action.action_id} decision={action}/>)}</details>
    </section>
    <SectionTitle>Vendor payment patterns</SectionTitle>
    <section className="card"><ReadState error={patterns.error} loading={!patterns.data}>{patterns.data?.length ? patterns.data.map(pattern=><details key={pattern.vendor}><summary>{pattern.vendor}</summary><Json value={pattern}/></details>) : <p>No saved payment patterns.</p>}</ReadState></section>
    <SectionTitle>Ask Claude</SectionTitle>
    <section className="card stack" style={{gap:14}}><p className="muted">Explicit model call using read-only invoice, agreement and chaser tools.</p><label>Question<textarea className="input" minLength={3} maxLength={2000} value={question} disabled={!!busy} onChange={event=>setQuestion(event.target.value)}/></label>
      <button className="pill" disabled={!!busy || question.trim().length<3 || !chaserHealth.data?.model_available} onClick={()=>perform("Ask Claude",async()=>{setAnswer(null);setAnswer(await api.ask(question));})}>Ask with live tools</button>
      {answer && <><p style={{whiteSpace:"pre-wrap"}}>{answer.answer || answer.error || "No answer returned."}</p><details open><summary>Reasoning summary</summary><Json value={answer.thinking ?? []}/></details><details open><summary>Tool trace</summary><Json value={answer.tools}/></details></>}
    </section>
  </>;
}
