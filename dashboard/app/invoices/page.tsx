"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { causeTag } from "@/components/ItemsTable";
import { useFeed } from "@/components/Feed";
import { Loading, PageTop } from "@/components/ui";
import { lateToPay, leftToPay, notFunded, totals } from "@/lib/balances";
import { money } from "@/lib/format";
import type { Item } from "@/lib/types";

// The same rules as every other page (lib/balances.ts), so a filter here shows exactly the bills
// behind a box anywhere else.
const FILTERS: { id: string; label: string; keep: (i: Item) => boolean }[] = [
  { id: "all", label: "All", keep: () => true },
  { id: "attention", label: "Needs attention", keep: (i) => i.attention },
  { id: "left", label: "Left to pay", keep: (i) => leftToPay(i) > 0 },
  { id: "late", label: "Past due", keep: lateToPay },
  { id: "unfunded", label: "Not yet funded", keep: (i) => notFunded(i) > 0 },
  { id: "paid", label: "Paid", keep: (i) => i.paid > 0 },
  { id: "paidlate", label: "Paid late", keep: (i) => i.paid_late },
];
const short = (iso: string | null) => (iso ? new Date(`${iso}T12:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" }) : "—");

/** Every invoice Keyway was funded for: where the work was, what was paid, what's still owed. */
export default function Invoices() {
  const { state } = useFeed();
  const [filter, setFilter] = useState("all");
  const [q, setQ] = useState("");
  const [home, setHome] = useState<string | null>(null);
  useEffect(() => {
    const u = new URLSearchParams(window.location.search);
    setHome(u.get("property"));
    if (FILTERS.some((f) => f.id === u.get("show"))) setFilter(u.get("show")!);
  }, []);
  const rows = useMemo(() => {
    if (!state) return [];
    const f = FILTERS.find((x) => x.id === filter) ?? FILTERS[0];
    const needle = q.trim().toLowerCase();
    return state.items.filter((i) => f.keep(i) && (!home || i.property_id === home)
      && (!needle || `${i.id} ${i.vendor} ${i.property ?? ""}`.toLowerCase().includes(needle)))
      .sort((a, b) => Number(b.attention) - Number(a.attention) || (b.amount - b.paid) - (a.amount - a.paid) || (b.received ?? "").localeCompare(a.received ?? ""));
  }, [state, filter, q, home]);
  if (!state) return <><PageTop title="Invoices" /><Loading /></>;
  const homeLabel = home ? state.properties?.find((p) => p.id === home) : null;
  const t = totals(rows);
  const box = (id: string, label: string, v: number, cls = "") => (
    <button type="button" className={`${cls}${filter === id ? " on" : ""}`} onClick={() => setFilter(id)}><span>{label}</span><strong>{money(v)}</strong></button>
  );

  return <>
    <PageTop title="Invoices" sub={homeLabel ? `${homeLabel.address}, ${homeLabel.city} ${homeLabel.state}` : "Every vendor bill: billed = paid + left to pay + not yet funded."} />
    <div className="ov-boxes inv-boxes">
      {box("all", `Billed · ${rows.length} bill${rows.length === 1 ? "" : "s"}`, t.billed)}
      {box("paid", "Paid to vendors", t.paid)}
      {box("left", "Left to pay by Keyway", t.left)}
      {box("unfunded", "Not yet funded by Oakline", t.notFunded)}
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
        <thead><tr><th>Invoice</th><th>Home</th><th>Vendor</th><th className="r">Billed</th><th className="r">Paid</th><th className="r">Left to pay</th><th className="r">Not funded</th><th>Received</th><th>Due</th><th>Status</th></tr></thead>
        <tbody>{rows.map((i) => (
          <tr key={i.id} className={i.attention ? "u-alert" : ""}>
            <td className="mono"><Link href={`/items/${i.id}`}>{i.id}</Link></td>
            <td>{i.property ?? "—"}</td><td>{i.vendor}</td>
            <td className="r num">{money(i.amount)}</td><td className="r num">{money(i.paid)}</td>
            <td className={`r num${lateToPay(i) ? " late-ink" : ""}`}>{leftToPay(i) > 0 ? money(leftToPay(i)) : "—"}</td>
            <td className="r num">{notFunded(i) > 0 ? money(notFunded(i)) : "—"}</td>
            <td className="mono">{short(i.received)}</td><td className="mono">{short(i.due)}</td>
            <td>{i.owed > 0 || i.attention ? causeTag(i) : <span className="tag ok">{i.paid_late ? "Paid late" : "Paid"}</span>}</td>
          </tr>))}
        </tbody>
      </table>
      {!rows.length && <p className="sim-log-empty">No invoices match.</p>}
    </div>
  </>;
}
