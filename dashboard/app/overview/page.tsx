"use client";

import Link from "next/link";
import { Spotlight } from "@/components/Spotlight";
import { Disclosure } from "@/components/Disclosure";
import { useFeed } from "@/components/Feed";
import { Badge, Loading, PageTop } from "@/components/ui";
import { ALERT, money } from "@/lib/format";

export default function Overview() {
  const { state } = useFeed();
  if (!state) return <><PageTop title="Overview" /><Loading /></>;
  const h = state.headline;
  const pending = state.cases.filter(c => c.status === "pending");
  const flagged = state.alerts.filter(a => a.open);
  const priorities = [...flagged].sort((a,b) => Number(b.kind === "unexplained_unpaid") - Number(a.kind === "unexplained_unpaid")).slice(0,3);
  const instruction = (state.decisions ?? []).filter(d => d.type === "funding_instruction").at(-1);
  const fundingPending = pending.some(c => c.kind === "funding");

  return (
    <>
      <PageTop title="Your money, accounted for." sub="A clear view of Keyway. Start with what needs you." />
      <Spotlight className="overview-hero">
        <div>
          <span className="label">/ {fundingPending ? "Your next decision" : instruction ? "Decision recorded" : "Your funding overview"}</span>
          <div className="moment-amount num" style={{ marginTop: 20 }}>{money(instruction && !fundingPending ? instruction.hold : h.proposed_hold, false)}<small>{instruction && !fundingPending ? "hold recorded" : "recommended hold"}</small></div>
          <p>{h.unexplained_items.length} invoices still need payment proof.</p>
        </div>
        <div className="moment-actions">
          <Link href="/cases" className="btn on-wine">{pending.length ? `Review ${pending.length} pending ${pending.length === 1 ? "case" : "cases"}` : "View decisions"} <span aria-hidden="true">↗</span></Link>
          <span className="micro" style={{ color: "#ddd", lineHeight: "16px" }}>You decide. No money moves here.</span>
        </div>
      </Spotlight>

      <div className="quiet-metrics">
        <Link href="/keyway"><span>Funded to Keyway</span><strong>{money(h.funded, false)}</strong><small>View reconciliation ↗</small></Link>
        <Link href="/keyway"><span>Confirmed paid</span><strong>{money(h.paid, false)}</strong><small>Bank or vendor proof ↗</small></Link>
        <Link href="/items"><span>Still owed</span><strong>{money(h.owed, false)}</strong><small>{money(h.unexplained, false)} unexplained ↗</small></Link>
      </div>

      <section className="focus-panel">
        <div className="focus-heading"><div><span className="label muted">/ Start here</span><h2>Needs a closer look.</h2></div><Link href="/items" className="pill">All open items ↗</Link></div>
        <div className="focus-list">
          {priorities.length === 0 && <p className="body muted">No open alerts. You’re caught up.</p>}
          {priorities.map(a => {
            const item = state.items.find(i => i.id === a.invoice_ids[0]);
            return <Link key={a.id} href={a.kind === "recurring_pattern" ? "/keyway" : `/items/${a.invoice_ids[0]}`} className="focus-row"><Badge kind={a.kind === "unexplained_unpaid" ? "flag" : "idle"} /><span><strong>{item?.vendor ?? "Keyway"}</strong><small>{ALERT[a.kind] ?? a.kind}</small></span><b>{money(a.amount, false)}</b><span aria-hidden="true">↗</span></Link>;
          })}
        </div>
        {flagged.length > priorities.length && <p className="body muted" style={{ margin: "18px 0 0" }}>{flagged.length - priorities.length} more alerts available in open items.</p>}
      </section>
      <Disclosure title="What happened behind the scenes" hint="Agent activity and source coverage" meta={`${state.meta.documents} records`}>
        <div className="rows">{state.agents.map(a => <div className="arow" key={a.agent}><Badge kind={a.flagged ? "flag" : "done"} /><div className="arow-main"><span className="arow-title">{a.agent}</span><span className="body muted">{a.done}{a.flagged ? ` · ${a.flagged}` : ""}</span></div></div>)}</div>
        <Link href="/agents" className="link" style={{ display: "inline-block", marginTop: 20 }}>Explore the team ↗</Link>
      </Disclosure>
    </>
  );
}
