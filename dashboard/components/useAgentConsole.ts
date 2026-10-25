"use client";

// Judge Console on the real agents: send a file to the agent API (/pipeline/console/*) and build
// the console's cards, map and log from the agents' live stream (/pipeline/stream) as each step
// actually happens. Nothing is scripted: a card exists only because that event was emitted.

import { useCallback, useEffect, useRef, useState } from "react";
import { AGENTS_URL } from "@/lib/agents-url";

const API = AGENTS_URL;

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
  day?: string; usage?: { calls: number; cost: number };
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
  const cardsRef = useRef<LiveCard[]>([]);
  cardsRef.current = cards;
  const [lit, setLit] = useState<Set<string>>(new Set());
  const [active, setActive] = useState<Set<string>>(new Set());
  const [run, setRun] = useState<AgentRun | null>(null);
  const [connected, setConnected] = useState(false);
  const [doing, setDoing] = useState<string | null>(null);   // what is happening right now, in words
  const runningRef = useRef(false);
  const doneWaiter = useRef<(() => void) | null>(null);
  const dayCard = useRef<string | null>(null);
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
  const agentName = (taskId: string) => cardsRef.current.find((c) => c.key === agentCard.current[taskId])?.who ?? "The agent";
  const say = (text: string | null) => setDoing((d) => (d === text ? d : text));
  const log = (text: string) => setRun((r) => (r ? { ...r, log: [...r.log, text] } : r));

  useEffect(() => {
    const es = new EventSource(`${API}/pipeline/stream`);
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    const on = (type: string, f: (m: Record<string, any>) => void) =>
      es.addEventListener(type, (e) => { if (runningRef.current) f(JSON.parse((e as MessageEvent).data)); });

    on("fact", (m) => {
      say("Your fact is filed. The investigator will check it against the books.");
      light("you", "you>log", "log");
      add({ who: "Your fact", tone: "src", status: "done", title: `\u201c${m.text}\u201d`,
        lines: [{ kind: "check", text: "A fact is a claim, not evidence: the investigator verifies it against the books" }] });
    });
    on("day_started", (m) => {
      say(`${fmtDay(m.day)} begins: new paperwork arrives and the agents wake on their own.`);
      light("clock", "clock>log", "log");
      dayCard.current = add({ who: "Clock", tone: "src", status: "done", title: `${fmtDay(m.day)} begins: the agents wake on their own`, lines: [] });
      setRun((r) => (r ? { ...r, day: m.day } : r));
    });
    on("evidence", (m) => {
      const k = dayCard.current;
      if (k) patch(k, (c) => ({ ...c, lines: [...c.lines, { kind: "evidence", text: `${words(m.kind)} arrived: ${String(m.text || m.document).slice(0, 110)}` }] }));
      light("inbox", "log");
    });
    on("matched", (m) => {
      add({ who: "Payment matching", tone: "check", status: "done",
        title: `The books now show a payment for ${m.invoices.join(", ")}`,
        lines: Object.entries(m.paid || {}).map(([inv, [a, b]]: any) => ({ kind: "evidence" as const, text: `${inv}: paid $${a} \u2192 $${b}` })) });
    });
    on("reader", (m) => {
      say(`The reader is reading ${m.file}.`);
      light("inbox", "inbox>reader", "reader");
      add({ who: "Reader", tone: "src", status: m.kinds?.length ? "done" : "quiet",
        title: m.kinds?.length ? `Read ${m.file} as ${m.kinds.map(words).join(", ")}` : `Nothing usable in ${m.file}`,
        lines: [{ kind: "evidence", text: m.invoices?.length ? `Invoices named: ${m.invoices.join(", ")}` : "No invoice these books know" },
          ...(m.flags || []).map((f: string) => ({ kind: "check" as const, text: f }))] });
    });
    on("filed", (m) => {
      say("Filing what it read in the event log.");
      light("reader>log", "log");
      add({ who: "Event log", tone: "check", status: m.documents?.length ? "done" : "quiet",
        title: m.documents?.length ? `Filed ${m.documents.length} document(s) in the books` : "Already in the books; nothing new filed",
        meta: m.book, lines: [...(m.documents || []), ...(m.already || [])].map((d: string) => ({ kind: "evidence" as const, text: d.replace(/^raw:inbox\//, "") })) });
      (m.documents || []).forEach((d: string) => log(`Filed ${d.replace(/^raw:inbox\//, "")}`));
    });
    on("checks", (m) => {
      say("Running the automatic checks on every invoice it touches.");
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
      say(`${w.who} woke up for ${m.invoice_id === "portfolio" ? "all invoices" : m.invoice_id}${m.reason ? `: ${m.reason}` : ""}`);
      agentCard.current[m.task_id] = add({ who: w.who, tone: "agent", status: "working",
        title: `${m.invoice_id === "portfolio" ? "All invoices" : m.invoice_id}`, question: m.reason });
    });
    on("thinking", (m) => {
      const k = agentCard.current[m.task_id];
      if (!k) return;
      say(`${agentName(m.task_id)} is reasoning about the evidence.`);
      patch(k, (c) => {
        const last = c.lines[c.lines.length - 1];
        if (last?.kind === "thinking") return { ...c, lines: [...c.lines.slice(0, -1), { ...last, text: last.text + m.text }] };
        return { ...c, lines: [...c.lines, { kind: "thinking", text: m.text }] };
      });
    });
    on("query", (m) => {
      const k = agentCard.current[m.task_id];
      if (!k) return;
      say(`${agentName(m.task_id)} is querying the books: ${m.purpose}`);
      patch(k, (c) => ({ ...c, lines: [...c.lines, { kind: "sql", text: m.error ? `${m.purpose}: refused (${m.error})` : `${m.purpose}: ${m.row_count} rows`,
        rows: m.rows, columns: m.columns }] }));
    });
    on("tool_call", (m) => {
      const k = agentCard.current[m.task_id];
      if (k && m.tool !== "query_books") say(`${agentName(m.task_id)} is using ${words(m.tool)}.`);
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
      say(`${WHO[m.agent]?.who || m.agent} finished ${m.invoice_id}: ${verdict}.`);
      if (raised) light(box + ">verifier", "verifier", "verifier>owner", "owner");
      log(`${WHO[m.agent]?.who || m.agent} on ${m.invoice_id}: ${verdict}`);
    });
    on("flag_raised", (m) => {
      say(`A flag on ${m.invoice_id} is waiting for the controller.`);
      light("gate>controller", "controller");
      add({ who: "Controller queue", tone: "out", status: "raised", title: `Flag on ${m.invoice_id}: ${words(m.kind)}`,
        lines: [{ kind: "result", text: m.summary || "" }] });
    });
    on("email_sent", (m) => {
      say("A message to Keyway Management passed the policy gate.");
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
      say(null);
      setActive(new Set());
      setRun((r) => (r ? { ...r, status: m.ok === false ? "failed" : "done", error: m.error, books: m.books,
        usage: m.usage, day: m.day ?? r.day, summary: m.ok === false ? m.error : summarize(m) } : r));
      doneWaiter.current?.();
      doneWaiter.current = null;
    });
    return () => es.close();
  }, []);

  const start = (label: string) => {
    runningRef.current = true;
    agentCard.current = {};
    setCards([]); setLit(new Set()); setActive(new Set());
    setRun({ label, status: "running", emails: 0, log: [] });
    say("Sent to the agents. Waiting for the first one to wake up.");
  };
  const fail = (e: unknown) => {
    runningRef.current = false;
    say(null);
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
  const waitDone = () => new Promise<void>((res) => { doneWaiter.current = res; });
  const sendEmail = useCallback(async (sender: string, subject: string, body: string, label: string) => {
    start(label);
    try { await post("/pipeline/console/email", { sender, subject, body }); } catch (e) { fail(e); }
  }, []);
  const tellFact = useCallback(async (text: string) => {
    start(`Fact: ${text}`);
    try { await post("/pipeline/console/fact", { text }); } catch (e) { fail(e); }
  }, []);
  const advance = useCallback(async (days: number) => {
    start(`+${days} day${days > 1 ? "s" : ""}`);
    try { const w = waitDone(); await post("/pipeline/console/advance", { days }); await w; } catch (e) { fail(e); }
  }, []);
  const stopRef = useRef(false);
  /** Play day by day (one request per day) so it can be stopped between days. */
  const play = useCallback(async (days: number, onDay?: (i: number) => void) => {
    stopRef.current = false;
    for (let i = 0; i < days && !stopRef.current; i++) {
      onDay?.(i + 1);
      start(`Day ${i + 1} of ${days}`);
      try { const w = waitDone(); await post("/pipeline/console/advance", { days: 1 }); await w; } catch (e) { fail(e); break; }
    }
  }, []);
  const stop = useCallback(() => { stopRef.current = true; }, []);
  const resetBooks = useCallback(async () => {
    await post("/pipeline/demo/reset?run_first_day=false");
    setCards([]); setLit(new Set()); setActive(new Set()); setRun(null);
  }, []);

  return { cards, lit, active, run, connected, doing, busy: run?.status === "running", sendSample, sendUpload, resetBooks,
    sendEmail, tellFact, advance, play, stop };
}

function fmtDay(d?: string): string {
  return d ? new Date(d + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "";
}

function summarize(m: Record<string, any>): string {
  const flagged = Object.values(m.findings || {}).filter((c) => (c as string[]).length).length;
  const parts = [m.kind === "time" || m.kind === "fact" ? `${m.label}` : `${m.label}: ${m.filed?.length ? `filed ${m.filed.length} document(s)` : "nothing new filed"}`];
  if (flagged) parts.push(`${flagged} invoice(s) flagged`);
  if (m.decisions?.length) parts.push(`${m.decisions.length} agent decision(s)`);
  parts.push(m.emails ? `${m.emails} email(s) to Keyway` : "no email");
  if (m.usage) parts.push(m.usage.calls ? `Claude: ${m.usage.calls} call(s), $${m.usage.cost.toFixed(3)}` : "no Claude call needed");
  if (m.note) parts.push(m.note);
  return parts.join(" · ");
}
