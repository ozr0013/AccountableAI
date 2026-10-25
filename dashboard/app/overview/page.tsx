"use client";

import Link from "next/link";
import { Disclosure } from "@/components/Disclosure";
import { useFeed } from "@/components/Feed";
import { MoneyStrip } from "@/components/MoneyStrip";
import { FundingChart, OutstandingChart } from "@/components/OverviewCharts";
import { Badge, Loading, PageTop } from "@/components/ui";
import { ALERT, money } from "@/lib/format";

const SHOWN = 3;

export default function Overview() {
  const { state } = useFeed();
  if (!state) return <><PageTop title="Overview" /><Loading /></>;
  const items = state.items;
  const open = state.alerts.filter((a) => a.open && a.kind !== "recurring_pattern")
    .sort((a, b) => Number(b.kind === "unexplained_unpaid") - Number(a.kind === "unexplained_unpaid") || b.amount - a.amount);

  return (
    <>
      <PageTop title="Overview" sub="Where Oakline's money to Keyway Management stands today." />

      {open.length > 0 && (
        <section className="ov-alerts" aria-label="Needs a closer look">
          <div className="ov-alerts-head">
            <span className="label">Alert</span>
            <Link href="/cases" className="pill">Review queue ↗</Link>
          </div>
          <div className="ov-alerts-row">
            {open.slice(0, SHOWN).map((a) => {
              const item = items.find((i) => i.id === a.invoice_ids[0]);
              return <Link key={a.id} href={`/items/${a.invoice_ids[0]}`} className="ov-alert">
                <span className="ov-dot" aria-hidden="true" />
                <span className="ov-alert-txt"><b>{item?.vendor ?? "Keyway Management"}</b><small>{a.kind === "unexplained_unpaid" ? "Past due" : ALERT[a.kind] ?? a.kind.replace(/_/g, " ")}</small></span>
                <strong>{money(a.amount, false)}</strong>
              </Link>;
            })}
            {open.length > SHOWN && <Link href="/cases" className="ov-alert ov-more">+{open.length - SHOWN} more ↗</Link>}
          </div>
        </section>
      )}

      <MoneyStrip items={items} />

      <div className="ov-charts">
        <FundingChart items={items} asOf={state.meta.as_of} />
        <OutstandingChart items={items} />
      </div>

      <Disclosure title="What happened behind the scenes" hint="Agent activity and source coverage" meta={`${state.meta.documents} records`}>
        <div className="rows">{state.agents.map(a => <div className="arow" key={a.agent}><Badge kind={a.flagged ? "flag" : "done"} /><div className="arow-main"><span className="arow-title">{a.agent}</span><span className="body muted">{a.done}{a.flagged ? ` · ${a.flagged}` : ""}</span></div></div>)}</div>
        <Link href="/agents" className="link" style={{ display: "inline-block", marginTop: 20 }}>Explore the team ↗</Link>
      </Disclosure>
    </>
  );
}
