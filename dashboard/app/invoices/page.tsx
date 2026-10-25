"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { causeTag } from "@/components/ItemsTable";
import { useFeed } from "@/components/Feed";
import { Loading, PageTop } from "@/components/ui";
import { balances } from "@/lib/balances";
import { money } from "@/lib/format";
import type { Item } from "@/lib/types";

const FILTERS: { id: string; label: string; keep: (i: Item) => boolean }[] = [
  { id: "all", label: "All", keep: () => true },
  { id: "attention", label: "Needs attention", keep: (i) => i.attention },
  { id: "unpaid", label: "Unpaid", keep: (i) => i.owed > 0 },
  { id: "paid", label: "Paid", keep: (i) => i.owed <= 0 && !i.paid_late },
  { id: "late", label: "Paid late", keep: (i) => i.paid_late },
];
const short = (iso: string | null) => (iso ? new Date(`${iso}T12:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" }) : "—");

/** Every invoice Keyway was funded for: where the work was, what was paid, what's still owed. */
export default function Invoices() {
  const { state } = useFeed();
  const [filter, setFilter] = useState("all");
  const [q, setQ] = useState("");
  const [home, setHome] = useState<string | null>(null);
  useEffect(() => { setHome(new URLSearchParams(window.location.search).get("property")); }, []);
  const rows = useMemo(() => {
    if (!state) return [];
    const f = FILTERS.find((x) => x.id === filter) ?? FILTERS[0];
    const needle = q.trim().toLowerCase();
    return state.items.filter((i) => f.keep(i) && (!home || i.property_id === home)
      && (!needle || `${i.id} ${i.vendor} ${i.property ?? ""}`.toLowerCase().includes(needle)))
      .sort((a, b) => Number(b.attention) - Number(a.attention) || b.owed - a.owed || (b.received ?? "").localeCompare(a.received ?? ""));
  }, [state, filter, q, home]);
  if (!state) return <><PageTop title="Invoices" /><Loading /></>;
  const homeLabel = home ? state.properties?.find((p) => p.id === home) : null;
  const total = (k: "amount" | "paid" | "owed") => rows.reduce((s, i) => s + i[k], 0);

  return <>
    <PageTop title="Invoices" sub={homeLabel ? `${homeLabel.address}, ${homeLabel.city} ${homeLabel.state}` : "Every vendor bill Keyway was funded for."} />
    <div className="sim-stats">
      <div><span>Invoices</span><strong>{rows.length}</strong><small>{state.items.length} in total</small></div>
      <div><span>Billed</span><strong>{money(total("amount"))}</strong><small>by vendors</small></div>
      <div><span>Paid to vendors</span><strong>{money(total("paid"))}</strong><small>bank or vendor proof</small></div>
      <div><span>Open vendor balance</span><strong>{money(total("owed"))}</strong><small>{filter === "all" && !home && !q ? `Keyway still owes ${money(balances(state).keyway)} of it` : `${rows.filter((i) => i.attention).length} need attention`}</small></div>
    </div>
    <div className="inv-bar">
      <div className="inv-filters" role="group" aria-label="Show">
        {FILTERS.map((f) => <button key={f.id} type="button" className={`chipbtn${filter === f.id ? " on" : ""}`} onClick={() => setFilter(f.id)}>{f.label}</button>)}
      </div>
      <input className="inv-search" type="search" placeholder="Search invoice, vendor or address" aria-label="Search invoices" value={q} onChange={(e) => setQ(e.target.value)} />
      {home && <Link className="pill" href="/invoices" onClick={() => setHome(null)}>All homes ✕</Link>}
    </div>
    <div className="table-scroll lr-box">
      <table className="table lr-table">
        <thead><tr><th>Invoice</th><th>Home</th><th>Vendor</th><th className="r">Amount</th><th className="r">Paid</th><th className="r">Owed</th><th>Received</th><th>Due</th><th>Status</th></tr></thead>
        <tbody>{rows.map((i) => (
          <tr key={i.id} className={i.attention ? "u-alert" : ""}>
            <td className="mono"><Link href={`/items/${i.id}`}>{i.id}</Link></td>
            <td>{i.property ?? "—"}</td><td>{i.vendor}</td>
            <td className="r num">{money(i.amount)}</td><td className="r num">{money(i.paid)}</td>
            <td className="r num">{i.owed > 0 ? money(i.owed) : "—"}</td>
            <td className="mono">{short(i.received)}</td><td className="mono">{short(i.due)}</td>
            <td>{i.owed > 0 || i.attention ? causeTag(i) : <span className="tag ok">{i.paid_late ? "Paid late" : "Paid"}</span>}</td>
          </tr>))}
        </tbody>
      </table>
      {!rows.length && <p className="sim-log-empty">No invoices match.</p>}
    </div>
  </>;
}
