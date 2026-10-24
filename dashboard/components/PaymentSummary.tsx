"use client";
import Link from "next/link";
import type { State, Item } from "@/lib/types";
import { money } from "@/lib/format";

export function PaymentSummary({ state, items = state.items }: { state: State; items?: Item[] }) {
  const unique = items.filter(i => !i.duplicate_of);
  const invoiced = unique.reduce((n,i) => n+i.amount,0);
  const paid = unique.reduce((n,i) => n+i.paid,0);
  const overdue = unique.filter(i => i.owed > 0 && i.days_late > 0);
  const past = overdue.reduce((n,i) => n+i.owed,0);
  const outstanding = unique.filter(i => i.owed > 0 && i.days_late <= 0).reduce((n,i) => n+i.owed,0);
  const metrics = [
    {label:"Total invoiced",value:invoiced,note:`${unique.length} unique invoices`,tone:"neutral",icon:"▤"},
    {label:"Payments",value:paid,note:`${unique.filter(i => i.owed <= 0).length} settled invoices`,tone:"paid",icon:"✓"},
    {label:"Outstanding",value:outstanding,note:"Unpaid · not past due",tone:"indigo",icon:"▣"},
    {label:"Past due",value:past,note:`${overdue.length} invoices · all causes`,tone:"danger",icon:"◷"},
  ];
  const total = paid+outstanding+past;
  return <section className="ledger-panel"><h2>Status update</h2><div className="ledger-kpis">{metrics.map(m => <Link href="/items" key={m.label} className={`ledger-kpi ${m.tone}`}><div className="ledger-kpi-icon">{m.icon}<span>↗</span></div><span>{m.label}</span><strong>{money(m.value,false)}</strong><small>{m.note}</small></Link>)}</div><div className="ledger-distribution" aria-label="Payment breakdown">{[{v:paid,c:"#059669"},{v:outstanding,c:"#4f46e5"},{v:past,c:"#e11d48"}].map((p,i)=><span key={i} style={{width:`${total ? p.v/total*100 : 0}%`,background:p.c}} />)}</div><div className="ledger-legend"><span>● Payments {money(paid,false)}</span><span>● Outstanding {money(outstanding,false)}</span><span>● Past due {money(past,false)}</span></div></section>;
}

export function PaymentAnalytics({ state }: { state: State }) {
  const months: Record<string,number> = {};
  for(const i of state.items) if(i.paid_on && !i.duplicate_of) months[i.paid_on.slice(0,7)] = (months[i.paid_on.slice(0,7)] ?? 0)+i.paid;
  const entries = Object.entries(months).sort(([a],[b])=>a.localeCompare(b));
  const max = Math.max(1,...entries.map(([,v])=>v));
  const points = entries.map(([,v],i)=>`${40+i*520/Math.max(1,entries.length-1)},${190-v/max*150}`).join(" ");
  const trades: Record<string,number> = {};
  for(const i of state.items) if(i.owed>0 && !i.duplicate_of) trades[i.trade || "Other"]=(trades[i.trade || "Other"]??0)+i.owed;
  const groups=Object.entries(trades).sort((a,b)=>b[1]-a[1]);
  const total=groups.reduce((n,[,v])=>n+v,0);
  return <div className="ledger-analytics"><section className="ledger-panel"><div className="between"><h2>Payment trends</h2><span className="tag neutral">Monthly · recorded payments</span></div>{entries.length ? <><svg viewBox="0 0 600 220" role="img" aria-label="Monthly confirmed vendor payments" className="ledger-chart"><path d="M40 40H560 M40 115H560 M40 190H560" stroke="#e2e8f0" fill="none" /><polygon points={`40,190 ${points} 560,190`} fill="#4f46e51a"/><polyline points={points} stroke="#4f46e5" strokeWidth="3" fill="none"/>{entries.map(([m,v],i)=><circle key={m} cx={40+i*520/Math.max(1,entries.length-1)} cy={190-v/max*150} r="4" fill="#4f46e5"><title>{m}: {money(v)}</title></circle>)}</svg><div className="ledger-months">{entries.map(([m,v])=><div key={m}><span>{m}</span><strong>{money(v,false)}</strong></div>)}</div></> : <p className="body muted">No confirmed payments yet.</p>}</section><section className="ledger-panel"><h2>Unpaid by trade</h2><p className="body muted">Total: {money(total,false)}</p>{groups.map(([k,v],i)=><div className="trade-row" key={k}><div className="between"><span>{k}</span><b>{money(v,false)}</b></div><div><span style={{width:`${total ? v/total*100:0}%`,background:["#6366f1","#d97706","#0891b2","#64748b"][i%4]}} /></div></div>)}</section></div>;
}
