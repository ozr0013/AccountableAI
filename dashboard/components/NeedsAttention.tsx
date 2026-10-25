"use client";

// What still needs someone, for an owner: one total (the same as Invoices' "still owed"), split by
// who has to act, each invoice in plain words. Shown at the top of the Review queue (and on /items).

import Link from "next/link";
import { balances } from "@/lib/balances";
import { money } from "@/lib/format";
import type { Item, State } from "@/lib/types";

type Group = "keyway" | "you" | "expected";
const GROUPS: { id: Group; title: string; note: string }[] = [
  { id: "keyway", title: "Keyway must show proof", note: "Funded and past due, with no bank record or vendor confirmation." },
  { id: "you", title: "Needs your decision", note: "Only you can approve these. Nothing is paid until you do." },
  { id: "expected", title: "Expected, no action needed", note: "Allowed by the agreement or waiting on the vendor." },
];

/** Who has to act on an open invoice, and why, in an owner's words. */
function triage(i: Item, items: Item[]): { group: Group; why: string } {
  if (i.duplicate_of) return { group: "you", why: `Billed twice (copy of ${i.duplicate_of}): don't pay it again` };
  if (i.alerts.some((a) => a.startsWith("al-bank_detail_change"))) return { group: "you", why: "Vendor asked to change bank details: verify before anyone pays" };
  if (i.cause === "owner_side") return { group: "you", why: "Over your approval limit: needs your approval and funding" };
  if (i.cause === "unexplained") return { group: "keyway", why: `No proof it was paid${i.days_late ? ` · ${i.days_late} days past due` : ""}` };
  if (i.cause === "paperwork_pending") return { group: "expected", why: "Waiting for the vendor's W-9; Keyway can't pay until it arrives" };
  if (i.cause === "authorized_hold") return { group: "expected", why: "Retained until the work passes inspection, as the agreement allows" };
  if (items.some((x) => x.duplicate_of === i.id)) return { group: "expected", why: "Paid; the original of the bill that was sent twice" };
  if (i.state === "not_due") return { group: "expected", why: "Not due yet" };
  return { group: "expected", why: i.cause_text ?? i.state.replace(/_/g, " ") };
}

export function NeedsAttention({ state }: { state: State }) {
  const open = state.items.filter((i) => i.attention);
  const rows = open.map((i) => ({ i, ...triage(i, state.items) }));
  const total = open.reduce((s, i) => s + i.owed, 0);
  const bal = balances(state);
  const sum = (g: Group) => rows.filter((r) => r.group === g).reduce((s, r) => s + r.i.owed, 0);
  const pattern = state.alerts.find((a) => a.open && a.kind === "recurring_pattern");

  if (!open.length) return (
    <section className="na" id="needs-attention">
      <span className="label muted">/ Needs attention</span>
      <h2 className="na-total">Nothing is waiting on anyone.</h2>
    </section>
  );

  return (
    <section className="na" id="needs-attention" aria-label="Needs attention">
      <div className="na-head">
        <div>
          <span className="label muted">/ Needs attention</span>
          <h2 className="na-total">{open.length} invoices · {money(total)} open</h2>
        </div>
        <Link href="/invoices" className="pill">Every invoice ↗</Link>
      </div>
      <p className="na-recon">
        <span><b>{money(bal.keyway)}</b> Keyway still owes vendors</span>
        {bal.unfunded > 0 && <span>+ <b>{money(bal.unfunded)}</b> not yet funded by you</span>}
        {bal.retained > 0 && <span>+ <b>{money(bal.retained)}</b> retained under the agreement</span>}
        {bal.duplicate > 0 && <span>+ <b>{money(bal.duplicate)}</b> billed twice</span>}
        <span>= <b>{money(bal.open)}</b> open</span>
      </p>
      <div className="na-bar" role="img" aria-label={GROUPS.map((g) => `${g.title} ${money(sum(g.id))}`).join(", ")}>
        {GROUPS.map((g) => sum(g.id) > 0 && <span key={g.id} className={`na-seg g-${g.id}`} style={{ flexGrow: sum(g.id) }} />)}
      </div>
      <div className="na-legend">
        {GROUPS.map((g) => <span key={g.id} className={`g-${g.id}`}><i />{g.title.split(" ")[0] === "Keyway" ? "Keyway" : g.id === "you" ? "You" : "Expected"} <b>{money(sum(g.id))}</b></span>)}
      </div>

      {GROUPS.map((g) => {
        const mine = rows.filter((r) => r.group === g.id).sort((a, b) => b.i.owed - a.i.owed);
        if (!mine.length) return null;
        return (
          <div key={g.id} className={`na-group g-${g.id}`}>
            <div className="na-group-head">
              <h3>{g.title}{g.id === "keyway" && <span className="na-act">Your response needed</span>}</h3>
              <span className="mono">{money(sum(g.id))} · {mine.length}</span>
            </div>
            <p className="na-note">{g.note}{g.id === "keyway" && pattern ? ` ${pattern.headline.replace(/^keyway/i, "Keyway")}.` : ""}</p>
            {mine.map(({ i, why }) => (
              <Link key={i.id} href={`/items/${i.id}`} className="na-row">
                <span className="na-who"><b>{i.vendor}</b><small>{i.property ?? i.id}</small></span>
                <span className="na-why">{why}</span>
                <b className="na-amt">{i.owed > 0 ? money(i.owed) : "—"}</b>
                <span aria-hidden="true">↗</span>
              </Link>
            ))}
          </div>
        );
      })}
    </section>
  );
}
