"use client";

import Link from "next/link";
import { useFeed } from "@/components/Feed";
import { Loading, PageTop } from "@/components/ui";
import { totals } from "@/lib/balances";
import { money } from "@/lib/format";

/** Oakline's homes: what was spent on each, and what's still open there. */
export default function Properties() {
  const { state } = useFeed();
  if (!state) return <><PageTop title="Properties" /><Loading /></>;
  const homes = state.properties ?? [];
  if (!homes.length) return <><PageTop title="Properties" /><section className="sim-empty"><p>No properties in this world.</p></section></>;
  const cities = [...new Set(homes.map((h) => `${h.city}, ${h.state}`))];
  // each home's figures from its own bills, by the rules every page uses
  const byHome = new Map(homes.map((h) => [h.id, totals(state.items.filter((i) => i.property_id === h.id))]));
  const all = totals(state.items.filter((i) => i.property_id && byHome.has(i.property_id)));
  return <>
    <PageTop title="Properties" sub={`${homes.length} homes Keyway Management looks after for Oakline in ${cities.join(" · ")}`} />
    <div className="ov-boxes">
      <Link href="/invoices"><span>Homes · {homes.filter((h) => h.invoices).length} with repairs</span><strong>{homes.length}</strong></Link>
      <Link href="/invoices?show=paid" className="paid"><span>Paid</span><strong>{money(all.paid)}</strong></Link>
      <Link href="/invoices?show=left"><span>Outstanding</span><strong>{money(all.left)}</strong></Link>
      <Link href="/invoices?show=unfunded"><span>Not yet funded by Oakline</span><strong>{money(all.notFunded)}</strong></Link>
    </div>
    <div className="table-scroll lr-box" style={{ marginTop: 16 }}>
      <table className="table lr-table">
        <thead><tr><th>Home</th><th>City</th><th className="r">Beds</th><th className="r">Invoices</th><th className="r">Paid</th><th className="r">Outstanding</th><th className="r">Not funded</th><th>Last invoice</th><th>Open</th></tr></thead>
        <tbody>{homes.map((h) => (
          <tr key={h.id} className={h.attention ? "u-alert" : ""}>
            <td><Link href={`/invoices?property=${h.id}`}>{h.address}</Link><div className="mono" style={{ fontSize: 11, color: "var(--n9)" }}>{h.id}</div></td>
            <td>{h.city}, {h.state} {h.zip}</td><td className="r num">{h.beds}</td><td className="r num">{h.invoices}</td>
            <td className="r num">{money(byHome.get(h.id)!.paid)}</td>
            <td className={`r num${byHome.get(h.id)!.late ? " late-ink" : ""}`}>{byHome.get(h.id)!.left ? money(byHome.get(h.id)!.left) : "—"}</td>
            <td className="r num">{byHome.get(h.id)!.notFunded ? money(byHome.get(h.id)!.notFunded) : "—"}</td>
            <td className="mono">{h.last_invoice ?? "—"}</td>
            <td>{h.attention ? <span className="tag">{h.attention} need attention</span> : <span className="tag ok">Clear</span>}</td>
          </tr>))}
        </tbody>
      </table>
    </div>
  </>;
}
