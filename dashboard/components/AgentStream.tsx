"use client";

// Live agent reasoning: subscribes to the feed's /api/stream (Server-Sent Events) and renders
// each DecisionRecord as it is made, with its steps and, in Claude mode, Claude's summarized
// thinking. Connects to the feed directly: the Next.js dev proxy can buffer an event stream.

import { useEffect, useState } from "react";
import { Cites } from "@/components/ui";
import type { Decision, StreamAction } from "@/lib/types";

const FEED_URL = process.env.NEXT_PUBLIC_FEED_URL || "http://127.0.0.1:8765";
const AGENT_NAMES: Record<string, string> = {
  agreement: "Agreement agent", trail: "Payment-trail agent", cause: "Delay-cause agent",
  chaser: "Chaser agent", case: "Case agent", verifier: "Verifier", workflow: "Workflow",
};

export function agentName(agent: string) { return AGENT_NAMES[agent] ?? agent; }

export function useAgentStream(enabled: boolean, keep = 60) {
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [action, setAction] = useState<StreamAction | null>(null);
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    if (!enabled || typeof EventSource === "undefined") return;
    const es = new EventSource(`${FEED_URL}/api/stream`);
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.addEventListener("decision", (e) => {
      const d = JSON.parse((e as MessageEvent).data) as Decision;
      setDecisions(prev => [...prev, d].slice(-keep));
    });
    es.addEventListener("action", (e) => {
      const a = JSON.parse((e as MessageEvent).data) as StreamAction;
      setAction(a);
      if (a.status === "started") setDecisions([]);
    });
    return () => es.close();
  }, [enabled, keep]);
  return { decisions, action, connected };
}

function modeChip(mode: Decision["mode"]) {
  if (mode === "llm") return <span className="tag">Claude</span>;
  if (mode === "fallback") return <span className="tag warn">Claude rejected · rules</span>;
  return <span className="tag neutral">Rules</span>;
}

export function DecisionCard({ d, open: initiallyOpen = false }: { d: Decision; open?: boolean }) {
  const [open, setOpen] = useState(initiallyOpen);
  const result = [...d.steps].reverse().find(s => s.kind === "result");
  const thinking = d.steps.find(s => s.kind === "thinking");
  return (
    <article className={`decision-card ${d.agent}`}>
      <button className="decision-head" onClick={() => setOpen(o => !o)} aria-expanded={open}>
        <span className="decision-agent">{agentName(d.agent)}</span>
        {d.invoice_id && <span className="micro">{d.invoice_id}</span>}
        {modeChip(d.mode)}
        <span className="decision-ms micro">{d.model ? `${d.model} · ` : ""}{d.ms} ms{d.tokens_in ? ` · ${d.tokens_in}+${d.tokens_out} tok` : ""}</span>
      </button>
      <p className="decision-q">{d.question}</p>
      {result && <p className="decision-result">{result.text}</p>}
      {thinking && !open && <p className="decision-thinking-peek">Claude’s summarized thinking: {thinking.text.slice(0, 180)}{thinking.text.length > 180 ? "…" : ""}</p>}
      {open && <ol className="decision-steps">
        {d.steps.map((s, i) => <li key={i} className={`step-${s.kind}`}>
          <span className="step-kind">{s.kind === "thinking" ? "Claude’s thinking (summary)" : s.kind}</span>
          <span className="step-text">{s.text}{s.citations.length > 0 && <> <Cites cites={s.citations} /></>}</span>
        </li>)}
      </ol>}
    </article>
  );
}

export function LiveReasoning({ enabled }: { enabled: boolean }) {
  const { decisions, action, connected } = useAgentStream(enabled);
  return (
    <section className="live-reasoning">
      <div className="simulation-heading">
        <div><span className="label">/ live agent reasoning</span><h2>What each agent is deciding, and why.</h2></div>
        <span className={`tag ${connected ? "" : "neutral"}`}>{connected ? "● streaming" : "stream offline"}</span>
      </div>
      {action && <p className="live-action">{action.status === "started" ? "Working on: " : "Done: "}{action.summary}
        {action.status === "finished" && action.unexplained !== undefined ? ` · unexplained now $${action.unexplained.toLocaleString(undefined, { minimumFractionDigits: 2 })}` : ""}</p>}
      {decisions.length === 0
        ? <p className="micro">{enabled ? "Waiting for the next action. Inject a claim or play the loop." : "Start the live feed to watch the agents."}</p>
        : <div className="decision-list">{decisions.map(d => <DecisionCard key={`${d.trigger}-${d.seq}-${d.id}`} d={d} />)}</div>}
    </section>
  );
}
