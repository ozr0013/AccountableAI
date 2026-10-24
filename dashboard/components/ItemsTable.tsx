"use client";

import { useRouter } from "next/navigation";
import { CAUSE, day, money } from "@/lib/format";
import type { Item, State } from "@/lib/types";

/** Why an item is open, in one tag. Only "unexplained" lands on the manager. */
export function causeTag(it: Item) {
  if (it.duplicate_of) return <span className="tag">Duplicate of {it.duplicate_of}</span>;
  if (it.alerts.some((a) => a.startsWith("al-bank_detail_change"))) return <span className="tag wine">Bank-detail hold</span>;
  if (it.state === "not_due") return <span className="tag neutral">Not due yet</span>;
  if (!it.cause) return <span className="tag neutral">{it.state}</span>;
  if (it.cause === "unexplained") return <span className="tag">Unexplained</span>;
  return <span className="tag ok">{CAUSE[it.cause]}</span>;
}

export function onManager(it: Item): boolean {
  return it.cause === "unexplained" && it.owed > 0;
}

export function ItemsTable({ state, items, limit }: { state: State; items?: Item[]; limit?: number }) {
  const router = useRouter();
  const rows = (items ?? state.items.filter((i) => i.attention))
    .slice()
    .sort((a, b) => Number(onManager(b)) - Number(onManager(a)) || b.owed - a.owed || a.id.localeCompare(b.id))
    .slice(0, limit);
  if (!rows.length) return <div className="empty">Nothing open.</div>;
  return (
    <table className="table">
      <thead>
        <tr>
          <th>Vendor</th>
          <th>Invoice</th>
          <th className="r">Still owed</th>
          <th>Cause</th>
          <th>On Keyway?</th>
          <th className="r">Due</th>
          <th className="r">Past due</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((it) => (
          <tr key={it.id} className="clickable" onClick={() => router.push(`/items/${it.id}`)}>
            <td className="vendor">
              {it.vendor}
              <div className="micro muted" style={{ marginTop: 2 }}>{it.trade}</div>
            </td>
            <td className="mono muted" style={{ fontSize: 12 }}>{it.id}</td>
            <td className="r num" style={{ fontWeight: 500 }}>{money(it.owed)}</td>
            <td>{causeTag(it)}</td>
            <td>
              {onManager(it)
                ? <span className="micro warn">Yes · alert</span>
                : <span className="micro ok">No</span>}
            </td>
            <td className="r num muted">{day(it.due)}</td>
            <td className="r num">{it.owed > 0 && it.days_late > 0 ? <span className={onManager(it) ? "warn" : "muted"}>{it.days_late}d</span> : <span className="faint">–</span>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
