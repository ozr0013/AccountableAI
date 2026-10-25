"use client";

import Link from "next/link";
import { Disclosure } from "@/components/Disclosure";
import { useFeed } from "@/components/Feed";
import { FundingChart, OutstandingChart } from "@/components/OverviewCharts";
import { Badge, Loading, PageTop } from "@/components/ui";
import { flow } from "@/lib/balances";
import { ALERT, money } from "@/lib/format";

const SHOWN = 3;

export default function Overview() {
  const { state } = useFeed();
  if (!state) return <><PageTop title="Overview" /><Loading /></>;
  const items = state.items;
  const open = state.alerts.filter((a) => a.open && a.kind !== "recurring_pattern")
    .sort((a, b) => Number(b.kind === "unexplained_unpaid") - Number(a.kind === "unexplained_unpaid") || b.amount - a.amount);
  const pending = state.cases.find((c) => c.status === "pending" && c.kind === "funding");
  const f = flow(state);
  const fundedBills = items.filter((i) => i.funded > 0).length;
  const paidBills = items.filter((i) => i.paid > 0).length;
  const parts = [[f.owed, "owed"], [f.retained, "retained"], [f.duplicate, "billed twice"]] as const;
  const oldest = f.late.length ? Math.max(...f.late.map((i) => i.days_late)) : 0;

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
        <Link href="/keyway"><span>Paid by Oakline to Keyway</span><strong>{money(f.funded)}</strong><small>funding for {fundedBills} vendor bills ↗</small></Link>
        <Link href="/keyway"><span>Paid to vendors by Keyway</span><strong>{money(f.paid)}</strong><small>{paidBills} bills, proven by bank or vendor ↗</small></Link>
        <Link href="/invoices"><span>Left to pay</span><strong>{money(f.left)}</strong><small>{parts.filter(([v]) => v > 0).map(([v, w]) => `${money(v)} ${w}`).join(" · ")} ↗</small></Link>
        <Link href="/cases" className="late"><span>Past due</span><strong>{money(f.lateSum)}</strong><small>{f.late.length} bills{oldest ? `, oldest ${oldest} days` : ""}{f.lateUnexplained ? ` · ${money(f.lateUnexplained)} unexplained` : ""} ↗</small></Link>
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
