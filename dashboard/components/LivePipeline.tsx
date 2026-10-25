"use client";

// The three agents working live: reasoning streams in as Claude writes it, each tool call and its
// result appear as they happen, decisions and hand-offs land in the record. Data comes from the
// agent service (chaser_agent/pipeline_api.py): /pipeline/stream (Server-Sent Events) for the live
// events, /pipeline/demo/* for the time-lapse and the stored record. Nothing here is scripted: every
// label is derived from what the agents recorded.

import { useCallback, useEffect, useRef, useState } from "react";
import s from "./LivePipeline.module.css";

const API = process.env.NEXT_PUBLIC_AGENTS_URL || "http://127.0.0.1:8773";
const PRICE = { input: 4e-6, cached: 4e-7, output: 2e-5 };      // Claude Opus 5.5, USD per token

type Step = { tool: string; input?: unknown; result?: unknown; refused?: boolean; terminal?: boolean };
type Trace = { model?: string; thinking?: string[]; steps?: Step[]; usage?: Record<string, number> };
type Result = { level?: string; status?: string; recorded?: string; waiting?: boolean; verdict?: string };
type Outcome = { decision?: string | null; rationale?: string | null; verdict?: string | null; summary?: string | null;
  finding_codes?: string[] | null; decided_by?: string; error?: string | null; result?: Result | null };
type Task = { task_id: string; agent: "chaser" | "anomaly"; invoice_id: string; status: string; as_of: string;
  created_by: string; reason: string; outcome: Outcome; trace?: Trace };
type Action = { action_id: string; status: string; level?: string | null; action: string; invoice_id: string; as_of: string;
  message?: string | null };
type Invoice = { amount: number; funded_amount?: number | null; funded_on?: string | null; paid_amount?: number | null;
  paid_on?: string | null; invoice_received?: string | null };
type Claim = { statement: string; dated: string; says?: string; amount?: number; paid_on?: string | null };
type Flow = { vendor: string; invoice: Invoice; tasks: Task[]; chaser_actions: Action[]; deadline?: string | null;
  statements?: Claim[]; rules: { rule_type: string; check: string }[];
  case?: { state?: string; findings?: { code: string; message: string }[] } | null };
type Demo = { today: string; start: string; end: string; focus: string; done: boolean; usage?: { calls: number; input: number; cached: number; output: number } };
type LiveTool = { tool: string; state: "running" | "done" | "refused"; preview?: string };
type Query = { purpose: string; sql: string; columns?: string[]; rows?: unknown[][]; row_count?: number; error?: string };
type Live = { task_id: string; agent: string; invoice_id: string; day: string; reason: string;
  thinking: string; text: string; tools: LiveTool[]; queries: Query[]; decision: string | null; status: "working" | "done" | "failed" };
type Line = { id: number; at: string; agent: string; kind: string; detail: string };
type Row = { invoice_id: string; vendor: string; amount: number; funded_amount: number; deadline: string | null;
  deadline_source: string | null; paid_on: string | null; paid_amount: number; state: string; days: number | null;
  reminders: string[]; escalated: boolean; flags?: string[]; usage?: { calls: number; input: number; cached: number; output: number } | null;
  last: { agent: string; day: string; decision?: string | null; verdict?: string | null; by: string } | null };

const STATE: Record<string, string> = { overdue: "Overdue", not_due: "Not due", paid_on_time: "Paid on time",
  awaiting_funding: "Awaiting funding", misdirected: "Paid to wrong vendor",
  paid_late: "Paid late", partial: "Partly paid", not_funded: "Not funded", no_deadline: "No deadline" };
const FLAG: Record<string, string> = { statement_claims_unbacked: "statement claims a payment the bank does not show",
  vendor_reports_unpaid: "vendor reports it was not paid", delivery_failed: "an email could not be confirmed as sent", reply_without_proof: "Keyway says paid without proof",
  payment_to_other_vendor: "payment went to a different vendor", other_contradiction: "sources disagree",
  unreferenced_payment: "a bank payment without an invoice number seems to pay it", bundled_payment: "one transfer covers several invoices",
  misapplied_payment: "its payment was booked under another invoice number", duplicate_payment: "the same money was paid twice",
  vendor_pattern: "a repeated pattern with this vendor" };
const RANK: Record<string, number> = { overdue: 0, misdirected: 0, partial: 1, paid_late: 2, not_due: 3, not_funded: 4,
  awaiting_funding: 5, no_deadline: 6, paid_on_time: 7 };
const LEVELS = ["polite", "firm", "escalation"];

const FINDING: Record<string, string> = {
  late_payment: "paid after the agreement deadline",
  statement_predates_payment: "the statement claimed payment before it happened",
  wrong_vendor: "paid to a different payee", wrong_account: "paid to a different account",
  amount_mismatch: "amount differs from the approved invoice",
};
const VERDICT: Record<string, string> = { needs_review: "Needs controller review", clean: "Clean", awaiting_statement: "Awaiting statement" };

const usd = (n?: number | null) => (n == null ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD" }));
const day = (d?: string | null) => (d ? new Date(d + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "—");
const days = (a: string, b: string) => Math.round((Date.parse(b) - Date.parse(a)) / 864e5);
const num = (n: number) => n.toLocaleString("en-US");
const clock = () => new Date().toLocaleTimeString("en-US", { hour12: false });

// Model rationale for people: rule ids and raw field names become words.
const plain = (t?: string | null) => (t || "")
  .replace(/\s*\[schedule:[^\]]*\]/g, "")
  .replace(/\(?rule [0-9a-f]{8,}:(\w+)\)?/g, (_, r: string) => `the agreement's ${r.replace(/_/g, " ")} rule`)
  .replace(/\s*\(rule :\w+\)/g, "")
  .replace(/paid_amount 0(\.00)?, paid_on null/g, "no bank payment on record")
  .replace(/\s*\(line L\d+\)/g, "")
  .replace(/\s*\(?simulated-email-[\d-]+\)?/g, "")
  .replace(/\b\d{4}-\d{2}-\d{2}\b/g, m => day(m))
  .replace(/\s{2,}/g, " ").trim();

/** One label and one plain sentence per decision, derived from what the agent recorded. */
function describe(t: Task, flow: Flow): { label: string; tone: "" | "hold" | "quiet" | "ok"; line: string } {
  const o = t.outcome || {}, r = o.result || {};
  const late = flow.deadline ? days(flow.deadline, t.as_of) : 0;
  if (t.agent === "anomaly") {
    const found = (o.finding_codes || []).map(c => FINDING[c] || c.replace(/_/g, " "));
    return { label: VERDICT[o.verdict || ""] || o.verdict || "Verdict", tone: o.verdict === "clean" ? "ok" : "hold",
      line: found.length ? `Found: ${found.join("; ")}.` : "No problems found." };
  }
  if (o.decision === "verified_paid") return { label: "Verified paid", tone: "ok",
    line: "Full amount to the right vendor by the deadline. Checked by code, no model call." };
  if (o.decision === "wait") return o.decided_by === "schedule"
    ? { label: "Waited", tone: "quiet", line: "Next step not due yet. No model call." }
    : { label: "Waited", tone: "quiet", line: "Claude chose to wait." };
  if (o.decision === "send_reminder") {
    const action = flow.chaser_actions.find(a => a.action_id === r.recorded);
    const level = r.level ? r.level[0].toUpperCase() + r.level.slice(1) : "";
    if (r.status === "needs_approval") {
      const now = action?.status || "needs_approval";
      if (now === "needs_approval") return { label: `${level} reminder held for approval`, tone: "hold",
        line: `${late} days past the deadline. Waiting for the controller to approve.` };
      if (now === "cancelled") return { label: `${level} reminder withdrawn`, tone: "quiet",
        line: `${late} days late. Held for the controller; withdrawn once the payment arrived.` };
      if (now === "rejected") return { label: `${level} reminder rejected`, tone: "quiet",
        line: "The controller rejected the draft; nothing was sent." };
      return { label: `${level} reminder approved and sent`, tone: "",
        line: `${late} days past the deadline. Held for approval; the controller approved it.` };
    }
    const n = LEVELS.indexOf(r.level || "") + 1;
    return { label: `${level} reminder sent`, tone: n === 3 ? "hold" : "",
      line: `${late} days past the ${day(flow.deadline)} deadline. Email ${n} of 3, sent automatically.` };
  }
  if (o.decision === "escalate") {
    const hold = flow.chaser_actions.find(a => a.action === "hold_funding");
    const state = hold?.status === "approved" ? "the controller approved the funding hold"
      : hold?.status === "rejected" ? "the controller declined the funding hold" : "a funding hold awaits the controller";
    return { label: "Raised to Oakline", tone: "hold",
      line: `No proof of payment after three reminders. Owner and Keyway notified; ${state}.` };
  }
  if (o.decision === "handoff_to_anomaly") return { label: "Handed to Anomaly agent", tone: "",
    line: "Bank payment found. Anomaly agent checks amount, payee and timing." };
  return { label: o.decision || t.status, tone: "", line: plain(o.error) };
}

/** Streamed text revealed at a steady reading pace instead of in network-sized bursts. Speeds up
 *  gently when far behind, and finishes quickly once the agent is done. */
function usePaced(target: string, settle: boolean) {
  const [shown, setShown] = useState(target);
  const st = useRef({ n: target.length, target });
  useEffect(() => {
    const cur = st.current;
    if (cur.n > target.length || target.slice(0, cur.n) !== cur.target.slice(0, cur.n)) { cur.n = 0; setShown(""); }
    cur.target = target;
    let raf = 0, last = performance.now();
    const tick = (now: number) => {
      const dt = Math.min(64, now - last); last = now;
      const backlog = cur.target.length - cur.n;
      if (backlog <= 0) return;
      const rate = settle ? 1600 : Math.max(70, backlog / 1.4);          // characters per second
      cur.n = Math.min(cur.target.length, cur.n + Math.max(1, Math.round((rate * dt) / 1000)));
      setShown(cur.target.slice(0, cur.n));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, settle]);
  return shown;
}

/** A scroll box that follows new content only while the reader is at its bottom. Scrolling up to
 *  read stops the following until the reader scrolls back down; nothing re-arms it behind their back. */
function useFollow(dep: unknown, smooth = false) {
  const el = useRef<HTMLDivElement | null>(null);
  const stuck = useRef(true);
  const ref = useCallback((node: HTMLDivElement | null) => {
    el.current = node;
    if (node) node.onscroll = () => { stuck.current = node.scrollHeight - node.scrollTop - node.clientHeight < 56; };
  }, []);
  useEffect(() => {
    const n = el.current;
    if (n && stuck.current) n.scrollTo({ top: n.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  }, [dep, smooth]);
  return ref;
}

/** A number that eases to its new value rather than jumping. */
function Count({ value, format }: { value: number; format: (n: number) => string }) {
  const [v, setV] = useState(value);
  const from = useRef(value);
  useEffect(() => {
    const a = from.current, b = value;
    if (a === b) return;
    const start = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const p = Math.min(1, (now - start) / 700), x = a + (b - a) * (1 - Math.pow(1 - p, 3));
      from.current = x; setV(x);
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value]);
  return <>{format(v)}</>;
}

async function call<T>(path: string, method = "GET"): Promise<T> {
  const r = await fetch(`${API}${path}`, { method, cache: "no-store" });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((body as { detail?: string }).detail || `${path}: ${r.status}`);
  return body as T;
}

function CaseStrip({ flow, today }: { flow: Flow; today: string }) {
  const inv = flow.invoice;
  const claim = [...(flow.statements || [])].reverse().find(c => c.dated <= today);
  const paidOn = inv.paid_on && inv.paid_on <= today ? inv.paid_on : null;
  const claimsPaid = claim?.says === "paid";
  const conflict = claimsPaid && (!paidOn || (!!claim?.paid_on && claim.paid_on !== paidOn));
  const deadline = flow.deadline || null;
  const overdue = deadline && !paidOn && today > deadline ? days(deadline, today) : 0;
  const paidLate = deadline && paidOn && paidOn > deadline ? days(deadline, paidOn) : 0;
  const cells = [
    { k: "Owner funded", v: usd(inv.funded_amount), sub: `on ${day(inv.funded_on)}`, tone: "" },
    { k: "Agreement deadline", v: day(deadline),
      sub: overdue ? `${overdue} days overdue` : paidLate ? `paid ${paidLate} days late` : paidOn ? "paid on time" : "not yet due",
      tone: overdue || paidLate ? s.warn : "" },
    { k: "Keyway statement says", v: claim ? (claimsPaid ? `Paid ${day(claim.paid_on)}` : claim.says || "—") : "No statement",
      sub: claim ? `statement dated ${day(claim.dated)}` : "", tone: conflict ? s.warn : "" },
    { k: "Bank record shows", v: paidOn ? `Paid ${day(paidOn)}` : "No payment", sub: paidOn ? usd(inv.paid_amount) : `as of ${day(today)}`,
      tone: conflict ? s.warn : paidOn ? s.good : "" },
  ];
  return (
    <div className={s.caseStrip}>
      {cells.map(c => <div key={c.k} className={`${s.caseCell} ${c.tone}`}>
        <span className="micro">{c.k}</span><b>{c.v}</b><span className={s.caseSub}>{c.sub}</span>
      </div>)}
      {conflict && <div className={s.conflict}>Sources disagree: the statement claims a payment the bank {paidOn ? "shows on a different date" : "does not show"}.</div>}
    </div>
  );
}

function InvoiceTable({ rows, selected, working, onSelect }:
    { rows: Row[]; selected: string; working: string | null; onSelect: (id: string) => void }) {
  const [filter, setFilter] = useState("attention");
  const attention = (r: Row) => !["paid_on_time", "not_due", "awaiting_funding"].includes(r.state) || r.reminders.length > 0 || !!r.flags?.length;
  const shown = rows.filter(r => filter === "all" || attention(r))
    .sort((a, b) => (RANK[a.state] - RANK[b.state]) || ((b.days || 0) - (a.days || 0)));
  const count = (k: string) => rows.filter(r => r.state === k).length;
  return (
    <section className={s.panel}>
      <div className={s.panelHead}>
        <span className="micro">All invoices</span>
        <span className={s.mono}>{rows.length} checked · {count("overdue")} overdue · {count("paid_late")} paid late · {count("paid_on_time")} on time</span>
        <span className={s.grow} />
        {[["attention", "Needs attention"], ["all", "All"]].map(([k, l]) =>
          <button key={k} className={`${s.filter} ${filter === k ? s.filterOn : ""}`} onClick={() => setFilter(k)}>{l}</button>)}
      </div>
      <div className={s.tableWrap} data-lenis-prevent>
        <table className={s.table}>
          <thead><tr><th>Invoice</th><th>Vendor</th><th className={s.num}>Funded</th><th>Deadline</th><th>Status</th><th>Emails</th><th>Agents</th></tr></thead>
          <tbody>
            {shown.map(r => <tr key={r.invoice_id} onClick={() => onSelect(r.invoice_id)}
                className={`${r.invoice_id === selected ? s.sel : ""} ${r.invoice_id === working ? s.workingRow : ""}`}>
              <td className={s.mono}>{r.invoice_id}</td>
              <td>{r.vendor}</td>
              <td className={s.num}>{usd(r.funded_amount)}</td>
              <td>{day(r.deadline)}<span className={s.src2}>{r.deadline_source === "vendor agreement" ? "vendor" : r.deadline_source ? "mgmt" : ""}</span></td>
              <td><span className={`${s.pill} ${s["st_" + r.state] || ""}`}>{STATE[r.state] || r.state}{r.days ? ` · ${r.days}d` : ""}</span>
                {!!r.flags?.length && <span className={s.raised}>flagged</span>}</td>
              <td><span className={s.ladder}>{LEVELS.map(l => <i key={l} className={r.reminders.includes(l) ? s.sent : ""} title={l} />)}</span>
                {r.escalated && <span className={s.raised}>raised</span>}</td>
              <td className={s.lastCell}>{r.invoice_id === working ? <b className={s.workingTag}>working</b>
                : r.last ? `${r.last.agent} · ${r.last.verdict ? VERDICT[r.last.verdict] || r.last.verdict : r.last.decision === "wait" ? "waited" : (r.last.decision || "").replace(/_/g, " ")}` : "code check"}</td>
            </tr>)}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function LivePipeline() {
  const [demo, setDemo] = useState<Demo | null>(null);
  const [flow, setFlow] = useState<Flow | null>(null);
  const [live, setLive] = useState<Live | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [playing, setPlaying] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [seen, setSeen] = useState<Set<string>>(new Set());
  const [rows, setRows] = useState<Row[]>([]);
  const [selected, setSelected] = useState("");
  const selectedRef = useRef("");
  const playRef = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const d = await call<Demo>("/pipeline/demo");
      setDemo(d);
      const r = await call<Row[]>("/pipeline/demo/invoices");
      setRows(r);
      if (!r.length) { setFlow(null); setError(""); return; }   // books empty until Start: nothing to show yet
      const pick = selectedRef.current || d.focus;
      setFlow(await call<Flow>(`/pipeline/demo/flow/${pick}`));
    } catch (e) {
      if (!/demo not started/.test(String(e))) setError(String((e as Error).message || e));
      setDemo(null);
    }
  }, []);

  // live events
  useEffect(() => {
    const es = new EventSource(`${API}/pipeline/stream`);
    let seq = 0;      // stable keys: only a new line animates in, the rest stay put
    const log = (agent: string, kind: string, detail: string) =>
      setLines(prev => [{ id: ++seq, at: clock(), agent, kind, detail }, ...prev].slice(0, 80));
    const on = (type: string, f: (m: Record<string, unknown>) => void) =>
      es.addEventListener(type, e => f(JSON.parse((e as MessageEvent).data)));
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    on("day_started", m => { log("system", "day", `${day(String(m.day))} begins`); refresh(); });
    on("evidence", m => log("system", "evidence", `${String(m.kind).replace("_", " ")} arrived · ${String(m.text || m.document).slice(0, 110)}`));
    on("task_started", m => {
      setLive({ task_id: String(m.task_id), agent: String(m.agent), invoice_id: String(m.invoice_id), day: String(m.day),
        reason: String(m.reason), thinking: "", text: "", tools: [], queries: [], decision: null, status: "working" });
      setSeen(prev => new Set(prev).add(String(m.agent)));
      log(String(m.agent), "start", `${m.invoice_id} · ${m.reason}`);
    });
    on("query", m => {
      setLive(l => (l && l.task_id === m.task_id ? { ...l, queries: [...l.queries, m as unknown as Query] } : l));
      log(String(m.agent), m.error ? "refused" : "query", `${m.purpose}${m.error ? ` · ${m.error}` : ` · ${m.row_count} rows`}`);
    });
    on("thinking", m => setLive(l => (l && l.task_id === m.task_id ? { ...l, thinking: l.thinking + String(m.text) } : l)));
    on("text", m => setLive(l => (l && l.task_id === m.task_id ? { ...l, text: l.text + String(m.text) } : l)));
    on("tool_call", m => {
      setLive(l => (l && l.task_id === m.task_id ? { ...l, tools: [...l.tools, { tool: String(m.tool), state: "running" }] } : l));
      log(String(m.agent), "tool call", String(m.tool));
    });
    on("tool_result", m => {
      setLive(l => {
        if (!l || l.task_id !== m.task_id) return l;
        const tools = [...l.tools];
        const i = tools.map(t => t.tool).lastIndexOf(String(m.tool));
        const next: LiveTool = { tool: String(m.tool), state: m.refused ? "refused" : "done", preview: String(m.preview || "") };
        if (i >= 0 && tools[i].state === "running") tools[i] = next; else tools.push(next);
        return { ...l, tools };
      });
      log(String(m.agent), m.refused ? "refused" : "result", `${m.tool}${m.refused ? " refused by guardrail" : ""}`);
    });
    on("decision", m => {
      setLive(l => (l && l.task_id === m.task_id ? { ...l, decision: String(m.tool) } : l));
      log(String(m.agent), "decision", String(m.tool));
    });
    on("task_done", m => {
      const o = (m.outcome || {}) as Outcome;
      setLive(l => (l && l.task_id === m.task_id ? { ...l, status: m.status === "failed" ? "failed" : "done",
        decision: l.decision || o.decision || null,
        tools: l.tools.map(t => (t.state === "running" ? { ...t, state: "done" } : t)) } : l));
      if (o.decided_by === "schedule") log(String(m.agent), "wait", `${m.invoice_id} · not due yet, no model call`);
      refresh();
    });
    on("day_done", () => refresh());
    on("email_sent", m => log("system", "email", `${m.invoice_id} · ${m.subject}${m.simulated ? " (simulated)" : ""}`));
    on("flag_raised", m => log(String(m.agent || "chaser"), "flag", `${m.invoice_id} · ${FLAG[String(m.kind)] || m.kind} · cites ${(m.evidence as string[] || []).join(", ")}`));
    on("email_failed", m => log("system", "refused", `email not sent: ${m.error}`));
    return () => es.close();
  }, [refresh]);

  useEffect(() => { refresh(); }, [refresh]);
  const thinking = usePaced(live?.thinking || "", live?.status !== "working");
  const thinkRef = useFollow(`${thinking.length}:${live?.tools.length}:${live?.queries.length}`);
  const recordRef = useFollow(flow?.tasks.length, true);

  const select = (id: string) => { if (id === "portfolio") return; selectedRef.current = id; setSelected(id); setOpen(null); refresh(); };
  const act = async (what: string, path: string) => {
    setBusy(what); setError("");
    try { await call(path, "POST"); } catch (e) { setError(String((e as Error).message || e)); playRef.current = false; setPlaying(false); }
    setBusy(""); await refresh();
  };
  const reset = () => { setLive(null); setLines([]); return act("Starting Apr 22", "/pipeline/demo/reset"); };
  const next = () => act("Agents working", "/pipeline/demo/next-day");
  const play = async () => {
    if (playRef.current) { playRef.current = false; setPlaying(false); return; }
    playRef.current = true; setPlaying(true);
    for (;;) {
      const d = await call<Demo>("/pipeline/demo").catch(() => null);
      if (!playRef.current || !d || d.done) break;
      await next();
      // A person must decide before the story moves on: pause on any draft waiting for approval.
      const f = await call<Flow>(`/pipeline/demo/flow/${d.focus}`).catch(() => null);
      if (f?.chaser_actions.some(a => a.status === "needs_approval")) break;
      await new Promise(r => setTimeout(r, 600));
    }
    playRef.current = false; setPlaying(false);
  };
  const decide = (id: string, approve: boolean) =>
    act(approve ? "Approving" : "Rejecting", `/pipeline/demo/actions/${encodeURIComponent(id)}/decide?approve=${approve}&note=${approve ? "approved" : "rejected"}`);

  const tasks = flow?.tasks || [];
  // Every agent call across all invoices, not just the case on screen.
  // The service's total includes the investigator's portfolio sweep, which no invoice row carries.
  const usage = demo?.usage ?? rows.reduce((u, r) => (r.usage ? { calls: u.calls + r.usage.calls, input: u.input + r.usage.input,
    cached: u.cached + r.usage.cached, output: u.output + r.usage.output } : u), { calls: 0, input: 0, cached: 0, output: 0 });
  const cost = (usage.input - usage.cached) * PRICE.input + usage.cached * PRICE.cached + usage.output * PRICE.output;
  const pending = flow?.chaser_actions.find(a => a.action !== "flag" && ["needs_approval", "needs_controller"].includes(a.status));
  const flags = flow?.chaser_actions.filter(a => a.action === "flag") || [];
  const raised = !!flow?.chaser_actions.some(a => a.action === "escalate");
  const review = flow?.case?.state === "needs_controller" || raised || flags.length > 0;
  const current = selected || demo?.focus || "";
  const emails = rows.reduce((n, r) => n + r.reminders.length, 0);
  const controllerWaiting = !!pending || review;
  const activeAgent = live?.status === "working" ? live.agent : null;
  const ran = (k: string) => (k === "agreement" ? (flow?.rules.length ?? 0) > 0
    : seen.has(k) || tasks.some(t => t.agent === k) || rows.some(r => r.last?.agent === k));
  const finished = !!demo?.done && live?.status !== "working";

  return (
    <div className={s.wrap}>
      <header className={s.hero}>
        <div className={s.eyebrow}>
          <span className={`${s.dot} ${connected ? s.on : ""}`} />
          <span>{connected ? "Live" : "Connecting"}</span>
          {demo && <span className={s.faint}>Keyway Property Management · {day(demo.today)}</span>}
        </div>
        <div className={s.heroRow}>
          <div>
            <p className={s.kicker}>Agreement · Investigator · Chaser · Anomaly</p>
            <h2 className={s.headline}>The agents, <em>working the books.</em></h2>
            <p className={s.lede}>Each day they read the new paperwork and check every invoice against the agreement.
              Anything that would hold money waits for you.</p>
          </div>
          <div className={s.controls}>
            {busy && <span className={s.busy}><span className={s.spinner} />{busy}</span>}
            {demo ? <>
              <button className={s.textBtn} onClick={reset} disabled={!!busy || playing}>Reset</button>
              <button className={s.textBtn} onClick={next} disabled={!!busy || playing || demo.done}>Next day <span aria-hidden>↗</span></button>
              <button className={s.primary} onClick={play} disabled={(!!busy && !playing) || demo.done}>
                {playing ? "Pause" : "Play"}<span className={s.orb} aria-hidden>{playing ? "❚❚" : "↘"}</span></button>
            </> : <button className={s.primary} onClick={reset} disabled={!!busy}>Start<span className={s.orb} aria-hidden>↘</span></button>}
          </div>
        </div>
      </header>

      {error && <div className={s.error}>{error}</div>}

      {demo && <div className={s.metrics}>
        {([["Invoices checked", rows.length, num], ["Reminder emails", emails, num], ["Model calls", usage.calls, num],
          ["Input tokens", usage.input, num], ["Output tokens", usage.output, num],
          ["Cost", cost, (n: number) => `$${n.toFixed(3)}`]] as [string, number, (n: number) => string][]).map(([k, v, f]) =>
          <div key={k} className={s.metric}><b><Count value={v} format={x => f(k === "Cost" ? x : Math.round(x))} /></b><span>{k}</span></div>)}
      </div>}

      {demo && <div className={s.engine}>
        {[["agreement", "Agreement agent"], ["investigator", "Investigator"], ["chaser", "Chaser agent"], ["anomaly", "Anomaly agent"], ["controller", "Controller"]].map(([k, n], i) => {
          const waiting = k === "controller" && controllerWaiting;
          return <div key={k} className={s.stage}>
            <span className={`${s.node} ${activeAgent === k ? s.active : ""} ${waiting ? s.waiting : ran(k) ? s.done : ""}`} />
            <span className={s.stageNum}>{String(i + 1).padStart(2, "0")}</span>
            <span>{n}{waiting && <em className={s.pendingTag}>decision pending</em>}</span>{i < 4 && <span className={s.link} />}
          </div>;
        })}
      </div>}

      {demo && <InvoiceTable rows={rows} selected={current} working={activeAgent ? live?.invoice_id || null : null} onSelect={select} />}

      {demo && flow && <div className={s.caseHead}><span className="micro">Case</span>
        <span className={s.mono}>{current} · {flow.vendor}</span></div>}
      {demo && flow && <CaseStrip flow={flow} today={demo.today} />}

      {pending && <div className={s.approval}>
        <span className="micro">Approval required{!playing && " · paused"}</span>
        <span>{pending.action === "hold_funding" ? `${pending.message || "Funding hold proposed"}`
          : `${(pending.level || pending.action).replace("_", " ")} reminder to Keyway · ${pending.invoice_id}`}</span>
        <span className={s.grow} />
        <button className="btn ghost" onClick={() => decide(pending.action_id, false)} disabled={!!busy}>Reject</button>
        <button className="btn" onClick={() => decide(pending.action_id, true)} disabled={!!busy}>Approve</button>
      </div>}

      {demo && <div className={s.grid}>
        <section className={s.panel}>
          <div className={s.panelHead}>
            <span className="micro">Live reasoning</span>
            {live && (live.invoice_id === "portfolio"
              ? <span className={s.mono}>investigator · all invoices · {day(live.day)}</span>
              : <button className={s.linkBtn} onClick={() => select(live.invoice_id)}>{live.agent} agent · {live.invoice_id} · {day(live.day)}</button>)}
            {live && <span className={`${s.state} ${s[live.status]}`}>{live.status === "working" ? "Working" : live.status === "failed" ? "Failed" : "Done"}</span>}
          </div>
          {live ? <div className={s.liveBody} ref={thinkRef} data-lenis-prevent>
            <p className={s.reason}>{plain(live.reason)}</p>
            {(live.thinking || live.status === "working" || live.tools.length === 0) && <div className={s.thinking}>
              {plain(thinking) || (live.status === "working" ? "" : "Not due yet: the schedule waited without calling the model.")}
              {live.status === "working" && <span className={s.caret} />}</div>}
            {live.queries.length > 0 && <div className={s.queries}>
              {live.queries.map((q, i) => <details key={i} className={`${s.query} ${q.error ? s.refused : ""}`}>
                <summary className={s.queryHead}><span className={s.ring} /><b>{q.purpose}</b>
                  <span className={s.toolState}>{q.error ? "Refused" : `${q.row_count} rows`}</span></summary>
                <pre className={s.sql}>{q.sql}</pre>
                {q.error ? <p className={s.queryErr}>{q.error}</p> : !!q.rows?.length && <div className={s.qtableWrap}><table className={s.qtable}>
                  <thead><tr>{q.columns?.map((c, k) => <th key={k}>{c}</th>)}</tr></thead>
                  <tbody>{q.rows.slice(0, 6).map((r, j) => <tr key={j}>{r.map((v, k) => <td key={k}>{v == null ? "—" : String(v)}</td>)}</tr>)}</tbody>
                </table>{(q.row_count || 0) > Math.min(6, q.rows.length) && <span className={s.more}>+{(q.row_count || 0) - Math.min(6, q.rows.length)} more rows</span>}</div>}
              </details>)}
            </div>}
            {(() => {
              const groups: { tool: string; n: number; state: LiveTool["state"]; preview?: string }[] = [];
              for (const t of live.tools.filter(t => t.tool !== "query_books")) {
                const g = groups.find(x => x.tool === t.tool);
                const state = t.state === "refused" || g?.state === "refused" ? "refused" : t.state === "running" || g?.state === "running" ? "running" : "done";
                if (g) { g.n += 1; g.state = state; if (t.state === "refused") g.preview = t.preview; }
                else groups.push({ tool: t.tool, n: 1, state, preview: t.state === "refused" ? t.preview : undefined });
              }
              return groups.length > 0 && <ol className={s.tools}>
                {groups.map(g => <li key={g.tool} className={s[g.state]}>
                  <span className={s.ring} /><span className={s.mono}>{g.tool.replace(/_/g, " ")}{g.n > 1 && <em className={s.times}> ×{g.n}</em>}</span>
                  <span className={s.toolState}>{g.state === "running" ? "Running" : g.state === "refused" ? "Refused" : "Done"}</span>
                  {g.preview && <pre>{g.preview}</pre>}
                </li>)}
              </ol>;
            })()}
            {live.text && <p className={s.text}>{plain(live.text)}</p>}
          </div> : <p className={s.empty}>{finished
            ? "Run complete. Press Reset to watch it again from Apr 22."
            : "Press Play. Each day the agents wake on their own; their reasoning appears here as it is written."}</p>}
        </section>

        <section className={s.panel}>
          <div className={s.panelHead}><span className="micro">Decisions · {current}</span><span className={s.mono}>oldest first</span></div>
          <div className={s.record} ref={recordRef} data-lenis-prevent>
            {tasks.length === 0 && <p className={s.empty}>No decisions yet.</p>}
            {flow && tasks.map(t => {
              const o = t.outcome || {}, quiet = o.decided_by === "schedule" || o.decided_by === "code", d = describe(t, flow);
              return <div key={t.task_id} className={`${s.item} ${quiet ? s.quiet : ""}`}>
                <button className={s.itemHead} onClick={() => setOpen(open === t.task_id ? null : t.task_id)} disabled={quiet}>
                  <span className={s.mono}>{day(t.as_of)}</span>
                  <span className={s.agentTag}>{t.agent}</span>
                  <span className={`${s.badge} ${d.tone ? s[d.tone] : ""}`}>{d.label}</span>
                  <span className={s.src}>{o.decided_by === "code" ? "Code" : quiet ? "Schedule" : "Claude"}</span>
                </button>
                <p className={s.itemText}>{d.line}</p>
                {open === t.task_id && <div className={s.detail}>
                  <p className={s.why}>{plain(o.summary || o.rationale)}</p>
                  {(t.trace?.thinking || []).map((x, i) => <p key={i} className={s.quote}>{plain(x)}</p>)}
                  {(t.trace?.steps || []).map((st, i) => <div key={i} className={`${s.step} ${st.refused ? s.refused : ""}`}>
                    <span className={s.mono}>{i + 1}. {st.tool}</span><span className={s.toolState}>{st.refused ? "Refused" : st.terminal ? "Decision" : "Read"}</span>
                  </div>)}
                </div>}
              </div>;
            })}
            {review && demo && <div className={`${s.item} ${s.controllerItem}`}>
              <div className={s.itemHead}>
                <span className={s.mono}>{day(demo.today)}</span><span className={s.agentTag}>controller</span>
                <span className={`${s.badge} ${s.hold}`}>Decision pending</span><span className={s.src}>Human</span>
              </div>
              <p className={s.itemText}>Review: {[...(flow?.case?.findings || []).map(f => FINDING[f.code] || f.message),
                ...(raised ? ["no proof of payment after three reminders"] : []),
                ...flags.map(f => `flagged by the chaser: ${FLAG[f.level || ""] || f.level}`)].join("; ")}. Only a person can close this case or hold funds.</p>
              {flags.map(f => <p key={f.action_id} className={s.quote}>{plain(f.message)}</p>)}
            </div>}
          </div>
        </section>
      </div>}

      {demo && <section className={`${s.panel} ${s.events}`}>
        <div className={s.panelHead}><span className="micro">Event stream</span><span className={s.mono}>{lines.length} events</span></div>
        <div className={s.eventList} data-lenis-prevent>
          {lines.length === 0 && <p className={s.empty}>Events appear here as the agents work.</p>}
          {lines.map(l => <div key={l.id} className={`${s.event} ${s[l.kind.replace(" ", "")] || ""}`}>
            <span>{l.at}</span><span>{l.agent}</span><span>{l.kind}</span><span>{l.detail}</span></div>)}
        </div>
      </section>}
    </div>
  );
}
