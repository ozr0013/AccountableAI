// The shapes dashboard/feed/session.py writes. Kept loose where the UI only displays text.

export type Citation = { source_id: string; kind: string; locator?: string | null; quote?: string | null };

export type TrailEntry = {
  on: string | null; kind: string; claim: string; source_id: string;
  locator?: string | null; quote?: string | null;
};

export type Item = {
  id: string; vendor_id: string; vendor: string; trade?: string | null;
  amount: number; funded: number; paid: number; owed: number;
  received: string | null; funded_on: string | null; paid_on: string | null; due: string | null;
  status: string; duplicate_of: string | null; alerts: string[];
  state: string; cause: string | null; days_late: number; note?: string;
  cause_text?: string; cause_confidence?: number; cause_citations?: Citation[];
  trail?: { conflict: boolean; winning_claim: string; reasoning: string; winning: string[]; entries: TrailEntry[] };
  attention: boolean; paid_late: boolean;
};

export type Alert = {
  id: string; kind: string; invoice_ids: string[]; amount: number; headline: string;
  recommended_action: string | null;
  claims: { text: string; citations: Citation[] }[];
  verdict: { status: string; reasons: string[] };
  open: boolean; state: string; withdrawn_reason: string | null;
};

export type Case = {
  approval_id: string; id: string; alerts: string[]; kind: "funding" | "bank_change";
  proposed_hold: number; summary: string; evidence: Citation[]; proposed_by: string;
  recommendation: string | null; funding_amount: number | null; requested_at: string;
  status: "pending" | "approved" | "declined" | "superseded";
  decided_by: string | null; decided_at: string | null;
};

export type Chase = {
  id: string; invoice_ids: string[]; level: string; template_id: string; body: string;
  sent_at: string; sent: boolean; policy: string | null;
};

export type Doc = { id: string; kind: string; dated: string | null; vendor_id: string | null; text: string; data: Record<string, unknown> };

export type Diff = {
  rechecked: string[];
  changed: { id: string; vendor: string; delta: Record<string, [unknown, unknown]> }[];
  rechecked_unchanged: string[]; untouched: number;
  withdrawn: Alert[]; raised: Alert[]; kept: string[];
  unexplained: [number, number]; unexplained_items: [string[], string[]];
};

export type State = {
  meta: {
    as_of: string; parent: string; manager_id: string; manager: string; funding_pending: number;
    controller: string; vendors: number; documents: number; events: number; agents: string;
    names: Record<string, string>; generated_at: string;
  };
  headline: {
    funded: number; paid: number; held: number; owed: number; unfunded: number;
    unexplained: number; unexplained_items: string[]; vendors_owed: number;
    times_chased: number; reminders_sent: number; open_alerts: number; proposed_hold: number;
  };
  recon: Record<string, number>;
  aging: Record<string, number>;
  late_months: Record<string, boolean>;
  terms: Record<string, number | string>;
  items: Item[];
  alerts: Alert[];
  cases: Case[];
  chases: Chase[];
  replies: { id: string; on: string | null; text: string; invoice_id: string | null; claims_paid: boolean; verdict?: string | null; judge?: boolean }[];
  agents: { agent: string; kind: string; done: string; flagged: string | null }[];
  audit: { seq: number; id: string; at: string; type: string; actor: string; what: string; invoice_id: string | null }[];
  guardrails: { label: string; action: string; actor: string; allowed: boolean; reason: string }[];
  documents: Record<string, Doc>;
  history: { summary: string; affected: string[]; diff: Diff }[];
};

export type ConsoleResult =
  | { ok: true; summary: string; affected: string[]; trace: string[]; diff: Diff; state: State; events: unknown[] }
  | { ok: false; error: string; suggestions?: string[] };

export type Briefing = {
  manager_id: string; question: string; answer: string;
  claims: { text: string; citations: Citation[] }[];
  prior_rulings: string[]; meta: Record<string, unknown>;
};

export type Preset = { id: string; label: string; description: string; params: Record<string, string>; defaults: Record<string, string> };
