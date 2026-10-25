"use client";

// Where Oakline's money to Keyway is (Overview, Keyway): total invoiced (what Oakline paid Keyway) =
// paid to vendors + outstanding; past due is the late part of outstanding. Each box opens the bills behind it.

import Link from "next/link";
import { totals } from "@/lib/balances";
import { money } from "@/lib/format";
import type { Item } from "@/lib/types";

export function MoneyStrip({ items }: { items: Item[] }) {
  const t = totals(items);
  return (
    <div className="ov-boxes">
      <Link href="/keyway"><span>Total invoiced</span><strong>{money(t.funded)}</strong></Link>
      <Link href="/invoices?show=paid" className="paid"><span>Paid</span><strong>{money(t.paid)}</strong></Link>
      <Link href="/invoices?show=left"><span>Outstanding</span><strong>{money(t.left)}</strong></Link>
      <Link href="/invoices?show=late" className="late"><span>Past due</span><strong>{money(t.late)}</strong></Link>
    </div>
  );
}
