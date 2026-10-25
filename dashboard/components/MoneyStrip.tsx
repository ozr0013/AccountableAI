"use client";

// Where Oakline's money to Keyway is, in figures that add up:
// funded = paid to vendors + held back (retained, billed twice) + still owed to vendors (± anything that doesn't fit).

import Link from "next/link";
import { balances } from "@/lib/balances";
import { money } from "@/lib/format";
import type { State } from "@/lib/types";

export function MoneyStrip({ state }: { state: State }) {
  const b = balances(state);
  const r = state.recon;
  const held = b.retained + b.duplicate;
  const why = [b.retained ? `${money(b.retained)} retained` : "", b.duplicate ? `${money(b.duplicate)} billed twice` : ""].filter(Boolean).join(" · ");
  // Whatever doesn't fit the four (overpaid, over-funded, a paid duplicate) is shown, never absorbed.
  const gap = Math.round((r.funded - r.paid - held - b.keyway) * 100) / 100;
  return (
    <div className={`quiet-metrics money-strip${gap ? " has-gap" : ""}`}>
      <Link href="/keyway"><span>Funded to Keyway</span><strong>{money(r.funded)}</strong><small>View reconciliation ↗</small></Link>
      <Link href="/keyway"><span>Paid to vendors</span><strong>{money(r.paid)}</strong><small>Bank or vendor proof ↗</small></Link>
      <Link href="/cases"><span>Held back</span><strong>{money(held)}</strong><small>{why || "Nothing held"} ↗</small></Link>
      <Link href="/cases"><span>Keyway still owes vendors</span><strong>{money(b.keyway)}</strong><small>{money(b.unexplained)} unexplained ↗</small></Link>
      {gap !== 0 && <Link href="/keyway"><span>{gap > 0 ? "Not accounted for" : "Paid beyond funding"}</span><strong>{money(Math.abs(gap))}</strong><small>Check the reconciliation ↗</small></Link>}
    </div>
  );
}
