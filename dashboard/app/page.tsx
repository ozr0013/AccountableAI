"use client";

import Link from "next/link";
import { useFeed } from "@/components/Feed";
import { ItemsTable } from "@/components/ItemsTable";
import { Badge, Loading, PageTop, SectionTitle, Spark } from "@/components/ui";
import { ALERT, money, named } from "@/lib/format";
import type { State } from "@/lib/types";

function monthly(state: State, key: "funded_on" | "paid_on", val: "funded" | "paid") {
  const by: Record<string, number> = {};
  for (const it of state.items) {
    const d = it[key];
    if (d) by[d.slice(0, 7)] = (by[d.slice(0, 7)] ?? 0) + it[val];
  }
  return Object.keys(by).sort().map((k) => by[k]);
}

export default function Overview() {
  const { state } = useFeed();
  if (!state) return <><PageTop title="Overview" /><Loading /></>;
  const h = state.headline;
  const names = state.meta.names;
  const moment = state.history.at(-1)?.diff;
  const was = moment?.unexplained[0];
  const vendors = h.unexplained_items.map((i) => state.items.find((x) => x.id === i)!).filter(Boolean);
  const flagged = state.alerts.filter((a) => a.open);
  const notKeyway = flagged.filter((a) => ["owner_pending_approval", "paperwork_request", "bank_detail_change"].includes(a.kind)).length;
  const funding = state.cases.filter((c) => c.kind === "funding").at(-1);

  return (
    <>
      <PageTop title="Overview" sub={`${state.meta.parent} funds ${state.meta.manager}, which pays ${state.meta.vendors} vendors on Oakline's behalf.`} />

      <section className="moment">
        <div>
          <span className="label">/ Before you send Keyway {money(state.meta.funding_pending, false)}</span>
          <div className="moment-amount num" style={{ marginTop: 14 }}>
            {was != null && was !== h.unexplained && <span className="strike" style={{ opacity: .45, marginRight: 16 }}>{money(was, false)}</span>}
            {money(h.proposed_hold, false)}<small>recommended hold</small>
          </div>
          <p>
            Keyway still owes {vendors.length} vendor{vendors.length === 1 ? "" : "s"} {money(h.unexplained, false)} from money you already sent
            {vendors.length ? ` (${vendors.map((v) => `${v.vendor} ${money(v.owed, false)}`).join(", ")})` : ""}.
            No bank record or vendor confirmation shows them paid. Chased {h.times_chased} times.
          </p>
        </div>
        <div className="moment-actions">
          <Link href="/cases" className="btn on-wine">Review the case</Link>
          <Link href="/console" className="pill on-wine">Judge Console</Link>
        </div>
      </section>

      <div className="grid g4" style={{ marginTop: 16 }}>
        <div className="card stat">
          <p className="card-label">Funded to Keyway</p>
          <div className="stat-value num">{money(h.funded, false)}</div>
          <div className="stat-sub micro warn">Jan to Apr 2026 · {state.items.length} invoices</div>
          <div className="stat-chart"><Spark values={monthly(state, "funded_on", "funded")} /></div>
        </div>
        <div className="card stat">
          <p className="card-label">Confirmed paid to vendors</p>
          <div className="stat-value num">{money(h.paid, false)}</div>
          <div className="stat-sub micro ok">{((h.paid / h.funded) * 100).toFixed(1)}% of funded · bank + vendor proof</div>
          <div className="stat-chart"><Spark values={monthly(state, "paid_on", "paid")} tone="g" /></div>
        </div>
        <div className="card stat">
          <p className="card-label">Still owed by Keyway</p>
          <div className="stat-value num">{money(h.owed, false)}</div>
          <div className="stat-sub micro warn">{money(h.unexplained, false)} unexplained · {money(h.held, false)} authorized hold</div>
          <div className="stat-chart">
            <div className="bar" style={{ height: 22 }}>
              <div style={{ width: `${(h.unexplained / h.owed) * 100}%`, background: "var(--o)" }} title="Unexplained" />
              <div style={{ width: `${(h.held / h.owed) * 100}%`, background: "var(--g-ink)" }} title="Authorized hold" />
              <div style={{ flex: 1, background: "var(--n6)" }} title="Explained, not Keyway's" />
            </div>
          </div>
        </div>
        <div className="card stat">
          <p className="card-label">Open alerts</p>
          <div className="stat-value num">{h.open_alerts}</div>
          <div className="stat-sub micro"><span className="warn">{h.unexplained_items.length} on Keyway</span><span className="faint">·</span><span className="ok">{notKeyway} not Keyway&apos;s fault</span></div>
          <div className="stat-chart micro muted">Every alert cites its sources · verifier checked</div>
        </div>
      </div>

      <div className="grid g-main" style={{ marginTop: 16 }}>
        <div className="card">
          <div className="between" style={{ marginBottom: 16 }}>
            <p className="card-label" style={{ margin: 0 }}>Agent activity</p>
            <span className="micro muted">{state.meta.documents} documents · {state.meta.events} events</span>
          </div>
          <div className="rows">
            {state.agents.map((a) => (
              <div key={a.agent} className="arow">
                <Badge kind={a.flagged ? "flag" : "done"} />
                <div className="arow-main">
                  <span className="arow-title">{a.agent}</span>
                  <div className="arow-sub">
                    <span className="micro ok">{a.done}</span>
                    {a.flagged && <span className="micro warn">{a.flagged}</span>}
                  </div>
                </div>
                <div className="arow-end"><span className="tag neutral">{a.kind === "code" ? "Code" : "Agent"}</span></div>
              </div>
            ))}
          </div>
        </div>
        <div className="card">
          <p className="card-label" style={{ marginBottom: 16 }}>Flagged for you</p>
          <div className="rows">
            {flagged.map((a) => (
              <Link key={a.id} href={a.kind === "recurring_pattern" ? "/keyway" : `/items/${a.invoice_ids[0]}`} className="arow">
                <Badge kind={a.kind === "unexplained_unpaid" ? "flag" : "idle"} />
                <div className="arow-main">
                  <span className="arow-title">{ALERT[a.kind] ?? a.kind}</span>
                  <div className="arow-sub">
                    <span className="micro muted" style={{ whiteSpace: "normal" }}>{named(a.headline, names).split(": ").slice(-1)[0]}</span>
                  </div>
                </div>
                <div className="arow-end"><span className={`tag ${a.kind === "unexplained_unpaid" ? "" : "neutral"}`}>Review</span></div>
              </Link>
            ))}
          </div>
        </div>
      </div>

      <section className="section">
        <SectionTitle right={<Link href="/items" className="link">All open items</Link>}>Keyway&apos;s open items, by vendor money still owed</SectionTitle>
        <div className="card" style={{ padding: "16px 8px 4px" }}>
          <ItemsTable state={state} />
        </div>
      </section>

      <footer className="foot">
        <span className="label">/ Seats filled</span>
        <p className="foot-line">
          The accounts-payable reviewer who confirms every vendor got paid from the money we sent the middleman,
          and the collections person who chases the middleman when they didn&apos;t.
        </p>
        <div className="foot-grid">
          <div><div className="v num">{state.meta.documents}</div><div className="micro" style={{ opacity: .7 }}>documents read</div></div>
          <div><div className="v num">{state.items.length}</div><div className="micro" style={{ opacity: .7 }}>invoices traced, funded to paid</div></div>
          <div><div className="v num">{h.reminders_sent}</div><div className="micro" style={{ opacity: .7 }}>reminders from approved templates</div></div>
          <div><div className="v num">0</div><div className="micro" style={{ opacity: .7 }}>dollars moved by an agent</div></div>
        </div>
        {funding && <p className="micro" style={{ opacity: .55, marginTop: 28 }}>Case {funding.id} · proposed by {funding.proposed_by} · {funding.status}</p>}
      </footer>
    </>
  );
}
