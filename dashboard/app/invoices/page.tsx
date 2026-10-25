"use client";

// Invoices (UI revamp 2): Accounts Payable. Filter by property, vendor and status (or search from
// the top bar), see the totals, the aging and the trend, and open any invoice from the ledger.

import { Loading, PageTop } from "@/components/ui";
import { money } from "@/lib/format";
import { dateLabel, sums } from "@/lib/workspace";
import { AgingChart, InvoiceStats, Ledger, PeriodTabs, STATUSES, TrendChart, WsHead } from "@/components/workspace/parts";
import { filtered, useWs } from "@/components/workspace/Workspace";

export default function Invoices() {
  const ws = useWs();
  if (!ws.ready) return <><PageTop title="Accounts Payable" /><Loading /></>;
  const rows = filtered(ws), s = sums(rows), f = ws.filters;
  const vendors = [...new Set(ws.invoices.map(i => i.vendor))].sort();
  return <>
    <WsHead crumb={<>Invoices &nbsp;›<b>{ws.profile.company}</b></>} title="Accounts Payable" exportRows={rows} />
    <div className="ws-filterbar">
      <select aria-label="Filter by property" value={f.filterProperty} onChange={(e) => ws.setFilter("filterProperty", e.target.value)}>
        {["All Properties", ...ws.properties].map(p => <option key={p}>{p}</option>)}
      </select>
      <select aria-label="Filter by vendor" value={f.vendor} onChange={(e) => ws.setFilter("vendor", e.target.value)}>
        {["All Vendors", ...vendors].map(v => <option key={v}>{v}</option>)}
      </select>
      <select aria-label="Filter by status" value={f.status} onChange={(e) => ws.setFilter("status", e.target.value)}>
        {STATUSES.map(st => <option key={st}>{st}</option>)}
      </select>
      {f.query && <span className="ws-pill">Search: {f.query}</span>}
      <button type="button" className="ws-btn" onClick={ws.clearFilters}>Clear filters</button>
    </div>
    <InvoiceStats rows={rows} />
    <div className="ws-halves">
      <section className="lr-box ws-card">
        <div className="ws-charthead"><h3 className="ws-h3">Aging of the Payables</h3>
          <div className="ws-muted" style={{ textAlign: "right" }}>Current: <b>{money(s.out)}</b><br />Past due: <b>{money(s.past)}</b></div></div>
        <AgingChart rows={rows} />
      </section>
      <section className="lr-box ws-card">
        <div className="ws-charthead"><div><h3 className="ws-h3">Payables Trend</h3><div className="ws-muted">{dateLabel(ws.start, ws.end)}</div></div><PeriodTabs /></div>
        <TrendChart rows={rows} />
        <div className="ws-muted">Invoiced amount by due date</div>
      </section>
    </div>
    <Ledger rows={rows} />
  </>;
}
