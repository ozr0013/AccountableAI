"use client";

// The agentic workflow, simply: agreement agent → chaser agent → anomaly detector, one after
// another. Each step calls the live feed (dashboard/feed/loop.py), which runs the real agents;
// their reasoning arrives on /api/stream as they decide and is shown under the agent doing it.

import Link from "next/link";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useFeed } from "@/components/Feed";
import { Loading, PageTop } from "@/components/ui";
import { day, money } from "@/lib/format";
import type { Decision } from "@/lib/types";

const FEED_URL = process.env.NEXT_PUBLIC_FEED_URL || "http://127.0.0.1:8765";
const INVOICE = "INV-KW-0301";

type Key = "agreement" | "chaser" | "anomaly";
type Status = "waiting" | "running" | "done";
type Line = { who: string; kind: string; text: string };

const AGENTS: { key: Key; n: string; name: string; when: string; listens: (d: { agent: string; invoice_id: string | null }) => boolean }[] = [
  { key: "agreement", n: "1", name: "Agreement agent", when: "An agreement arrives in the owner's Gmail",
    listens: d => d.agent === "agreement_check" },
  { key: "chaser", n: "2", name: "Chaser agent", when: "The due date passes. Was the vendor paid?",
    listens: d => d.agent === "chaser" || (["trail", "cause"].includes(d.agent) && d.invoice_id === INVOICE) },
  { key: "anomaly", n: "3", name: "Anomaly detector", when: "The middleman sends a financial statement",
    listens: d => d.agent === "anomaly" || (d.agent === "verifier" && d.invoice_id === INVOICE) },
];
const MAIN = new Set(["agreement_check", "chaser", "anomaly"]);   // supporting checks show their conclusion only
const WHO: Record<string, string> = {
  agreement_check: "Agreement agent", chaser: "Chaser agent", trail: "Payment-trail check",
  cause: "Delay-cause check", anomaly: "Anomaly detector", verifier: "Verifier",
};

type AgreementOut = { items: { file: string; complete: boolean;
  stored: { vendor: string; amount: string; due: string } | null; owner_notice: { subject: string } | null }[] };
type Reminder = { level: string; sent: string | null; body: string };
type Email = { kind: string; to: string; subject: string; status: "sent" | "failed"; error?: string };
type ChaserOut = { invoice: string; vendor: string; amount: number; due: string | null; days_late: number;
  earlier: Reminder[]; new: Reminder[]; escalated: boolean; emails: Email[] };
type AnomalyOut = { invoice: string; approved: number; stated: number; caught: boolean;
  middleman: { body: string } | null; emails: Email[] };

function mailed(emails: Email[], kind: string): string | null {
  const sent = emails.filter(e => e.kind === kind && e.status === "sent");
  return sent.length ? `Emailed ${sent[0].to}${sent.length > 1 ? ` (${sent.length} emails)` : ""}.` : null;
}

const wait = (ms: number) => new Promise<void>(r => window.setTimeout(r, ms));
// Straight to the feed, not through the Next.js dev proxy: with Claude a step can take minutes,
// longer than the proxy waits.
async function call<T>(name: string): Promise<T> {
  const r = await fetch(`${FEED_URL}/api/loop/${name}`, { method: "POST", cache: "no-store" });
  if (!r.ok) throw new Error(`${name}: ${r.status}`);
  return r.json();
}

function Result({ tone, children }: { tone: "good" | "warn"; children: React.ReactNode }) {
  return <div className={`flow-result ${tone}`}>{children}</div>;
}

function AgreementResult({ out }: { out: AgreementOut }) {
  return <>{out.items.map(i => i.complete && i.stored
    ? <Result key={i.file} tone="good"><b>Complete → stored.</b> {i.stored.vendor} · {i.stored.amount} · {i.stored.due}</Result>
    : <Result key={i.file} tone="warn"><b>Missing info → owner notified.</b> {i.owner_notice?.subject.replace("Action needed: ", "")}</Result>)}</>;
}

function ChaserResult({ out }: { out: ChaserOut }) {
  const ladder = [...out.earlier, ...out.new].map(r => r.level).join(" → ");
  return <>
    <Result tone="warn"><b>{out.invoice} not paid.</b> {out.vendor} · {money(out.amount)} · due {out.due ? day(out.due) : "?"}, {out.days_late} days late, no bank or vendor proof.</Result>
    <Result tone="warn"><b>Reminders: {ladder}.</b> {out.escalated ? "Owner notified; middleman told the owner knows." : "Next reminder on schedule."} {mailed(out.emails, "reminder")}</Result>
  </>;
}

function AnomalyResult({ out }: { out: AnomalyOut }) {
  return out.caught
    ? <>
        <Result tone="warn"><b>Mismatch caught.</b> Owner approved {money(out.approved)}, statement shows {money(out.stated)}. Owner alerted.</Result>
        {out.middleman && <Result tone="warn"><b>Note to the middleman.</b> {mailed(out.emails, "statement_notice") ?? "Not emailed (email not connected)."} {out.middleman.body}</Result>}
      </>
    : <Result tone="good"><b>Statement matches</b> what the owner approved.</Result>;
}

export function AgentWorkflowView({ head }: { head?: (run: ReactNode, mode: string) => ReactNode }) {
  const { state, mode, refresh } = useFeed();
  const [status, setStatus] = useState<Record<Key, Status>>({ agreement: "waiting", chaser: "waiting", anomaly: "waiting" });
  const [lines, setLines] = useState<Record<Key, Line[]>>({ agreement: [], chaser: [], anomaly: [] });
  const [out, setOut] = useState<{ agreement?: AgreementOut; chaser?: ChaserOut; anomaly?: AnomalyOut }>({});
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = useRef<Key | null>(null);
  const queue = useRef<{ key: Key; line: Line }[]>([]);
  const live = mode === "live";

  // Reasoning as it happens: every decision on the stream goes to the agent card that is running.
  useEffect(() => {
    if (!live || typeof EventSource === "undefined") return;
    const es = new EventSource(`${FEED_URL}/api/stream`);
    const take = (d: { agent: string; invoice_id: string | null }) => {
      const key = active.current;
      return key && AGENTS.find(a => a.key === key)!.listens(d) ? key : null;
    };
    const asked = new Set<string>();
    const ask = (key: Key, agent: string, text: string) => {
      const id = `${key}|${agent}|${text}`;
      if (asked.has(id)) return;
      asked.add(id);
      queue.current.push({ key, line: { who: WHO[agent] ?? agent, kind: "question", text } });
    };
    es.addEventListener("action", e => {
      if (JSON.parse((e as MessageEvent).data).status === "started") asked.clear();
    });
    es.addEventListener("agent_started", e => {
      const m = JSON.parse((e as MessageEvent).data);
      const key = take(m);
      if (key) ask(key, m.agent, m.question);
    });
    es.addEventListener("thinking", e => {
      const m = JSON.parse((e as MessageEvent).data);
      const key = take(m);
      if (key) queue.current.push({ key, line: { who: WHO[m.agent] ?? m.agent, kind: "thinking", text: m.text } });
    });
    es.addEventListener("email", e => {
      const m = JSON.parse((e as MessageEvent).data) as Email;
      const key = active.current;
      if (key) queue.current.push({ key, line: { who: "Email", kind: m.status === "sent" ? "email" : "guard",
        text: m.status === "sent" ? `Emailed ${m.to}: ${m.subject}` : `Email to ${m.to} failed: ${m.error ?? ""}` } });
    });
    es.addEventListener("decision", e => {
      const d = JSON.parse((e as MessageEvent).data) as Decision;
      const key = take(d);
      if (!key) return;
      const who = WHO[d.agent] ?? d.agent;
      ask(key, d.agent, d.question);
      const steps = MAIN.has(d.agent) ? d.steps : d.steps.filter(s => s.kind === "result");
      for (const s of steps) if (s.kind !== "thinking") queue.current.push({ key, line: { who, kind: s.kind, text: s.text } });
    });
    // Reveal one line at a time so a person can follow it; rules-mode agents decide in milliseconds.
    const t = window.setInterval(() => {
      const next = queue.current.shift();
      if (next) setLines(l => ({ ...l, [next.key]: [...l[next.key], next.line] }));
    }, 170);
    return () => { es.close(); window.clearInterval(t); };
  }, [live]);

  if (!state) return <Loading />;

  const drained = async () => { while (queue.current.length) await wait(120); await wait(300); };
  const runAgent = async <T,>(key: Key): Promise<T> => {
    active.current = key;
    setStatus(s => ({ ...s, [key]: "running" }));
    const res = await call<T>(key);
    await wait(250);                       // the last stream messages land just after the response
    await drained();
    setOut(o => ({ ...o, [key]: res }));
    setStatus(s => ({ ...s, [key]: "done" }));
    return res;
  };

  const run = async () => {
    setRunning(true); setError(null); setOut({});
    setLines({ agreement: [], chaser: [], anomaly: [] });
    setStatus({ agreement: "waiting", chaser: "waiting", anomaly: "waiting" });
    queue.current = [];
    try {
      await call("start");
      await runAgent<AgreementOut>("agreement");
      await wait(600);
      await runAgent<ChaserOut>("chaser");
      await wait(600);
      await runAgent<AnomalyOut>("anomaly");
      active.current = null;
      await refresh();
    } catch (e) {
      setError(`${e}. Is the live feed running? python dashboard/feed/serve.py`);
    }
    setRunning(false);
  };

  const modeLabel = state.meta.agents === "live" ? "Claude agents" : "Rules agents · $0";
  const runButton = <button className="btn" onClick={run} disabled={running || !live}>{running ? "Running…" : out.anomaly ? "Run again" : "Run the loop"}</button>;
  return <>
    {head ? head(runButton, modeLabel) : <>
      <PageTop title="Agent workflow" sub="Owner pays the middleman, the middleman pays the vendor. Three agents check every step.">
        <span className="chip">{modeLabel}</span>
      </PageTop>
      <div className="flow-bar">
        {runButton}
        <span className="muted">Watch each agent think, then decide.</span>
        <Link href="/investigation" className="flow-link">Detailed investigation →</Link>
      </div>
    </>}
    {!live && <div className="notice">This needs the live feed: <span className="mono">python dashboard/feed/serve.py</span></div>}
    {error && <div className="notice err">{error}</div>}

    <div className="flow">
      {AGENTS.map((a, i) => <div key={a.key} className="flow-col">
        <section className={`flow-card ${status[a.key]}`}>
          <header>
            <span className="flow-n">{status[a.key] === "done" ? "✓" : a.n}</span>
            <div><h3>{a.name}</h3><p>{a.when}</p></div>
          </header>
          <div className="flow-think" aria-live="polite">
            {lines[a.key].length === 0 && <p className="flow-idle">{status[a.key] === "running" ? "Starting…" : status[a.key] === "waiting" ? "Waiting" : ""}</p>}
            {lines[a.key].map((l, k) => <p key={k} className={`flow-line ${l.kind}`}>
              {l.kind === "question" ? <><b>{l.who}</b> {l.text}</> : l.text}</p>)}
            {status[a.key] === "running" && <span className="flow-caret" />}
          </div>
          {a.key === "agreement" && out.agreement && <AgreementResult out={out.agreement} />}
          {a.key === "chaser" && out.chaser && <ChaserResult out={out.chaser} />}
          {a.key === "anomaly" && out.anomaly && <AnomalyResult out={out.anomaly} />}
        </section>
        {i < AGENTS.length - 1 && <span className={`flow-arrow ${status[a.key] === "done" ? "on" : ""}`}>→</span>}
      </div>)}
    </div>
    <p className="micro muted flow-foot">Reminders and the statement note are emailed to the middleman from the owner&apos;s Gmail, using approved templates only. Nothing moves money.</p>
  </>;
}
