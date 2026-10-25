"use client";

// The three agents working live: reasoning streams in as Claude writes it, each tool call and its
// result appear as they happen, decisions and hand-offs land in the record. Data comes from the
// agent service (chaser_agent/pipeline_api.py): /pipeline/stream (Server-Sent Events) for the live
// events, /pipeline/demo/* for the time-lapse and the stored record.

import { useCallback, useEffect, useRef, useState } from "react";
import s from "./LivePipeline.module.css";

const API = process.env.NEXT_PUBLIC_AGENTS_URL || "http://127.0.0.1:8773";
const PRICE = { input: 4e-6, cached: 4e-7, output: 2e-5 };      // Claude Opus 5.5, USD per token

type Step = { tool: string; input?: unknown; result?: unknown; refused?: boolean; terminal?: boolean };
type Trace = { model?: string; thinking?: string[]; steps?: Step[]; usage?: Record<string, number> };
type Outcome = { decision?: string | null; rationale?: string | null; verdict?: string | null; summary?: string | null;
  finding_codes?: string[] | null; decided_by?: string; error?: string | null };
type Task = { task_id: string; agent: "chaser" | "anomaly"; invoice_id: string; status: string; as_of: string;
  created_by: string; reason: string; outcome: Outcome; trace?: Trace };
type Flow = { vendor: string; tasks: Task[]; chaser_actions: { action_id: string; status: string; level?: string | null;
  action: string; invoice_id: string }[]; rules: { rule_type: string; check: string }[] };
type Demo = { today: string; start: string; end: string; focus: string; done: boolean };
type LiveTool = { tool: string; state: "running" | "done" | "refused"; preview?: string; input?: unknown };
type Live = { task_id: string; agent: string; invoice_id: string; day: string; reason: string;
  thinking: string; text: string; tools: LiveTool[]; decision: string | null; status: "working" | "done" | "failed" };
type Line = { at: string; agent: string; kind: string; detail: string };

const LABEL: Record<string, string> = { send_reminder: "Reminder", wait: "Wait", escalate: "Escalation",
  handoff_to_anomaly: "Hand-off", record_verdict: "Verdict" };
const label = (d?: string | null) => (d ? LABEL[d] || d : "—");
const verdict = (v?: string | null) => (v ? v[0].toUpperCase() + v.slice(1).replace("_", " ") : "");
const day = (d?: string) => (d ? new Date(d + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "—");
const num = (n: number) => n.toLocaleString("en-US");
const clock = () => new Date().toLocaleTimeString("en-US", { hour12: false });

async function call<T>(path: string, method = "GET"): Promise<T> {
  const r = await fetch(`${API}${path}`, { method, cache: "no-store" });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((body as { detail?: string }).detail || `${path}: ${r.status}`);
  return body as T;
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
    const on = (type: string, f: (m: Record<string, unknown> & { agent?: string; invoice_id?: string }) => void) =>
      es.addEventListener(type, e => f(JSON.parse((e as MessageEvent).data)));
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    on("day_started", m => log("system", "day", `Day ${day(String(m.day))} begins`));
    on("evidence", m => log("system", "evidence", `New ${String(m.kind).replace("_", " ")} arrived: ${m.document}`));
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
        const next: LiveTool = { tool: String(m.tool), state: m.refused ? "refused" : "done", preview: String(m.preview || ""), input: m.input };
        if (i >= 0 && tools[i].state === "running") tools[i] = next; else tools.push(next);
        return { ...l, tools };
      });
      log(String(m.agent), m.refused ? "refused" : "result", `${m.tool}${m.refused ? " refused by guardrail" : ""}`);
    });
    on("decision", m => {
      setLive(l => (l && l.task_id === m.task_id ? { ...l, decision: String(m.tool) } : l));
      log(String(m.agent), "decision", label(String(m.tool)));
    });
    on("task_done", m => {
      const o = (m.outcome || {}) as Outcome;
      setLive(l => (l && l.task_id === m.task_id ? { ...l, status: m.status === "failed" ? "failed" : "done",
        decision: l.decision || o.decision || null } : l));
      if (o.decided_by === "schedule") log(String(m.agent), "wait", `${m.invoice_id} · schedule waited, no model call`);
      refresh();
    });
    on("day_done", () => refresh());
    return () => es.close();
  }, [refresh]);

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => { thinkRef.current?.scrollTo({ top: thinkRef.current.scrollHeight }); }, [live?.thinking, live?.tools.length]);

  const act = async (what: string, path: string) => {
    setBusy(what); setError("");
    try { await call(path, "POST"); } catch (e) { setError(String((e as Error).message || e)); playRef.current = false; setPlaying(false); }
    setBusy(""); await refresh();
  };
  const reset = () => { setLive(null); setLines([]); return act("Resetting and running the first day", "/pipeline/demo/reset"); };
  const next = () => act("Agents working", "/pipeline/demo/next-day");
  const play = async () => {
    if (playRef.current) { playRef.current = false; setPlaying(false); return; }
    playRef.current = true; setPlaying(true);
    for (;;) {
      const d = await call<Demo>("/pipeline/demo").catch(() => null);
      if (!playRef.current || !d || d.done) break;
      await next();
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
  const activeAgent = live?.status === "working" ? live.agent : null;
  const record = [...tasks].reverse();

  return (
    <div className={s.wrap}>
      <div className={s.bar}>
        <div className={s.title}>
          <span className={`${s.dot} ${connected ? s.on : ""}`} />
          <span className="micro">{connected ? "Live" : "Connecting"}</span>
          {demo && flow && <span className={s.mono}>{demo.focus} · {flow.vendor}</span>}
        </div>
        <div className={s.controls}>
          {busy && <span className={s.busy}>{busy}</span>}
          {demo ? <>
            <button className="btn ghost" onClick={reset} disabled={!!busy || playing}>Reset</button>
            <button className="btn ghost" onClick={next} disabled={!!busy || playing || demo.done}>Next day</button>
            <button className="btn" onClick={play} disabled={!!busy && !playing}>{playing ? "Pause" : "Play"}</button>
          </> : <button className="btn" onClick={reset} disabled={!!busy}>Start</button>}
        </div>
      </div>

      {error && <div className={s.error}>{error}</div>}

      {demo && <div className={s.metrics}>
        {[["Day", day(demo.today)], ["Model calls", num(usage.calls)], ["Input tokens", num(usage.input)],
          ["Output tokens", num(usage.output)], ["Cost", `$${cost.toFixed(3)}`]].map(([k, v]) =>
          <div key={k} className={s.metric}><span className="micro">{k}</span><b>{v}</b></div>)}
      </div>}

      {demo && <div className={s.engine}>
        {[["agreement", "Agreement agent"], ["chaser", "Chaser agent"], ["anomaly", "Anomaly agent"], ["controller", "Controller"]].map(([k, n], i) =>
          <div key={k} className={s.stage}>
            <span className={`${s.node} ${activeAgent === k ? s.active : ""} ${(k === "agreement" && (flow?.rules.length ?? 0) > 0)
              || tasks.some(t => t.agent === k) || (k === "controller" && pending) ? s.done : ""}`} />
            <span>{n}</span>{i < 3 && <span className={s.link} />}
          </div>)}
      </div>}

      {pending && <div className={s.approval}>
        <span className="micro">Approval required</span>
        <span>{(pending.level || pending.action).replace("_", " ")} reminder · {pending.invoice_id}</span>
        <span className={s.grow} />
        <button className="btn ghost" onClick={() => decide(pending.action_id, false)} disabled={!!busy}>Reject</button>
        <button className="btn" onClick={() => decide(pending.action_id, true)} disabled={!!busy}>Approve</button>
      </div>}

      {demo && <div className={s.grid}>
        <section className={`card ${s.panel}`}>
          <div className={s.panelHead}>
            <span className="micro">Live reasoning</span>
            {live && <span className={s.mono}>{live.agent} agent · {live.invoice_id} · {day(live.day)}</span>}
            {live && <span className={`${s.state} ${s[live.status]}`}>{live.status === "working" ? "Working" : live.status === "failed" ? "Failed" : label(live.decision)}</span>}
          </div>
          {live ? <div className={s.liveBody} ref={thinkRef}>
            <p className={s.reason}>{live.reason}</p>
            <div className={s.thinking}>{live.thinking || (live.status === "working" ? "" : "No model call: the schedule waited.")}
              {live.status === "working" && <span className={s.caret} />}</div>
            {live.tools.length > 0 && <ol className={s.tools}>
              {live.tools.map((t, i) => <li key={i} className={s[t.state]}>
                <span className={s.ring} /><span className={s.mono}>{t.tool}</span>
                <span className={s.toolState}>{t.state === "running" ? "Running" : t.state === "refused" ? "Refused" : "Done"}</span>
                {t.preview && <pre>{t.preview}</pre>}
              </li>)}
            </ol>}
            {live.text && <p className={s.text}>{live.text}</p>}
          </div> : <p className={s.empty}>Press Play. Each day the agents wake on their own; their reasoning appears here as it is written.</p>}
        </section>

        <section className={`card ${s.panel}`}>
          <div className={s.panelHead}><span className="micro">Decisions</span></div>
          <div className={s.record}>
            {record.length === 0 && <p className={s.empty}>No decisions yet.</p>}
            {record.map(t => {
              const o = t.outcome || {}, quiet = o.decided_by === "schedule";
              return <div key={t.task_id} className={`${s.item} ${quiet ? s.quiet : ""}`}>
                <button className={s.itemHead} onClick={() => setOpen(open === t.task_id ? null : t.task_id)}>
                  <span className={s.mono}>{day(t.as_of)}</span>
                  <span className={s.agentTag}>{t.agent}</span>
                  <span className={`${s.badge} ${o.verdict === "needs_review" || o.decision === "handoff_to_anomaly" ? s.hold : ""}`}>
                    {o.verdict ? verdict(o.verdict) : label(o.decision)}</span>
                  <span className={s.src}>{quiet ? "Schedule" : "Model"}</span>
                </button>
                {!quiet && <p className={s.itemText}>{o.summary || o.rationale || o.error}</p>}
                {open === t.task_id && <div className={s.detail}>
                  {(t.trace?.thinking || []).map((x, i) => <p key={i} className={s.quote}>{x}</p>)}
                  {(t.trace?.steps || []).map((st, i) => <div key={i} className={`${s.step} ${st.refused ? s.refused : ""}`}>
                    <span className={s.mono}>{i + 1}. {st.tool}</span><span className={s.toolState}>{st.refused ? "Refused" : st.terminal ? "Decision" : "Read"}</span>
                  </div>)}
                  {(o.finding_codes || []).length > 0 && <div className={s.codes}>{o.finding_codes!.map(c => <span key={c}>{c}</span>)}</div>}
                </div>}
              </div>;
            })}
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
