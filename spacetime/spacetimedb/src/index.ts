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
