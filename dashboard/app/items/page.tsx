"use client";

import Link from "next/link";
import { useState } from "react";
import { Disclosure } from "@/components/Disclosure";
import { useFeed } from "@/components/Feed";
import { ItemsTable, onManager } from "@/components/ItemsTable";
import { Badge, Loading, PageTop } from "@/components/ui";
import { money } from "@/lib/format";

export default function Items() {
  const { state } = useFeed();
  const [all, setAll] = useState(false);
  if (!state) return <><PageTop title="Open items" /><Loading /></>;
  const open = state.items.filter((i) => i.attention);
  const late = state.items.filter((i) => i.paid_late);
  const mine = open.filter(onManager);
  const explained = open.filter((i) => !onManager(i));
  const priority = mine.slice().sort((a, b) => b.owed - a.owed);
  return (
    <>
      <PageTop title="Needs attention" sub="The few items that need a closer look. Everything else is still available when you need it." />
      <section className="focus-panel attention-panel">
        <div className="focus-heading"><div><span className="label muted">/ Keyway needs to explain</span><h2>{money(mine.reduce((s, i) => s + i.owed, 0), false)} still needs proof.</h2></div><Link href="/cases" className="pill">Review the case ↗</Link></div>
        <div className="focus-list">
          {priority.map((item) => (
            <Link key={item.id} href={`/items/${item.id}`} className="focus-row">
              <Badge kind="flag" />
              <span><strong>{item.vendor}</strong><small>Payment proof still missing</small></span>
              <b>{money(item.owed, false)}</b><span aria-hidden="true">↗</span>
            </Link>
          ))}
        </div>
      </section>

      <Disclosure title="Other open items" hint="Already explained, awaiting an action, or held for safety" meta={`${explained.length} items`}>
        <div className="quiet-metrics compact-metrics">
          <div><span>Explained / not Keyway</span><strong>{money(explained.reduce((s, i) => s + i.owed, 0), false)}</strong></div>
          <div><span>Paid late</span><strong>{late.length}</strong></div>
          <div><span>Open total</span><strong>{money(open.reduce((s, i) => s + i.owed, 0), false)}</strong></div>
        </div>
        <div className="table-scroll"><ItemsTable state={state} items={explained} /></div>
      </Disclosure>

      <Disclosure title="All records" hint="The complete invoice table, including paid-late items" meta={`${open.length + late.length} records`}>
        <div className="between" style={{ marginBottom: 16 }}><p className="body muted" style={{ margin: 0 }}>Use this only when you need the full operating view.</p><button className="pill" onClick={() => setAll(!all)}>{all ? "Open only" : `Include ${late.length} paid late`}</button></div>
        <div className="table-scroll"><ItemsTable state={state} items={all ? [...open, ...late] : open} /></div>
      </Disclosure>
    </>
  );
}
