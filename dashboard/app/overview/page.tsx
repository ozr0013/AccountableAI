"use client";

import Link from "next/link";
import { Disclosure } from "@/components/Disclosure";
import { useFeed } from "@/components/Feed";
import { FundingChart, OutstandingChart, pastDue } from "@/components/OverviewCharts";
import { Badge, Loading, PageTop } from "@/components/ui";
import { ALERT, money } from "@/lib/format";

const SHOWN = 3;

export default function Overview() {
  const { state } = useFeed();
  if (!state) return <><PageTop title="Overview" /><Loading /></>;
  const items = state.items;
  const open = state.alerts.filter((a) => a.open && a.kind !== "recurring_pattern")
    .sort((a, b) => Number(b.kind === "unexplained_unpaid") - Number(a.kind === "unexplained_unpaid") || b.amount - a.amount);
  const pending = state.cases.find((c) => c.status === "pending" && c.kind === "funding");
  const billed = items.reduce((t, i) => t + i.amount, 0);
  const paid = items.reduce((t, i) => t + i.paid, 0);
  const unpaid = items.filter((i) => i.owed > 0);
  const late = items.filter(pastDue);
  const owed = unpaid.reduce((t, i) => t + i.owed, 0);
  const lateSum = late.reduce((t, i) => t + i.owed, 0);
  const funded = items.filter((i) => i.funded > 0);

  return (
    <>
      <PageTop title="Overview" sub="Where Oakline's money to Keyway stands today." />

      {open.length > 0 && (
        <section className="ov-alerts" aria-label="Needs a closer look">
          <div className="ov-alerts-head">
            <span className="label">/ Needs a closer look · {open.length}</span>
            {pending && <Link href="/cases" className="ov-hold">Recommended hold <b>{money(state.headline.proposed_hold, false)}</b> of the next funding · your decision ↗</Link>}
            <Link href="/cases" className="pill">Review queue ↗</Link>
          </div>
          <div className="ov-alerts-row">
            {open.slice(0, SHOWN).map((a) => {
              const item = items.find((i) => i.id === a.invoice_ids[0]);
              return <Link key={a.id} href={`/items/${a.invoice_ids[0]}`} className="ov-alert">
                <Badge kind={a.kind === "unexplained_unpaid" ? "flag" : "idle"} />
                <span className="ov-alert-txt"><b>{item?.vendor ?? "Keyway"}</b><small>{ALERT[a.kind] ?? a.kind.replace(/_/g, " ")}</small></span>
                <strong>{money(a.amount, false)}</strong>
              </Link>;
            })}
            {open.length > SHOWN && <Link href="/cases" className="ov-alert ov-more">+{open.length - SHOWN} more ↗</Link>}
          </div>
        </section>
      )}

      <div className="ov-boxes">
        <Link href="/keyway"><span>Sent to Keyway by Oakline</span><strong>{money(state.recon.funded)}</strong><small>funding for {funded.length} vendor bills ↗</small></Link>
        <Link href="/invoices"><span>Keyway has to pay vendors</span><strong>{money(billed)}</strong><small>{items.length} bills · {money(paid)} paid so far ↗</small></Link>
        <Link href="/invoices"><span>Outstanding</span><strong>{money(owed)}</strong><small>{unpaid.length} bills not paid to vendors yet ↗</small></Link>
        <Link href="/cases" className="late"><span>Past due</span><strong>{money(lateSum)}</strong><small>{late.length} bills past due{late.length ? ` · oldest ${Math.max(...late.map((i) => i.days_late))} days` : ""} ↗</small></Link>
      </div>

      <div className="ov-charts">
        <FundingChart items={items} />
        <OutstandingChart items={items} />
      </div>

      <Disclosure title="What happened behind the scenes" hint="Agent activity and source coverage" meta={`${state.meta.documents} records`}>
        <div className="rows">{state.agents.map(a => <div className="arow" key={a.agent}><Badge kind={a.flagged ? "flag" : "done"} /><div className="arow-main"><span className="arow-title">{a.agent}</span><span className="body muted">{a.done}{a.flagged ? ` · ${a.flagged}` : ""}</span></div></div>)}</div>
        <Link href="/agents" className="link" style={{ display: "inline-block", marginTop: 20 }}>Explore the team ↗</Link>
      </Disclosure>
    </>
  );
}
