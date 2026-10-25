/** Same-origin clients for the persisted agent services (separate from Google OAuth). */
export type WorkflowTrigger = {triggered: boolean; error?: string};
export type Rule = { rule_id: string; rule_type: string; params: unknown; check: string; status: string; source_quote?: string | null; location?: string | null };
export type Agreement = { doc_id: string; filename: string; kind: string; status: string; vendor_name?: string | null; missing: string[]; complete: boolean; rules: Rule[]; controller_note?: string | null; workflow?: WorkflowTrigger };
export type Decision = { invoice_id: string; action: string; reason: string; status: string; level?: string | null; due_date?: string | null; days_overdue?: number; amount_owed?: number; message?: string | null; rule_quote?: string | null };
export type Action = Decision & { action_id: string; as_of: string; vendor_name: string; decision_note?: string | null };
export type Invoice = { invoice_id: string; vendor: string; amount: number; received: string; paid_amount: number; paid_on: string | null; deadline: string | null; status: string; chaser_today: string | null };
export type InvoiceDetail = { ledger: unknown; vendor: string; chaser_today: Decision | null; rules: Rule[]; actions: Action[] };
export type Run = { as_of: string; decisions: Decision[]; counts?: Record<string, number>; saved?: number; cases?: WorkflowCase[] };
export type ReviewResult = { review: { doc_id: string; filename: string; fields: unknown[]; steps: unknown[]; mode: string; cost_usd: number }; rules: Rule[]; status: string; saved_to_spacetime: boolean; save_error: string | null; workflow?: WorkflowTrigger };
export type Answer = { answer: string | null; error?: string; thinking?: unknown; tools: unknown[]; usage?: unknown };
export type Health = { ok: boolean; model_available: boolean; daily?: string; last_run?: unknown };
export type Pattern = { vendor: string; [key: string]: unknown };
export type WorkflowOutbox = { id: string; invoice_id: string; kind: string; audience: string; body: string; status: 'needs_approval' | 'queued' | 'delivered' | 'cancelled'; created_on: string; reference?: string | null; delivered_on?: string | null };
export type WorkflowCase = { invoice_id: string; vendor: string; vendor_id?: string; state: string; closure_eligible?: boolean; source_ids: string[]; citations: {source_id: string; kind: string}[]; findings: {code: string; message: string}[]; history: {id: string; kind: string; at: string; payload: unknown}[]; outbox: WorkflowOutbox[] };
export type WorkflowDelivery = {enabled: boolean; configured: boolean; delivered: string[]; errors: {outbox_id: string | null; error: string}[]};
export type WorkflowCases = { cases: WorkflowCase[]; worker: {enabled: boolean; last_error: string | null; last_run: string | null; delivery?: WorkflowDelivery | null} };
export type EvidenceInput = { kind: 'vendor_invoice' | 'manager_bank' | 'manager_statement'; invoice_id: string; vendor_id?: string; amount: number; on: string; payee?: string; account?: string; routing?: string; bank?: string; reference?: string; invoice_date?: string; replaces?: string };

/** Agent pipeline: SpacetimeDB task queue shared by the chaser and anomaly agents. */
export type AgentStep = { tool: string; input: Record<string, unknown>; result: unknown; refused: boolean; terminal: boolean };
export type AgentTrace = { agent: 'chaser' | 'anomaly'; model: string; thinking: string[]; steps: AgentStep[]; decision: {tool: string; input: Record<string, unknown>; result: unknown} | null; text: string; usage: Record<string, number>; error: string | null };
export type TaskOutcome = { decision: string | null; rationale?: string | null; verdict?: 'clean' | 'awaiting_statement' | 'needs_review' | null; summary?: string | null; finding_codes?: string[] | null; result?: unknown; error: string | null };
export type AgentTask = { task_id: string; agent: 'chaser' | 'anomaly'; invoice_id: string; status: 'pending' | 'running' | 'done' | 'failed'; created_by: string; reason: string; payload: Record<string, unknown>; outcome: TaskOutcome; as_of: string; trace?: AgentTrace };
export type PipelineFlow = { invoice: unknown; vendor: string; agreement: Record<string, unknown>[]; rules: Rule[]; tasks: AgentTask[]; chaser_actions: Action[]; case: WorkflowCase | null };
export type Processed = { task_id: string; agent: string; invoice_id: string } & TaskOutcome;

type Options = { fetch?: typeof fetch; timeoutMs?: number };
export function createAgentApi(options: Options = {}) {
  const fetcher = options.fetch ?? globalThis.fetch;
  async function request<T>(service: 'agreement' | 'chaser', path: string, body?: unknown): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(new Error('Agent request timed out; refresh before retrying a write.')), options.timeoutMs ?? 120_000);
    const headers = new Headers();
    const multipart = body instanceof FormData;
    if (body !== undefined && !multipart) headers.set('Content-Type', 'application/json');
    try {
      const response = await fetcher(`/${service}-agent-api${path}`, {
        method: body === undefined ? 'GET' : 'POST', headers,
        body: body === undefined ? undefined : multipart ? body : JSON.stringify(body),
        signal: controller.signal, cache: 'no-store',
      });
      const raw = await response.text();
      let data: unknown;
      try { data = JSON.parse(raw); } catch { if (response.ok) throw new Error('Agent returned invalid JSON.'); }
      if (!response.ok) {
        const detail = data && typeof data === 'object' && 'detail' in data ? JSON.stringify(data.detail) : raw.slice(0, 400);
        throw new Error(`Agent API ${response.status}: ${detail}`);
      }
      return data as T;
    } finally { clearTimeout(timer); }
  }
  const query = (values: Record<string, string>) => `?${new URLSearchParams(values)}`;
  const id = encodeURIComponent;
  return {
    health: (service: 'agreement' | 'chaser') => request<Health>(service, '/health'),
    agreements: () => request<Agreement[]>('agreement', '/agreements'),
    agreement: (doc: string) => request<Agreement>('agreement', `/agreements/${id(doc)}`),
    review: (file: File, model: boolean) => { const form = new FormData(); form.set('file', file); return request<ReviewResult>('agreement', `/review?model=${model}`, form); },
    confirm: (doc: string, values: Record<string,string>, note: string) => request<Agreement>('agreement', `/agreements/${id(doc)}/confirm`, {values,note}),
    reject: (doc: string, note: string) => request<Agreement>('agreement', `/agreements/${id(doc)}/reject`, {note}),
    invoices: (status = 'all', vendor = '') => request<{as_of?: string; count: number; invoices: Invoice[]}>('chaser', `/chaser/invoices${query({status,vendor,limit:'50'})}`),
    invoice: (invoice: string) => request<InvoiceDetail>('chaser', `/chaser/invoices/${id(invoice)}`),
    preview: (asOf: string) => request<Run>('chaser', `/chaser/preview${asOf ? query({as_of:asOf}) : ''}`, {}),
    run: (asOf: string) => request<Run>('chaser', `/chaser/run${asOf ? query({as_of:asOf}) : ''}`, {}),
    actions: (invoice = '') => request<Action[]>('chaser', `/chaser/actions${invoice ? query({invoice_id:invoice}) : ''}`),
    patterns: (vendor = '') => request<Pattern[]>('chaser', `/chaser/patterns${vendor ? query({vendor}) : ''}`),
    decide: (action: string, approve: boolean, note: string) => request<Action>('chaser', `/chaser/actions/${id(action)}/decide`, {approve,note}),
    ask: (question: string) => request<Answer>('chaser', '/chaser/ask', {question}),
    reply: (invoice: string, text: string) => request<{invoice_id: string; verdict: string; proof: string[]; read_by: string; claims_paid: boolean}>('chaser', '/chaser/reply', {invoice_id:invoice,text}),
    cases: () => request<WorkflowCases>('chaser', '/workflow/cases'),
    tick: () => request<WorkflowCases>('chaser', '/workflow/tick', {}),
    evidence: (body: EvidenceInput) => request<{document_id: string; cases: WorkflowCase[]}>('chaser', '/workflow/evidence', body),
    acknowledgeOutbox: (outbox: string, reference: string) => request<{status: 'delivered'}>('chaser', `/workflow/outbox/${id(outbox)}/ack`, {reference}),
    pipelineDispatch: (vendor = '') => request<{as_of: string; queued: string[]}>('chaser', `/pipeline/dispatch${vendor ? query({vendor}) : ''}`, {}),
    pipelineRun: (limit = 10) => request<{as_of: string; processed: Processed[]}>('chaser', `/pipeline/run${query({limit: String(limit)})}`, {}),
    pipelineRunInvoice: (invoice: string) => request<{processed: Processed[]; flow: PipelineFlow}>('chaser', `/pipeline/invoices/${id(invoice)}/run`, {}),
    pipelineTasks: (filters: {invoice_id?: string; agent?: string; status?: string} = {}) => request<AgentTask[]>('chaser', `/pipeline/tasks${Object.keys(filters).length ? query(filters as Record<string,string>) : ''}`),
    pipelineTask: (task: string) => request<AgentTask>('chaser', `/pipeline/tasks/${id(task)}`),
    pipelineFlow: (invoice: string) => request<PipelineFlow>('chaser', `/pipeline/flow/${id(invoice)}`),
    resolveCase: (invoice: string, note: string) => request<WorkflowCase>('chaser', `/workflow/cases/${id(invoice)}/resolve`, {note}),
  };
}

/** Controller confirmation accepts only absent required payment fields, not optional metadata. */
export function missingPaymentTerms(agreement: Pick<Agreement, 'missing'>): string[] {
  return agreement.missing.filter(name => ['payment_due', 'payment_amount', 'payment_info'].includes(name));
}
export function canRejectAgreement(agreement: Pick<Agreement, 'status'>): boolean {
  return ['needs_controller', 'active', 'confirmed'].includes(agreement.status);
}
export function canPersistRunDate(asOf: string, worldDate?: string): boolean {
  return !asOf || asOf === worldDate;
}
export function deferredWorkflowMessage(workflow?: WorkflowTrigger): string | null {
  if (!workflow || workflow.triggered) return null;
  return `Connected workflow deferred: ${workflow.error || 'Use Reconcile now or wait for the next worker tick.'}`;
}
/** Show useful recovery guidance without rendering arbitrary transport exception text. */
export function deliveryErrorMessage(code: string): string {
  switch (code) {
    case 'smtp_configuration_invalid': return 'SMTP connection settings are invalid.';
    case 'smtp_not_configured': return 'SMTP delivery is enabled but connection settings are missing.';
    case 'recipient_not_configured': return 'A recipient is not configured for this communication.';
    case 'prior_attempt_requires_human_receipt': return 'A prior delivery attempt needs a human receipt before further action.';
    case 'SMTPAuthenticationError': return 'SMTP authentication failed. Check the configured credentials.';
    default: return 'Delivery could not be confirmed. Review the external receipt before retrying.';
  }
}
export function deadlineConflict(detail: Pick<InvoiceDetail, 'ledger' | 'chaser_today'>): {ledger: string; agreement: string} | null {
  const ledger = detail.ledger && typeof detail.ledger === 'object' && 'due_date' in detail.ledger ? detail.ledger.due_date : null;
  const agreement = detail.chaser_today?.due_date;
  return typeof ledger === 'string' && agreement && ledger !== agreement ? {ledger, agreement} : null;
}
