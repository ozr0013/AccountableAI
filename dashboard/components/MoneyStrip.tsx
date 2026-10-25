"use client";

// Where Oakline's money to Keyway is (Overview, Keyway): paid by Oakline = paid to vendors + left to
// pay; past due is the part of what's left that's late. Each box opens the bills behind it.

import Link from "next/link";
import { totals } from "@/lib/balances";
import { money } from "@/lib/format";
import type { Item } from "@/lib/types";

export function MoneyStrip({ items }: { items: Item[] }) {
  const t = totals(items);
  return (
    <div className="ov-boxes">
      <Link href="/keyway"><span>Paid by Oakline to Keyway</span><strong>{money(t.funded)}</strong></Link>
      <Link href="/invoices?show=paid"><span>Paid to vendors by Keyway</span><strong>{money(t.paid)}</strong></Link>
      <Link href="/invoices?show=left"><span>Left to pay</span><strong>{money(t.left)}</strong></Link>
      <Link href="/invoices?show=late" className="late"><span>Past due</span><strong>{money(t.late)}</strong></Link>
    </div>
  );
}
