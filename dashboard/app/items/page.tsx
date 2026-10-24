"use client";

import { useState } from "react";
import { useFeed } from "@/components/Feed";
import { ItemsTable, onManager } from "@/components/ItemsTable";
import { Loading, PageTop, SectionTitle } from "@/components/ui";
import { money } from "@/lib/format";

export default function Items() {
  const { state } = useFeed();
  const [all, setAll] = useState(false);
  if (!state) return <><PageTop title="Open items" /><Loading /></>;
  const open = state.items.filter((i) => i.attention);
  const late = state.items.filter((i) => i.paid_late);
  const mine = open.filter(onManager);
  return (
    <>
      <PageTop title="Open items" sub="Every vendor invoice Oakline funded that isn't confirmed paid, with one cause each. Only unexplained items land on Keyway." />
      <div className="grid g3">
        <div className="card"><p className="card-label">On Keyway (unexplained)</p><div className="display h2 num warn">{money(mine.reduce((s, i) => s + i.owed, 0), false)}</div><div className="micro muted" style={{ marginTop: 8 }}>{mine.length} items · alert + chase</div></div>
        <div className="card"><p className="card-label">Explained, not Keyway&apos;s fault</p><div className="display h2 num">{money(open.filter((i) => !onManager(i)).reduce((s, i) => s + i.owed, 0), false)}</div><div className="micro muted" style={{ marginTop: 8 }}>hold · paperwork · Oakline approval · bank-detail check · duplicate</div></div>
        <div className="card"><p className="card-label">Paid, but late</p><div className="display h2 num">{late.length}</div><div className="micro muted" style={{ marginTop: 8 }}>folded into the recurring-lateness alert</div></div>
      </div>
      <section className="section">
        <SectionTitle right={<button className="pill" onClick={() => setAll(!all)}>{all ? "Open only" : `Include ${late.length} paid late`}</button>}>
          {all ? "Open and paid-late items" : "Open items"}
        </SectionTitle>
        <div className="card" style={{ padding: "16px 8px 4px" }}>
          <ItemsTable state={state} items={all ? [...open, ...late] : open} />
        </div>
      </section>
    </>
  );
}
