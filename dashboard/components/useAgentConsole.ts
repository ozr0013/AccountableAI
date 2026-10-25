"use client";

// Judge Console on the real agents: send a file to the agent API (/pipeline/console/*) and build
// the console's cards, map and log from the agents' live stream (/pipeline/stream) as each step
// actually happens. Nothing is scripted: a card exists only because that event was emitted.

import { useCallback, useEffect, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_AGENTS_URL || "http://127.0.0.1:8773";

export type LiveLine = { kind: "check" | "result" | "evidence" | "thinking" | "sql"; text: string; rows?: unknown[][]; columns?: string[] };
export type LiveCard = {
  key: string; who: string; tone: "src" | "agent" | "check" | "gate" | "out";
  status: "working" | "done" | "held" | "raised" | "sent" | "failed" | "quiet";
  title: string; question?: string; lines: LiveLine[]; meta?: string;
};
export type Figures = { paid: number; left: number; late: number; attention: number };
export type AgentRun = {
  label: string; status: "running" | "done" | "failed"; summary?: string; error?: string;
  books?: { before: Figures; after: Figures }; emails: number; log: string[];
};

const WHO: Record<string, { who: string; box: string }> = {
  anomaly: { who: "Anomaly agent (Claude)", box: "anomaly" },
  chaser: { who: "Chaser agent (Claude)", box: "chaser" },
  investigator: { who: "Investigator (Claude)", box: "router" },
  agreement: { who: "Agreement agent (Claude)", box: "agreement" },
};
const words = (s: string) => s.replace(/_/g, " ");

async function post<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    method: "POST", headers: { "content-type": "application/json" }, body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((data as { detail?: string }).detail || `${path}: ${r.status}`);
  return data as T;
}

export function useAgentConsole() {
  const [cards, setCards] = useState<LiveCard[]>([]);
  const [lit, setLit] = useState<Set<string>>(new Set());
  const [active, setActive] = useState<Set<string>>(new Set());
  const [run, setRun] = useState<AgentRun | null>(null);
  const [connected, setConnected] = useState(false);
  const runningRef = useRef(false);
  const agentCard = useRef<Record<string, string>>({});       // task id -> card key

  const light = (...ids: string[]) => {
    setLit((old) => new Set([...old, ...ids]));
    setActive(new Set(ids));
  };
  const add = (c: Omit<LiveCard, "key" | "lines"> & { lines?: LiveLine[] }) => {
    const key = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    setCards((cs) => [...cs, { ...c, key, lines: c.lines ?? [] }]);
    return key;
  };
  const patch = (key: string, f: (c: LiveCard) => LiveCard) => setCards((cs) => cs.map((c) => (c.key === key ? f(c) : c)));
  const log = (text: string) => setRun((r) => (r ? { ...r, log: [...r.log, text] } : r));

  useEffect(() => {
    const es = new EventSource(`${API}/pipeline/stream`);
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    const on = (type: string, f: (m: Record<string, any>) => void) =>
      es.addEventListener(type, (e) => { if (runningRef.current) f(JSON.parse((e as MessageEvent).data)); });

    on("reader", (m) => {
      light("inbox", "inbox>reader", "reader");
      add({ who: "Reader", tone: "src", status: m.kinds?.length ? "done" : "quiet",
        title: m.kinds?.length ? `Read ${m.file} as ${m.kinds.map(words).join(", ")}` : `Nothing usable in ${m.file}`,
        lines: [{ kind: "evidence", text: m.invoices?.length ? `Invoices named: ${m.invoices.join(", ")}` : "No invoice these books know" },
          ...(m.flags || []).map((f: string) => ({ kind: "check" as const, text: f }))] });
    });
    on("filed", (m) => {
      light("reader>log", "log");
      add({ who: "Event log", tone: "check", status: m.documents?.length ? "done" : "quiet",
        title: m.documents?.length ? `Filed ${m.documents.length} document(s) in the books` : "Already in the books; nothing new filed",
        meta: m.book, lines: [...(m.documents || []), ...(m.already || [])].map((d: string) => ({ kind: "evidence" as const, text: d.replace(/^raw:inbox\//, "") })) });
      (m.documents || []).forEach((d: string) => log(`Filed ${d.replace(/^raw:inbox\//, "")}`));
    });
    on("checks", (m) => {
      light("log>router", "router");
      const entries = Object.entries(m.findings || {}) as [string, string[]][];
      const flagged = entries.filter(([, codes]) => codes.length);
      add({ who: "Automatic checks", tone: "check", status: flagged.length ? "raised" : "done",
        title: flagged.length ? `${flagged.length} invoice(s) need a look` : "Every line agrees with the books",
        lines: entries.map(([inv, codes]) => ({ kind: codes.length ? "check" as const : "result" as const,
          text: `${inv}: ${codes.length ? codes.map(words).join(", ") : "nothing to flag"}` })) });
      if ((m.notices || []).length) log(`Note to Keyway queued for ${m.notices.join(", ")}`);
    });
    on("task_started", (m) => {
      const w = WHO[m.agent] || { who: m.agent, box: "router" };
      light("router>" + w.box, w.box);
      agentCard.current[m.task_id] = add({ who: w.who, tone: "agent", status: "working",
        title: `${m.invoice_id === "portfolio" ? "All invoices" : m.invoice_id}`, question: m.reason });
    });
    on("thinking", (m) => {
      const k = agentCard.current[m.task_id];
      if (!k) return;
      patch(k, (c) => {
        const last = c.lines[c.lines.length - 1];
        if (last?.kind === "thinking") return { ...c, lines: [...c.lines.slice(0, -1), { ...last, text: last.text + m.text }] };
        return { ...c, lines: [...c.lines, { kind: "thinking", text: m.text }] };
      });
    });
    on("query", (m) => {
      const k = agentCard.current[m.task_id];
      if (!k) return;
      patch(k, (c) => ({ ...c, lines: [...c.lines, { kind: "sql", text: m.error ? `${m.purpose}: refused (${m.error})` : `${m.purpose}: ${m.row_count} rows`,
        rows: m.rows, columns: m.columns }] }));
    });
    on("tool_call", (m) => {
      const k = agentCard.current[m.task_id];
      if (k && m.tool !== "query_books") patch(k, (c) => ({ ...c, lines: [...c.lines, { kind: "check", text: `uses ${words(m.tool)}` }] }));
    });
    on("task_done", (m) => {
      const k = agentCard.current[m.task_id];
      if (!k) return;
      const o = m.outcome || {};
      const verdict = o.verdict ? words(o.verdict) : words(o.decision || m.status);
      const raised = o.verdict === "needs_review" || o.decision === "escalate";
      patch(k, (c) => ({ ...c, status: m.status === "failed" ? "failed" : raised ? "raised" : "done",
        title: `${c.title} · ${verdict}`, lines: [...c.lines, { kind: "result", text: o.summary || o.rationale || o.error || verdict }] }));
      const box = WHO[m.agent]?.box || "router";
      if (raised) light(box + ">verifier", "verifier", "verifier>owner", "owner");
      log(`${WHO[m.agent]?.who || m.agent} on ${m.invoice_id}: ${verdict}`);
    });
    on("flag_raised", (m) => {
      light("gate>controller", "controller");
      add({ who: "Controller queue", tone: "out", status: "raised", title: `Flag on ${m.invoice_id}: ${words(m.kind)}`,
        lines: [{ kind: "result", text: m.summary || "" }] });
    });
    on("email_sent", (m) => {
      light("gate", "gate>email", "email");
      add({ who: "Keyway Management email", tone: "out", status: m.simulated ? "quiet" : "sent",
        title: `${m.simulated ? "Recorded (sending is off)" : "Sent"}: ${m.subject || m.kind}`,
        lines: [{ kind: "result", text: `${words(m.kind || "")} for ${m.invoice_id || "this case"}` }] });
      log(`Email ${m.simulated ? "recorded" : "sent"}: ${m.subject}`);
      setRun((r) => (r ? { ...r, emails: r.emails + 1 } : r));
    });
    on("email_failed", (m) => {
      light("gate", "gate>email", "email");
      add({ who: "Keyway Management email", tone: "out", status: "failed", title: `Not sent: ${m.error}`, lines: [] });
    });
    on("console_done", (m) => {
      runningRef.current = false;
      setActive(new Set());
      setRun((r) => (r ? { ...r, status: m.ok === false ? "failed" : "done", error: m.error, books: m.books,
        summary: m.ok === false ? m.error : summarize(m) } : r));
    });
    return () => es.close();
  }, []);

  const start = (label: string) => {
    runningRef.current = true;
    agentCard.current = {};
    setCards([]); setLit(new Set()); setActive(new Set());
    setRun({ label, status: "running", emails: 0, log: [] });
  };
  const fail = (e: unknown) => {
    runningRef.current = false;
    setRun((r) => ({ ...(r ?? { label: "", emails: 0, log: [] }), status: "failed", error: String((e as Error)?.message || e) }));
  };

  const sendSample = useCallback(async (file: string, label: string) => {
    start(label);
    try { await post(`/pipeline/console/samples/${encodeURIComponent(file)}`); } catch (e) { fail(e); }
  }, []);
  const sendUpload = useCallback(async (name: string, data: string) => {
    start(`Your file: ${name}`);
    try { await post("/pipeline/console/upload", { name, data }); } catch (e) { fail(e); }
  }, []);
  const resetBooks = useCallback(async () => {
    await post("/pipeline/demo/reset?run_first_day=false");
    setCards([]); setLit(new Set()); setActive(new Set()); setRun(null);
  }, []);

  return { cards, lit, active, run, connected, busy: run?.status === "running", sendSample, sendUpload, resetBooks };
}

function summarize(m: Record<string, any>): string {
  const flagged = Object.values(m.findings || {}).filter((c) => (c as string[]).length).length;
  const parts = [`${m.label}: ${m.filed?.length ? `filed ${m.filed.length} document(s)` : "nothing new filed"}`];
  if (flagged) parts.push(`${flagged} invoice(s) flagged`);
  if (m.decisions?.length) parts.push(`${m.decisions.length} agent decision(s)`);
  parts.push(m.emails ? `${m.emails} email(s) to Keyway` : "no email");
  return parts.join(" · ");
}
