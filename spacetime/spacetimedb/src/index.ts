import { schema, table, t, SenderError, type InferSchema, type ReducerCtx } from 'spacetimedb/server';

const RuleInput = t.object('RuleInput', {
  ruleId: t.string(),
  ruleType: t.string(),
  params: t.string(),
  check: t.string(),
  status: t.string(),
  sourceQuote: t.option(t.string()),
  location: t.option(t.string()),
});

// Live mirror of the AccountableAI PostgreSQL tables. PostgreSQL stays the source of truth; this
// module lets the dashboard subscribe to agent work. Only the identity that published the module
// may write. Events are append-only; cases and jobs are replaced by id; facts and dependencies
// are derived indexes, written once per key and cleared only for a full rebuild.
const spacetimedb = schema({
  writer: table(
    { name: 'writer' },
    { identity: t.identity().primaryKey() }
  ),
  event: table(
    { name: 'event', public: true },
    {
      eventId: t.string().primaryKey(),
      seq: t.i64().index('btree'),
      kind: t.string().index('btree'),
      managerId: t.option(t.string()),
      invoiceId: t.option(t.string()),
      validFrom: t.string(),
      learnedAt: t.string(),
      payload: t.string(),
      mirroredAt: t.timestamp(),
    }
  ),
  agentCase: table(
    { name: 'agent_case', public: true },
    {
      caseId: t.string().primaryKey(),
      ownerId: t.string(),
      managerId: t.string(),
      vendorId: t.string(),
      invoiceId: t.string().index('btree'),
      state: t.string().index('btree'),
      version: t.i32(),
      body: t.string(),
      mirroredAt: t.timestamp(),
    }
  ),
  scheduledJob: table(
    { name: 'scheduled_job', public: true },
    {
      jobId: t.string().primaryKey(),
      caseId: t.string().index('btree'),
      scheduleKey: t.string(),
      dueAt: t.string(),
      status: t.string().index('btree'),
      attempts: t.i32(),
      leaseOwner: t.option(t.string()),
      leaseUntil: t.option(t.string()),
      lastError: t.option(t.string()),
      payload: t.string(),
      mirroredAt: t.timestamp(),
    }
  ),
  eventFact: table(
    { name: 'event_fact', public: true },
    {
      key: t.string().primaryKey(),
      eventId: t.string().index('btree'),
      factKey: t.string().index('btree'),
    }
  ),
  agreement: table(
    { name: 'agreement', public: true },
    {
      docId: t.string().primaryKey(),
      filename: t.string(),
      kind: t.string(),
      ownerName: t.option(t.string()),
      vendorName: t.option(t.string()),
      paymentDue: t.option(t.string()),
      paymentAmount: t.option(t.string()),
      paymentInfo: t.option(t.string()),
      effectiveDate: t.option(t.string()),
      complete: t.bool(),
      missing: t.array(t.string()),
      escalationSubject: t.option(t.string()),
      escalationBody: t.option(t.string()),
      fields: t.string(),
      steps: t.string(),
      mode: t.string(),
      costUsd: t.f64(),
      status: t.string(),
      confirmedBy: t.option(t.string()),
      controllerNote: t.option(t.string()),
      reviewedAt: t.timestamp(),
    }
  ),
  agreementRule: table(
    { name: 'agreement_rule', public: true },
    {
      ruleId: t.string().primaryKey(),
      docId: t.string().index('btree'),
      vendorName: t.string(),
      ruleType: t.string().index('btree'),
      params: t.string(),
      check: t.string(),
      status: t.string(),
      sourceQuote: t.option(t.string()),
      location: t.option(t.string()),
      createdAt: t.timestamp(),
    }
  ),
  chaseAction: table(
    { name: 'chase_action', public: true },
    {
      actionId: t.string().primaryKey(),
      invoiceId: t.string().index('btree'),
      vendorName: t.string(),
      action: t.string(),
      level: t.option(t.string()),
      dueDate: t.option(t.string()),
      daysOverdue: t.i32(),
      amountOwed: t.f64(),
      message: t.option(t.string()),
      reason: t.string(),
      ruleId: t.option(t.string()),
      ruleQuote: t.option(t.string()),
      status: t.string(),
      asOf: t.string(),
      createdAt: t.timestamp(),
    }
  ),
  chaseDecision: table(
    { name: 'chase_decision', public: true },
    {
      actionId: t.string().primaryKey(),
      status: t.string(),
      decidedBy: t.string(),
      note: t.option(t.string()),
      decidedAt: t.timestamp(),
    }
  ),
  agentTask: table(
    { name: 'agent_task', public: true },
    {
      taskId: t.string().primaryKey(),
      agent: t.string().index('btree'),
      invoiceId: t.string().index('btree'),
      status: t.string().index('btree'),
      createdBy: t.string(),
      reason: t.string(),
      payload: t.string(),
      outcome: t.string(),
      trace: t.string(),
      asOf: t.string(),
      createdAt: t.timestamp(),
      updatedAt: t.timestamp(),
    }
  ),
  dependency: table(
    { name: 'dependency', public: true },
    {
      key: t.string().primaryKey(),
      itemId: t.string().index('btree'),
      factKey: t.string().index('btree'),
    }
  ),
});
export default spacetimedb;

export const init = spacetimedb.init(ctx => {
  ctx.db.writer.insert({ identity: ctx.sender });
});

type Ctx = ReducerCtx<InferSchema<typeof spacetimedb>>;

function requireWriter(ctx: Ctx): void {
  if (!ctx.db.writer.identity.find(ctx.sender)) {
    throw new SenderError('only the AccountableAI backend may publish events');
  }
}

export const publishEvent = spacetimedb.reducer(
  {
    eventId: t.string(),
    seq: t.i64(),
    kind: t.string(),
    managerId: t.option(t.string()),
    invoiceId: t.option(t.string()),
    validFrom: t.string(),
    learnedAt: t.string(),
    payload: t.string(),
  },
  (ctx, args) => {
    requireWriter(ctx);
    // Replays and retries are safe: an event id is written once.
    if (ctx.db.event.eventId.find(args.eventId)) {
      return;
    }
    ctx.db.event.insert({
      ...args,
      managerId: args.managerId ?? undefined,
      invoiceId: args.invoiceId ?? undefined,
      mirroredAt: ctx.timestamp,
    });
  }
);

export const upsertCase = spacetimedb.reducer(
  {
    caseId: t.string(),
    ownerId: t.string(),
    managerId: t.string(),
    vendorId: t.string(),
    invoiceId: t.string(),
    state: t.string(),
    version: t.i32(),
    body: t.string(),
  },
  (ctx, args) => {
    requireWriter(ctx);
    const row = { ...args, mirroredAt: ctx.timestamp };
    if (ctx.db.agentCase.caseId.find(args.caseId)) {
      ctx.db.agentCase.caseId.update(row);
    } else {
      ctx.db.agentCase.insert(row);
    }
  }
);

export const upsertJob = spacetimedb.reducer(
  {
    jobId: t.string(),
    caseId: t.string(),
    scheduleKey: t.string(),
    dueAt: t.string(),
    status: t.string(),
    attempts: t.i32(),
    leaseOwner: t.option(t.string()),
    leaseUntil: t.option(t.string()),
    lastError: t.option(t.string()),
    payload: t.string(),
  },
  (ctx, args) => {
    requireWriter(ctx);
    const row = {
      ...args,
      leaseOwner: args.leaseOwner ?? undefined,
      leaseUntil: args.leaseUntil ?? undefined,
      lastError: args.lastError ?? undefined,
      mirroredAt: ctx.timestamp,
    };
    if (ctx.db.scheduledJob.jobId.find(args.jobId)) {
      ctx.db.scheduledJob.jobId.update(row);
    } else {
      ctx.db.scheduledJob.insert(row);
    }
  }
);

export const putFact = spacetimedb.reducer(
  { eventId: t.string(), factKey: t.string() },
  (ctx, { eventId, factKey }) => {
    requireWriter(ctx);
    const key = `${eventId}|${factKey}`;
    if (!ctx.db.eventFact.key.find(key)) {
      ctx.db.eventFact.insert({ key, eventId, factKey });
    }
  }
);

export const putDependency = spacetimedb.reducer(
  { itemId: t.string(), factKey: t.string() },
  (ctx, { itemId, factKey }) => {
    requireWriter(ctx);
    const key = `${itemId}|${factKey}`;
    if (!ctx.db.dependency.key.find(key)) {
      ctx.db.dependency.insert({ key, itemId, factKey });
    }
  }
);

// Facts and dependencies are rebuilt from the event log; clear them before a full resync.
export const clearDerived = spacetimedb.reducer(ctx => {
  requireWriter(ctx);
  for (const row of [...ctx.db.eventFact.iter()]) ctx.db.eventFact.key.delete(row.key);
  for (const row of [...ctx.db.dependency.iter()]) ctx.db.dependency.key.delete(row.key);
});

// One row per reviewed document; a re-review of the same file replaces its row.
export const saveAgreement = spacetimedb.reducer(
  {
    docId: t.string(),
    filename: t.string(),
    kind: t.string(),
    ownerName: t.option(t.string()),
    vendorName: t.option(t.string()),
    paymentDue: t.option(t.string()),
    paymentAmount: t.option(t.string()),
    paymentInfo: t.option(t.string()),
    effectiveDate: t.option(t.string()),
    complete: t.bool(),
    missing: t.array(t.string()),
    escalationSubject: t.option(t.string()),
    escalationBody: t.option(t.string()),
    fields: t.string(),
    steps: t.string(),
    mode: t.string(),
    costUsd: t.f64(),
    status: t.string(),
    confirmedBy: t.option(t.string()),
    controllerNote: t.option(t.string()),
  },
  (ctx, args) => {
    requireWriter(ctx);
    const row = {
      ...args,
      ownerName: args.ownerName ?? undefined,
      vendorName: args.vendorName ?? undefined,
      paymentDue: args.paymentDue ?? undefined,
      paymentAmount: args.paymentAmount ?? undefined,
      paymentInfo: args.paymentInfo ?? undefined,
      effectiveDate: args.effectiveDate ?? undefined,
      escalationSubject: args.escalationSubject ?? undefined,
      escalationBody: args.escalationBody ?? undefined,
      confirmedBy: args.confirmedBy ?? undefined,
      controllerNote: args.controllerNote ?? undefined,
      reviewedAt: ctx.timestamp,
    };
    if (ctx.db.agreement.docId.find(args.docId)) {
      ctx.db.agreement.docId.update(row);
    } else {
      ctx.db.agreement.insert(row);
    }
  }
);

// Replace the rules derived from one agreement in a single transaction.
export const setAgreementRules = spacetimedb.reducer(
  { docId: t.string(), vendorName: t.string(), rules: t.array(RuleInput) },
  (ctx, { docId, vendorName, rules }) => {
    requireWriter(ctx);
    for (const old of [...ctx.db.agreementRule.docId.filter(docId)]) {
      ctx.db.agreementRule.ruleId.delete(old.ruleId);
    }
    for (const r of rules) {
      ctx.db.agreementRule.insert({
        ...r,
        docId,
        vendorName,
        sourceQuote: r.sourceQuote ?? undefined,
        location: r.location ?? undefined,
        createdAt: ctx.timestamp,
      });
    }
  }
);

// One row per chaser action; the id makes a same-day rerun a no-op.
export const recordChase = spacetimedb.reducer(
  {
    actionId: t.string(),
    invoiceId: t.string(),
    vendorName: t.string(),
    action: t.string(),
    level: t.option(t.string()),
    dueDate: t.option(t.string()),
    daysOverdue: t.i32(),
    amountOwed: t.f64(),
    message: t.option(t.string()),
    reason: t.string(),
    ruleId: t.option(t.string()),
    ruleQuote: t.option(t.string()),
    status: t.string(),
    asOf: t.string(),
  },
  (ctx, args) => {
    requireWriter(ctx);
    if (ctx.db.chaseAction.actionId.find(args.actionId)) {
      return;
    }
    ctx.db.chaseAction.insert({
      ...args,
      level: args.level ?? undefined,
      dueDate: args.dueDate ?? undefined,
      message: args.message ?? undefined,
      ruleId: args.ruleId ?? undefined,
      ruleQuote: args.ruleQuote ?? undefined,
      createdAt: ctx.timestamp,
    });
  }
);

// A controller's decision on a chaser draft (approve / reject); one decision per action.
export const decideChase = spacetimedb.reducer(
  { actionId: t.string(), status: t.string(), decidedBy: t.string(), note: t.option(t.string()) },
  (ctx, args) => {
    requireWriter(ctx);
    if (!ctx.db.chaseAction.actionId.find(args.actionId)) {
      throw new SenderError('unknown chaser action');
    }
    if (ctx.db.chaseDecision.actionId.find(args.actionId)) {
      throw new SenderError('this action was already decided');
    }
    ctx.db.chaseDecision.insert({ ...args, note: args.note ?? undefined, decidedAt: ctx.timestamp });
  }
);

// Development reset: clear the chaser's actions and decisions (agreements and rules stay).
export const clearChaser = spacetimedb.reducer(ctx => {
  requireWriter(ctx);
  for (const row of [...ctx.db.chaseDecision.iter()]) ctx.db.chaseDecision.actionId.delete(row.actionId);
  for (const row of [...ctx.db.chaseAction.iter()]) ctx.db.chaseAction.actionId.delete(row.actionId);
});

// Agent-to-agent queue. A task is created once (same id = no-op) and then moves
// pending -> running -> done | failed, carrying the agent's outcome and full trace.
export const enqueueTask = spacetimedb.reducer(
  { taskId: t.string(), agent: t.string(), invoiceId: t.string(), createdBy: t.string(),
    reason: t.string(), payload: t.string(), asOf: t.string() },
  (ctx, args) => {
    requireWriter(ctx);
    if (ctx.db.agentTask.taskId.find(args.taskId)) {
      return;
    }
    ctx.db.agentTask.insert({ ...args, status: 'pending', outcome: '{}', trace: '{}',
      createdAt: ctx.timestamp, updatedAt: ctx.timestamp });
  }
);

export const updateTask = spacetimedb.reducer(
  { taskId: t.string(), status: t.string(), outcome: t.string(), trace: t.string() },
  (ctx, { taskId, status, outcome, trace }) => {
    requireWriter(ctx);
    const row = ctx.db.agentTask.taskId.find(taskId);
    if (!row) {
      throw new SenderError('unknown task');
    }
    ctx.db.agentTask.taskId.update({ ...row, status, outcome, trace, updatedAt: ctx.timestamp });
  }
);

// Development reset for the agent queue.
export const clearTasks = spacetimedb.reducer(ctx => {
  requireWriter(ctx);
  for (const row of [...ctx.db.agentTask.iter()]) ctx.db.agentTask.taskId.delete(row.taskId);
});
