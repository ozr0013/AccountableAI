const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function money(n: number | null | undefined, cents = true): string {
  if (n == null) return "–";
  return n.toLocaleString("en-US", {
    style: "currency", currency: "USD",
    minimumFractionDigits: cents ? 2 : 0, maximumFractionDigits: cents ? 2 : 0,
  });
}

export function compact(n: number): string {
  if (Math.abs(n) >= 1000) return `$${(n / 1000).toFixed(n >= 100000 ? 0 : 1)}k`;
  return money(n, false);
}

/** "2026-04-15" -> "Apr 15" (dates are calendar dates, never shifted by timezone). */
export function day(iso: string | null | undefined, withYear = false): string {
  if (!iso) return "–";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return `${MONTHS[m - 1]} ${d}${withYear ? `, ${y}` : ""}`;
}

export function month(key: string): string {
  const [y, m] = key.split("-").map(Number);
  return `${MONTHS[m - 1]} ${y}`;
}

export const CAUSE: Record<string, string> = {
  authorized_hold: "Authorized hold",
  paperwork_pending: "Paperwork pending",
  vendor_side: "Vendor side",
  owner_side: "Owner side",
  unexplained: "Unexplained",
};

export const ALERT: Record<string, string> = {
  unexplained_unpaid: "Unexplained unpaid",
  recurring_pattern: "Recurring lateness",
  duplicate_billing: "Duplicate billing",
  bank_detail_change: "Bank-detail change",
  owner_pending_approval: "Oakline approval pending",
  paperwork_request: "Paperwork request",
};

export const SOURCE: Record<string, string> = {
  funding: "Oakline funding",
  vendor_invoice: "Vendor invoice",
  manager_statement: "Keyway statement",
  manager_bank: "Keyway bank",
  vendor_confirmation: "Vendor confirmation",
  vendor_notice: "Vendor notice",
  manager_reply: "Keyway reply",
  agreement: "Agreement",
  judge: "Judge fact",
  controller: "Controller",
};

/** Does this cause put the alert on the manager? (Only unexplained does.) */
export const ON_MANAGER = new Set(["unexplained"]);

/** Replace raw vendor ids (v-summit) with names in agent-written text. */
export function named(text: string | null | undefined, names: Record<string, string>): string {
  if (!text) return "";
  return text.replace(/\bv-[a-z0-9-]+\b/g, (id) => names[id] ?? id).replace(/\bkeyway\b/g, "Keyway");
}
