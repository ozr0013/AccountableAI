import { schema, table, t, SenderError } from 'spacetimedb/server';

// Live mirror of the AccountableAI event log. PostgreSQL stays the source of truth; this module
// lets the dashboard subscribe to new agent work. Only the identity that published the module
// may write, and events are append-only: there are no update or delete reducers.
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
});
export default spacetimedb;

export const init = spacetimedb.init(ctx => {
  ctx.db.writer.insert({ identity: ctx.sender });
});

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
    if (!ctx.db.writer.identity.find(ctx.sender)) {
      throw new SenderError('only the AccountableAI backend may publish events');
    }
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
