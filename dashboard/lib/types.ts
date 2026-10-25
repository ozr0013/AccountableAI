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
  property_id?: string | null; property?: string | null;
};

export type Property = {
  id: string; address: string; city: string; state: string; zip: string; beds: number;
  invoices: number; amount: number; funded: number; paid: number; owed: number; attention: number; last_invoice: string | null;
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
    paperwork?: { records: number; documents: number; flagged: number; ignored: number } | null;
    live_available?: boolean; start?: string; actions?: number;
    meter?: { limit: number; spent: number; calls: number; recent: { who: string; model: string; cost: number }[] };
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
  properties?: Property[];
  alerts: Alert[];
  cases: Case[];
  chases: Chase[];
  replies: { id: string; on: string | null; text: string; invoice_id: string | null; claims_paid: boolean; verdict?: string | null; judge?: boolean }[];
  agents: { agent: string; kind: string; done: string; flagged: string | null }[];
  audit: { seq: number; id: string; at: string; type: string; actor: string; what: string; invoice_id: string | null }[];
  guardrails: { label: string; action: string; actor: string; allowed: boolean; reason: string }[];
  documents: Record<string, Doc>;
  history: { kind?: string; summary: string; affected: string[]; diff: Diff; responses?: Response[]; cost?: number }[];
  decisions?: ({ type: "funding_instruction"; release: number; hold: number; approved: boolean; by: string; at: string; approval_id: string }
    | { type: "bank_change_decided"; vendor_id: string; allowed: boolean; reason: string; identity_verified: boolean; by: string; at: string; approval_id: string })[];
};

export type Response = { agent: string; what: string; type: string; invoice_id: string | null };

/** The figures every tab shows, before and after one action (session.py `books`). */
export type Books = { funded: number; paid: number; left: number; late: number; not_funded: number; unexplained: number; attention: number };

/** One beat of an action's run, in order (dashboard/feed/session.py `_act` timeline). */
export type Signal =
  | { type: "reader"; file: string; kind: string[]; flags: string[]; invoices: string[] }
  | { type: "step"; step: string }
  | { type: "agent_started"; agent: string; invoice_id: string | null; question: string | null }
  | { type: "decision"; agent: string; invoice_id: string | null; question: string;
      steps: { kind: string; text: string; cites: { source: string; locator: string | null; quote: string | null }[] }[];
      answer: Record<string, unknown>; mode: string; ms: number | null }
  | { type: "policy"; action: string; actor: string; ok: boolean; why: string };

export type ActionResult =
  | { ok: true; kind: string; summary: string; affected: string[]; trace: string[]; diff: Diff; responses: Response[]; cost: number; live: boolean; state: State; events: unknown[]; parsed?: Parsed;
      // what ran and what was written (dashboard/feed/session.py _act)
      books?: { before: Books; after: Books };
      timeline?: Signal[]; ran_ms?: number; emails?: unknown[];
      steps?: string[]; agents?: Record<string, number>; writes?: { total: number; notes: { type: string; text: string; invoice_id: string | null }[] } }
  | { ok: false; error: string; suggestions?: string[]; state?: State };

export type ConsoleResult = ActionResult;

/** What the parser read from a file added to the inbox (dashboard/feed/session.py add_file). */
export type Parsed = { file: string; kind: string | string[]; flags: string[] };

export type Briefing = {
  manager_id: string; question: string; answer: string;
  claims: { text: string; citations: Citation[] }[];
  prior_rulings: string[]; meta: Record<string, unknown>;
};

export type Preset = { id: string; label: string; description: string; params: Record<string, string>; defaults: Record<string, string> };

// Live agent reasoning (workflow/trace.py DecisionRecord), streamed by GET /api/stream.
export type ReasonStep = {
  kind: "evidence" | "fact" | "rule" | "thinking" | "model" | "guard" | "fallback" | "result";
  text: string; citations: Citation[];
};

export type Decision = {
  id: string; trigger: string | null; seq: number; at: string | null; day: string | null;
  agent: string; invoice_id: string | null; question: string; steps: ReasonStep[];
  answer: Record<string, unknown>; confidence: number | null; mode: "rules" | "llm" | "fallback";
  citations: Citation[]; model: string | null; tokens_in: number | null; tokens_out: number | null; ms: number;
};

export type LiveSignal =
  | { type: "step"; step: string; notes: string[]; trigger: string | null }
  | { type: "agent_started"; agent: string; invoice_id: string | null; question: string; trigger: string | null }
  | { type: "thinking"; agent: string; invoice_id: string | null; text: string; trigger: string | null };

export type StreamAction = { type: "action"; status: "started" | "finished"; kind: string; summary: string; as_of: string; unexplained?: number; cost?: number };

// GET /api/timeline (workflow/timeline.py)
export type TimelineDay = {
  date: string; label: string; quiet: boolean;
  facts: { id: string; type: string; what: string; source_id: string | null; invoice_id: string | null }[];
  decisions: Decision[];
  outcomes: { type: string; what: string | null; invoice_ids: (string | null)[]; alert_id?: string; amount?: number; hold?: number }[];
  human: { type: string; what: string }[];
  unexplained_start: number; unexplained_end: number;
  counts: { facts: number; decisions: number; claude: number; fallback: number; outcomes: number; human: number };
};

export type Timeline = { as_of: string; start: string; days: TimelineDay[] };
