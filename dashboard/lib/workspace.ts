// The UI revamp 2 workspace (Portfolio, Properties, Invoices, Judgment, Settings, Help) on the
// live feed. Its invoice model: one row per Keyway invoice with a property, a status of
// Paid | Outstanding | Past Due and the issue a person should review. Every figure here is
// computed from the live state; nothing is illustrative.

import type { Item, State } from "./types";

export type WsStatus = "Paid" | "Outstanding" | "Past Due";
export type WsInvoice = {
  id: string; cid: string; property: string; propertyId: string | null; vendor: string;
  description: string; trade: string; amount: number; paid: number; owed: number;
  status: WsStatus; due: string; received: string | null; paidOn: string | null;
  daysLate: number; issue: string; cause: string;
};
export type Review = { notes: string[]; reviewed: boolean };
export type Reviews = Record<string, Review>;

const ISSUE: Record<string, string> = {
  unexplained_unpaid: "No proof the vendor was paid",
  duplicate_billing: "Possible duplicate invoice",
  bank_detail_change: "Bank-detail change request",
  paperwork_request: "Vendor paperwork missing (W-9)",
  owner_pending_approval: "Amount exceeds approval limit, Oakline approval pending",
  statement_mismatch: "Statement amount differs from approved",
};
const TRADE: Record<string, string> = {
  landscaper: "Landscaping", cleaner: "Cleaning", plumber: "Plumbing", roofer: "Roofing",
  hvac: "HVAC & Mechanical", handyman: "Handyman", painter: "Painting", electrician: "Electrical Systems",
};
export const tradeLabel = (t?: string | null) => (t ? TRADE[t] ?? t[0].toUpperCase() + t.slice(1) : "General");

export function toInvoice(i: Item): WsInvoice {
  const kinds = i.alerts.map(a => a.split("-")[1]).filter(k => k in ISSUE);
  const owed = Math.max(0, i.owed);
  const status: WsStatus = owed <= 0.005 && i.status !== "unfunded" ? "Paid" : i.days_late > 0 ? "Past Due" : "Outstanding";
  const property = i.property?.split(",")[0] ?? "Portfolio-wide";
  return {
    id: i.id, cid: i.property_id ?? i.vendor_id, property, propertyId: i.property_id ?? null, vendor: i.vendor,
    description: `${tradeLabel(i.trade)} work, ${property}`, trade: tradeLabel(i.trade),
    amount: i.amount, paid: Math.min(i.paid, i.amount), owed: status === "Paid" ? 0 : owed || i.amount,
    status, due: i.due ?? i.received ?? "", received: i.received, paidOn: i.paid_on,
    daysLate: status === "Past Due" ? Math.max(0, i.days_late) : 0,
    issue: [...new Set(kinds.map(k => ISSUE[k]))].join("; "), cause: i.cause_text ?? "",
  };
}

export const invoicesOf = (s: State) => s.items.map(toInvoice);

/** Every property that has invoices, by address. */
export function propertiesOf(rows: WsInvoice[]): string[] {
  return [...new Set(rows.map(r => r.property))].sort((a, b) => a === "Portfolio-wide" ? 1 : b === "Portfolio-wide" ? -1 : a.localeCompare(b));
}

/** Totals: Payments is what vendors were paid; Outstanding and Past Due are what is still owed. */
export function sums(rows: WsInvoice[]) {
  const sum = (f: (r: WsInvoice) => number) => rows.reduce((s, r) => s + f(r), 0);
  return {
    total: sum(r => r.amount), paid: sum(r => r.paid),
    out: sum(r => (r.status === "Outstanding" ? r.owed : 0)), past: sum(r => (r.status === "Past Due" ? r.owed : 0)),
  };
}

export const needsReview = (rows: WsInvoice[], reviews: Reviews) => rows.filter(r => r.issue && !reviews[r.id]?.reviewed);

export function dateBounds(rows: WsInvoice[]) {
  const d = rows.map(r => r.due).filter(Boolean).sort();
  return { start: d[0] ?? "2026-01-01", end: d.at(-1) ?? "2026-12-31" };
}

const fmtDay = (iso: string, o: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" }) =>
  new Date(`${iso}T12:00:00Z`).toLocaleDateString("en-US", { ...o, timeZone: "UTC" });
export const dateLabel = (start: string, end: string) => `${fmtDay(start)} – ${fmtDay(end)}, ${end.slice(0, 4)}`;
export const longDate = (iso: string) => (iso ? fmtDay(iso, { month: "short", day: "2-digit", year: "numeric" }) : "—");

/** Unpaid balance by trade: the three largest and the rest. */
export function categories(rows: WsInvoice[]) {
  const by = new Map<string, number>();
  for (const r of rows) if (r.status !== "Paid") by.set(r.trade, (by.get(r.trade) ?? 0) + r.owed);
  const sorted = [...by.entries()].sort((a, b) => b[1] - a[1]);
  const top = sorted.slice(0, 3), rest = sorted.slice(3).reduce((s, [, v]) => s + v, 0);
  const colors = ["var(--o)", "#db7b00", "#e9b400", "#536278"];
  return [...top, ...(sorted.length > 3 ? [["Other trades", rest] as [string, number]] : [])].map(([name, value], i) => ({ name, value, color: colors[i] }));
}

/** Unpaid balance by how late it is. */
export function aging(rows: WsInvoice[]) {
  const b = [0, 0, 0, 0, 0];
  for (const r of rows) {
    if (r.status === "Paid") continue;
    const d = r.daysLate;
    b[d <= 0 ? 0 : d <= 30 ? 1 : d <= 60 ? 2 : d <= 90 ? 3 : 4] += r.owed;
  }
  return ["Current", "1–30 Days", "31–60 Days", "61–90 Days", "90+ Days"].map((label, i) => ({ label, value: b[i] }));
}

/** Average days from the vendor's invoice arriving to the vendor being paid. */
export function avgDaysToPay(rows: WsInvoice[]): number | null {
  const d = rows.filter(r => r.status === "Paid" && r.received && r.paidOn)
    .map(r => (Date.parse(r.paidOn!) - Date.parse(r.received!)) / 86_400_000);
  return d.length ? Math.round(d.reduce((s, x) => s + x, 0) / d.length) : null;
}

export type Period = "Weekly" | "Monthly" | "Quarterly" | "1Y" | "5Y";
export const PERIODS: Period[] = ["Weekly", "Monthly", "Quarterly", "1Y", "5Y"];

/** Invoiced amount by due date, bucketed for the chosen period. */
export function trend(rows: WsInvoice[], period: Period, end: string, start?: string) {
  const key = (iso: string) => {
    const d = new Date(`${iso}T12:00:00Z`);
    if (period === "Weekly") { const m = new Date(d); m.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7)); return m.toISOString().slice(0, 10); }
    if (period === "Monthly" || period === "1Y") return iso.slice(0, 7);
    if (period === "Quarterly") return `${iso.slice(0, 4)}-Q${Math.floor(d.getUTCMonth() / 3) + 1}`;
    return iso.slice(0, 4);
  };
  const buckets = new Map<string, number>();
  if (period === "1Y") for (let m = 1; m <= 12; m++) buckets.set(`${end.slice(0, 4)}-${String(m).padStart(2, "0")}`, 0);
  if (period === "5Y") for (let y = Number(end.slice(0, 4)) - 4; y <= Number(end.slice(0, 4)); y++) buckets.set(String(y), 0);
  if (start && (period === "Weekly" || period === "Monthly" || period === "Quarterly")) {   // every bucket in the range, empty ones too
    for (let t = Date.parse(`${start}T12:00:00Z`); t <= Date.parse(`${end}T12:00:00Z`); t += 86_400_000) {
      const k = key(new Date(t).toISOString().slice(0, 10));
      if (!buckets.has(k)) buckets.set(k, 0);
    }
  }
  for (const r of rows) if (r.due) { const k = key(r.due); if (period === "1Y" || period === "5Y" ? buckets.has(k) : true) buckets.set(k, (buckets.get(k) ?? 0) + r.amount); }
  const label = (k: string) => period === "Weekly" ? fmtDay(k) : period === "Monthly" || period === "1Y" ? new Date(`${k}-15T12:00:00Z`).toLocaleDateString("en-US", { month: "short", timeZone: "UTC" }) : k;
  return [...buckets.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => ({ key: k, label: label(k), value: v }));
}

// ---- browser-only state: review notes, profile, preferences (as UI revamp 2 keeps them) ----
export type Profile = { name: string; email: string; company: string; management: string; phone: string; photo: string };
export type Prefs = { overdue: boolean; review: boolean; payment: boolean; weekly: boolean; compact: boolean };
export const DEFAULT_PROFILE: Profile = {
  name: "Dana Ortiz", email: "dana.ortiz@oaklinehomes.example", company: "Oakline Homes",
  management: "Keyway Property Management", phone: "+1 (555) 234-5678", photo: "",
};
export const DEFAULT_PREFS: Prefs = { overdue: true, review: true, payment: true, weekly: false, compact: false };

export function readLocal<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try { const v = window.localStorage.getItem(key); return v ? { ...fallback, ...JSON.parse(v) } : fallback; } catch { return fallback; }
}
export const writeLocal = (key: string, v: unknown) => { try { window.localStorage.setItem(key, JSON.stringify(v)); } catch { /* storage full or blocked */ } };

export function csv(rows: WsInvoice[], reviews: Reviews): string {
  const fields = ["id", "cid", "property", "vendor", "description", "amount", "status", "due", "issue", "reviewed"] as const;
  const val = (r: WsInvoice, f: (typeof fields)[number]) => f === "reviewed" ? Boolean(reviews[r.id]?.reviewed) : r[f];
  return [fields.join(","), ...rows.map(r => fields.map(f => `"${String(val(r, f)).replace(/"/g, '""')}"`).join(","))].join("\n");
}

export function download(name: string, text: string, type: string) {
  const a = document.createElement("a"), url = URL.createObjectURL(new Blob([text], { type }));
  a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
