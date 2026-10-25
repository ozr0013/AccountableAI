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
type Action = { action_id: string; status: string; level?: string | null; action: string; invoice_id: string; as_of: string };
type Invoice = { amount: number; funded_amount?: number | null; funded_on?: string | null; paid_amount?: number | null;
  paid_on?: string | null; invoice_received?: string | null };
type Claim = { statement: string; dated: string; says?: string; amount?: number; paid_on?: string | null };
type Flow = { vendor: string; invoice: Invoice; tasks: Task[]; chaser_actions: Action[]; deadline?: string | null;
  statements?: Claim[]; rules: { rule_type: string; check: string }[];
  case?: { state?: string; findings?: { code: string; message: string }[] } | null };
type Demo = { today: string; start: string; end: string; focus: string; done: boolean };
type LiveTool = { tool: string; state: "running" | "done" | "refused"; preview?: string };
type Live = { task_id: string; agent: string; invoice_id: string; day: string; reason: string;
  thinking: string; text: string; tools: LiveTool[]; decision: string | null; status: "working" | "done" | "failed" };
type Line = { at: string; agent: string; kind: string; detail: string };

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
    return { label: `${level} reminder sent`, tone: "",
      line: `${late} days past the ${day(flow.deadline)} deadline. Sent to Keyway automatically.` };
  }
  if (o.decision === "escalate") return { label: "Escalated", tone: "hold", line: "Owner and Keyway notified; controller review opened." };
  if (o.decision === "handoff_to_anomaly") return { label: "Handed to Anomaly agent", tone: "",
    line: "Bank payment found. Anomaly agent checks amount, payee and timing." };
  return { label: o.decision || t.status, tone: "", line: plain(o.error) };
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
  const playRef = useRef(false);
  const thinkRef = useRef<HTMLDivElement>(null);
  const recordRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      const d = await call<Demo>("/pipeline/demo");
      setDemo(d);
      setFlow(await call<Flow>(`/pipeline/demo/flow/${d.focus}`));
    } catch (e) {
      if (!/demo not started/.test(String(e))) setError(String((e as Error).message || e));
      setDemo(null);
    }
  }, []);

  // live events
  useEffect(() => {
    const es = new EventSource(`${API}/pipeline/stream`);
    const log = (agent: string, kind: string, detail: string) =>
      setLines(prev => [{ at: clock(), agent, kind, detail }, ...prev].slice(0, 80));
    const on = (type: string, f: (m: Record<string, unknown>) => void) =>
      es.addEventListener(type, e => f(JSON.parse((e as MessageEvent).data)));
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    on("day_started", m => { log("system", "day", `${day(String(m.day))} begins`); refresh(); });
    on("evidence", m => { log("system", "evidence", `New ${String(m.kind).replace("_", " ")} arrived: ${m.document}`); refresh(); });
    on("task_started", m => {
      setLive({ task_id: String(m.task_id), agent: String(m.agent), invoice_id: String(m.invoice_id), day: String(m.day),
        reason: String(m.reason), thinking: "", text: "", tools: [], decision: null, status: "working" });
      log(String(m.agent), "start", `${m.invoice_id} · ${m.reason}`);
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
        decision: l.decision || o.decision || null } : l));
      if (o.decided_by === "schedule") log(String(m.agent), "wait", `${m.invoice_id} · not due yet, no model call`);
      refresh();
    });
    on("day_done", () => refresh());
    return () => es.close();
  }, [refresh]);

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => { thinkRef.current?.scrollTo({ top: thinkRef.current.scrollHeight }); }, [live?.thinking, live?.tools.length]);
  useEffect(() => { recordRef.current?.scrollTo({ top: recordRef.current.scrollHeight, behavior: "smooth" }); }, [flow?.tasks.length]);

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
      await new Promise(r => setTimeout(r, 1800));
    }
    playRef.current = false; setPlaying(false);
  };
  const decide = (id: string, approve: boolean) =>
    act(approve ? "Approving" : "Rejecting", `/pipeline/demo/actions/${encodeURIComponent(id)}/decide?approve=${approve}&note=${approve ? "approved" : "rejected"}`);

  const tasks = flow?.tasks || [];
  const usage = tasks.reduce((u, t) => {
    const x = t.trace?.usage || {};
    if (!x.output_tokens) return u;
    return { calls: u.calls + 1, input: u.input + (x.input_tokens || 0) + (x.cache_read_input_tokens || 0),
      cached: u.cached + (x.cache_read_input_tokens || 0), output: u.output + (x.output_tokens || 0) };
  }, { calls: 0, input: 0, cached: 0, output: 0 });
  const cost = (usage.input - usage.cached) * PRICE.input + usage.cached * PRICE.cached + usage.output * PRICE.output;
  const pending = flow?.chaser_actions.find(a => ["needs_approval", "needs_controller"].includes(a.status));
  const review = flow?.case?.state === "needs_controller";
  const controllerWaiting = !!pending || review;
  const activeAgent = live?.status === "working" ? live.agent : null;
  const ran = (k: string) => (k === "agreement" ? (flow?.rules.length ?? 0) > 0 : tasks.some(t => t.agent === k));
  const finished = !!demo?.done && live?.status !== "working";

  return (
    <div className={s.wrap}>
      <div className={s.bar}>
        <div className={s.title}>
          <span className={`${s.dot} ${connected ? s.on : ""}`} />
          <span className="micro">{connected ? "Live" : "Connecting"}</span>
          {demo && flow && <span className={s.mono}>{demo.focus} · {flow.vendor} · {day(demo.today)}</span>}
        </div>
        <div className={s.controls}>
          {busy && <span className={s.busy}>{busy}</span>}
          {demo ? <>
            <button className="btn ghost" onClick={reset} disabled={!!busy || playing}>Reset</button>
            <button className="btn ghost" onClick={next} disabled={!!busy || playing || demo.done}>Next day</button>
            <button className="btn" onClick={play} disabled={(!!busy && !playing) || demo.done}>{playing ? "Pause" : "Play"}</button>
          </> : <button className="btn" onClick={reset} disabled={!!busy}>Start</button>}
        </div>
      </div>

      {error && <div className={s.error}>{error}</div>}

      {demo && flow && <CaseStrip flow={flow} today={demo.today} />}

      {demo && <div className={s.metrics}>
        {[["Model calls", num(usage.calls)], ["Input tokens", num(usage.input)],
          ["Output tokens", num(usage.output)], ["Cost", `$${cost.toFixed(3)}`]].map(([k, v]) =>
          <div key={k} className={s.metric}><span className="micro">{k}</span><b>{v}</b></div>)}
      </div>}

      {demo && <div className={s.engine}>
        {[["agreement", "Agreement agent"], ["chaser", "Chaser agent"], ["anomaly", "Anomaly agent"], ["controller", "Controller"]].map(([k, n], i) => {
          const waiting = k === "controller" && controllerWaiting;
          return <div key={k} className={s.stage}>
            <span className={`${s.node} ${activeAgent === k ? s.active : ""} ${waiting ? s.waiting : ran(k) ? s.done : ""}`} />
            <span>{n}{waiting && <em className={s.pendingTag}>decision pending</em>}</span>{i < 3 && <span className={s.link} />}
          </div>;
        })}
      </div>}

      {pending && <div className={s.approval}>
        <span className="micro">Approval required{!playing && " · paused"}</span>
        <span>{(pending.level || pending.action).replace("_", " ")} reminder to Keyway · {pending.invoice_id}</span>
        <span className={s.grow} />
        <button className="btn ghost" onClick={() => decide(pending.action_id, false)} disabled={!!busy}>Reject</button>
        <button className="btn" onClick={() => decide(pending.action_id, true)} disabled={!!busy}>Approve</button>
      </div>}

      {demo && <div className={s.grid}>
        <section className={`card ${s.panel}`}>
          <div className={s.panelHead}>
            <span className="micro">Live reasoning</span>
            {live && <span className={s.mono}>{live.agent} agent · {day(live.day)}</span>}
            {live && <span className={`${s.state} ${s[live.status]}`}>{live.status === "working" ? "Working" : live.status === "failed" ? "Failed" : "Done"}</span>}
          </div>
          {live ? <div className={s.liveBody} ref={thinkRef}>
            <p className={s.reason}>{plain(live.reason)}</p>
            {(live.thinking || live.status === "working" || live.tools.length === 0) && <div className={s.thinking}>
              {plain(live.thinking) || (live.status === "working" ? "" : "Not due yet: the schedule waited without calling the model.")}
              {live.status === "working" && <span className={s.caret} />}</div>}
            {live.tools.length > 0 && <ol className={s.tools}>
              {live.tools.map((t, i) => <li key={i} className={s[t.state]}>
                <span className={s.ring} /><span className={s.mono}>{t.tool}</span>
                <span className={s.toolState}>{t.state === "running" ? "Running" : t.state === "refused" ? "Refused" : "Done"}</span>
                {t.preview && <pre>{t.preview}</pre>}
              </li>)}
            </ol>}
            {live.text && <p className={s.text}>{plain(live.text)}</p>}
          </div> : <p className={s.empty}>{finished
            ? "Run complete. Press Reset to watch it again from Apr 22."
            : "Press Play. Each day the agents wake on their own; their reasoning appears here as it is written."}</p>}
        </section>

        <section className={`card ${s.panel}`}>
          <div className={s.panelHead}><span className="micro">Decisions</span><span className={s.mono}>oldest first</span></div>
          <div className={s.record} ref={recordRef}>
            {tasks.length === 0 && <p className={s.empty}>No decisions yet.</p>}
            {flow && tasks.map(t => {
              const o = t.outcome || {}, quiet = o.decided_by === "schedule", d = describe(t, flow);
              return <div key={t.task_id} className={`${s.item} ${quiet ? s.quiet : ""}`}>
                <button className={s.itemHead} onClick={() => setOpen(open === t.task_id ? null : t.task_id)} disabled={quiet}>
                  <span className={s.mono}>{day(t.as_of)}</span>
                  <span className={s.agentTag}>{t.agent}</span>
                  <span className={`${s.badge} ${d.tone ? s[d.tone] : ""}`}>{d.label}</span>
                  <span className={s.src}>{quiet ? "Schedule" : "Claude"}</span>
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
              <p className={s.itemText}>Review: {(flow?.case?.findings || []).map(f => FINDING[f.code] || f.message).join("; ")}. Only a person can close this case or hold funds.</p>
            </div>}
          </div>
        </section>
      </div>}

      {demo && <section className={`card ${s.panel} ${s.events}`}>
        <div className={s.panelHead}><span className="micro">Event stream</span><span className={s.mono}>{lines.length} events</span></div>
        <div className={s.eventList}>
          {lines.length === 0 && <p className={s.empty}>Events appear here as the agents work.</p>}
          {lines.map((l, i) => <div key={i} className={`${s.event} ${s[l.kind.replace(" ", "")] || ""}`}>
            <span>{l.at}</span><span>{l.agent}</span><span>{l.kind}</span><span>{l.detail}</span></div>)}
        </div>
      </section>}
    </div>
  );
}
