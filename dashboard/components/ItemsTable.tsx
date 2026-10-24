"use client";

import Link from "next/link";
import { CAUSE, money } from "@/lib/format";
import type { Item, State } from "@/lib/types";

export function causeTag(it: Item) {
  if (it.duplicate_of) return <span className="tag">Duplicate invoice</span>;
  if (it.alerts.some(a => a.startsWith("al-bank_detail_change"))) return <span className="tag wine">Bank-detail hold</span>;
  if (it.state === "not_due") return <span className="tag neutral">Not due yet</span>;
  if (!it.cause) return <span className="tag neutral">{it.state.replaceAll("_", " ")}</span>;
  return <span className={`tag ${it.cause === "unexplained" ? "" : "ok"}`}>{CAUSE[it.cause]}</span>;
}

export function onManager(it: Item): boolean { return it.cause === "unexplained" && it.owed > 0; }

export function ItemsTable({ state, items, limit }: { state: State; items?: Item[]; limit?: number }) {
  const rows = (items ?? state.items.filter(i => i.attention)).slice()
    .sort((a,b) => Number(onManager(b)) - Number(onManager(a)) || b.owed - a.owed || a.id.localeCompare(b.id)).slice(0,limit);
  if (!rows.length) return <div className="empty">Nothing open.</div>;
  return <div className="table-scroll"><table className="table compact-items">
    <thead><tr><th>Vendor / invoice</th><th className="r">Still owed</th><th>Status</th><th><span className="sr-only">Details</span></th></tr></thead>
    <tbody>{rows.map(it => <tr key={it.id}>
      <td className="vendor"><Link className="item-name" href={`/items/${it.id}`}>{it.vendor}</Link><div className="micro muted" style={{ marginTop: 6 }}>{it.id}</div></td>
      <td className="r num" style={{ fontWeight: 500 }}>{money(it.owed)}</td>
      <td>{causeTag(it)}</td>
      <td><Link href={`/items/${it.id}`} className="pill" aria-label={`View ${it.vendor} invoice ${it.id}`}>View <span aria-hidden="true">↗</span></Link></td>
    </tr>)}</tbody>
  </table></div>;
}
