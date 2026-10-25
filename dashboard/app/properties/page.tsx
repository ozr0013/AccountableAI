"use client";

import Link from "next/link";
import { useFeed } from "@/components/Feed";
import { Loading, PageTop } from "@/components/ui";
import { balances } from "@/lib/balances";
import { money } from "@/lib/format";

/** Oakline's homes: what was spent on each, and what's still open there. */
export default function Properties() {
  const { state } = useFeed();
  if (!state) return <><PageTop title="Properties" /><Loading /></>;
  const homes = state.properties ?? [];
  if (!homes.length) return <><PageTop title="Properties" /><section className="sim-empty"><p>No properties in this world.</p></section></>;
  const cities = [...new Set(homes.map((h) => `${h.city}, ${h.state}`))];
  const sum = (k: "paid" | "owed") => homes.reduce((s, h) => s + h[k], 0);
  return <>
    <PageTop title="Properties" sub={`${homes.length} homes Keyway manages for Oakline in ${cities.join(" · ")}`} />
    <div className="sim-stats">
      <div><span>Homes</span><strong>{homes.length}</strong><small>{cities.length} cities</small></div>
      <div><span>With repairs this year</span><strong>{homes.filter((h) => h.invoices).length}</strong><small>{homes.reduce((s, h) => s + h.invoices, 0)} invoices</small></div>
      <div><span>Paid to vendors</span><strong>{money(sum("paid"))}</strong><small>across all homes</small></div>
      <div><span>Open vendor balance</span><strong>{money(sum("owed"))}</strong><small>Keyway still owes {money(balances(state).keyway)} of it · {homes.filter((h) => h.attention).length} homes need attention</small></div>
    </div>
    <div className="table-scroll lr-box" style={{ marginTop: 16 }}>
      <table className="table lr-table">
        <thead><tr><th>Home</th><th>City</th><th className="r">Beds</th><th className="r">Invoices</th><th className="r">Paid</th><th className="r">Owed</th><th>Last invoice</th><th>Open</th></tr></thead>
        <tbody>{homes.map((h) => (
          <tr key={h.id} className={h.attention ? "u-alert" : ""}>
            <td><Link href={`/invoices?property=${h.id}`}>{h.address}</Link><div className="mono" style={{ fontSize: 11, color: "var(--n9)" }}>{h.id}</div></td>
            <td>{h.city}, {h.state} {h.zip}</td><td className="r num">{h.beds}</td><td className="r num">{h.invoices}</td>
            <td className="r num">{money(h.paid, false)}</td><td className="r num">{h.owed > 0 ? money(h.owed) : "—"}</td>
            <td className="mono">{h.last_invoice ?? "—"}</td>
            <td>{h.attention ? <span className="tag">{h.attention} need attention</span> : <span className="tag ok">Clear</span>}</td>
          </tr>))}
        </tbody>
      </table>
    </div>
  </>;
}
