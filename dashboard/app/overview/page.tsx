"use client";
import Link from "next/link";
import { useFeed } from "@/components/Feed";
import { PageTop, Loading } from "@/components/ui";
import { Disclosure } from "@/components/Disclosure";
import { PaymentSummary, PaymentAnalytics } from "@/components/PaymentSummary";
import { money, ALERT } from "@/lib/format";
export default function Overview(){
 const {state}=useFeed();
 if(!state)return <><PageTop title="Vendor payments"/><Loading/></>;
 const alerts=state.alerts.filter(a=>a.open && a.kind!=="recurring_pattern");
 const pending=state.cases.filter(c=>c.status==="pending");
 return <><div className="ledger-breadcrumb">Portfolio <span>›</span> {state.meta.parent}</div><PageTop title="Vendor payments" sub={`Payment oversight · ${state.meta.manager}`}/>
 <section className="ledger-panel review-panel"><div className="between"><h2><span className="ledger-alert">△</span> {alerts.length} alerts need review</h2><Link className="pill" href="/items">All invoices ↗</Link></div>{alerts.slice(0,2).map(a=><div className="ledger-review-row" key={a.id}><div><strong>{state.items.find(i=>i.id===a.invoice_ids[0])?.vendor || state.meta.manager}</strong><p>{ALERT[a.kind] || a.kind}</p></div><b>{money(a.amount,false)}</b><Link className="btn sm" href={`/items/${a.invoice_ids[0]}`}>Review</Link></div>)}{!alerts.length&&<p className="body muted">No alerts awaiting review.</p>}</section>
 <PaymentSummary state={state}/><PaymentAnalytics state={state}/>
 <Disclosure title="Controller decisions" hint="Funding holds and bank-detail reviews" meta={`${pending.length} pending`}><div className="between"><p className="body">Recommended funding hold: <strong>{money(state.headline.proposed_hold,false)}</strong>. Agents recommend; a controller decides.</p><Link className="btn" href="/cases">Open review queue</Link></div></Disclosure></>;
}
