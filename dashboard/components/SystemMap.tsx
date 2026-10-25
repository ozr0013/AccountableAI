"use client";

// How the agentic system runs, live: what woke each agent, what it handed to whom, and where the
// result went. Every node and edge lights up from the same event stream the agents emit
// (/pipeline/stream), so the map shows the real cause of each step, not a drawing of the idea.

import { useEffect, useRef, useState } from "react";
import s from "./SystemMap.module.css";

const API = process.env.NEXT_PUBLIC_AGENTS_URL || "http://127.0.0.1:8773";
const W = 1100, H = 430, NW = 200, NH = 54;

type Node = { id: string; x: number; y: number; title: string; sub: string; kind: "trigger" | "agent" | "data" | "out" | "human" };
const NODES: Node[] = [
  { id: "day", x: 10, y: 24, title: "New day", sub: "the schedule wakes the agents", kind: "trigger" },
  { id: "paper", x: 10, y: 112, title: "Paperwork arrives", sub: "invoices, wires, bank exports", kind: "trigger" },
  { id: "stmt", x: 10, y: 200, title: "Keyway statement", sub: "month-end claims", kind: "trigger" },
  { id: "upload", x: 10, y: 288, title: "Document uploaded", sub: "anyone drops in a file", kind: "trigger" },
  { id: "agreement", x: 450, y: 0, title: "Agreement agent", sub: "deadlines and terms → rules", kind: "agent" },
  { id: "investigator", x: 450, y: 84, title: "Investigator", sub: "sweeps every invoice + bank row", kind: "agent" },
  { id: "chaser", x: 450, y: 168, title: "Chaser agent", sub: "one invoice: chase, wait, hand off", kind: "agent" },
  { id: "anomaly", x: 450, y: 252, title: "Anomaly agent", sub: "reviews payments and statements", kind: "agent" },
  { id: "books", x: 450, y: 360, title: "Books · PostgreSQL", sub: "read-only SQL by the agents", kind: "data" },
  { id: "email", x: 860, y: 24, title: "Email to Keyway", sub: "approved templates only", kind: "out" },
  { id: "oakline", x: 860, y: 112, title: "Raised to Oakline", sub: "after 3 emails, no proof", kind: "out" },
  { id: "controller", x: 860, y: 200, title: "Controller", sub: "flags, holds, verdicts to decide", kind: "human" },
  { id: "verified", x: 860, y: 288, title: "Closed by code", sub: "paid in full, on time", kind: "out" },
];
const BY = Object.fromEntries(NODES.map(n => [n.id, n]));
const EDGES: [string, string, string][] = [
  ["day", "investigator", "weekdays, first"], ["day", "chaser", "every open invoice"], ["paper", "books", "filed"],
  ["stmt", "anomaly", "flagged lines"], ["upload", "investigator", ""], ["upload", "chaser", ""],
  ["agreement", "chaser", "rules"], ["investigator", "chaser", "note"], ["chaser", "anomaly", "payment seen"],
  ["investigator", "books", ""], ["chaser", "books", ""], ["anomaly", "books", ""],
  ["chaser", "email", "reminder"], ["anomaly", "email", "statement note"], ["chaser", "oakline", "escalate"],
  ["investigator", "controller", "flag"], ["chaser", "controller", "flag / hold"], ["anomaly", "controller", "verdict"],
  ["chaser", "verified", "verified"],
];
const key = (a: string, b: string) => `${a}>${b}`;

function path(a: Node, b: Node): string {
  if (b.id === "books") {                                     // agents reach down into the books
    const x1 = a.x + NW / 2 + (a.id === "investigator" ? -60 : a.id === "anomaly" ? 60 : 0);
    return `M${a.x === b.x ? x1 : a.x + NW} ${a.y + NH} C ${x1} ${b.y - 30}, ${b.x + NW / 2} ${b.y - 30}, ${b.x + NW / 2} ${b.y}`;
  }
  if (a.x === b.x) {                                          // agent to agent: curve on the left
    return `M${a.x} ${a.y + NH / 2} C ${a.x - 50} ${a.y + NH / 2}, ${b.x - 50} ${b.y + NH / 2}, ${b.x} ${b.y + NH / 2}`;
  }
  const x1 = a.x + NW, y1 = a.y + NH / 2, x2 = b.x, y2 = b.y + NH / 2, m = (x1 + x2) / 2;
  return `M${x1} ${y1} C ${m} ${y1}, ${m} ${y2}, ${x2} ${y2}`;
}

type Why = { at: string; text: string };
const day = (d?: unknown) => (d ? new Date(String(d) + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "");

export function SystemMap() {
  const [hot, setHot] = useState<Record<string, number>>({});
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [why, setWhy] = useState<Why[]>([]);
  const [live, setLive] = useState(false);
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  useEffect(() => {
    const es = new EventSource(`${API}/pipeline/stream`);
    const light = (...ids: string[]) => {
      setHot(h => ({ ...h, ...Object.fromEntries(ids.map(i => [i, Date.now()])) }));
      ids.forEach(i => {
        clearTimeout(timers.current[i]);
        timers.current[i] = setTimeout(() => setHot(h => { const n = { ...h }; delete n[i]; return n; }), 2600);
      });
    };
    const bump = (id: string) => setCounts(c => ({ ...c, [id]: (c[id] || 0) + 1 }));
    const say = (text: string) => setWhy(w => [{ at: new Date().toLocaleTimeString("en-US", { hour12: false }), text }, ...w].slice(0, 7));
    const on = (type: string, f: (m: Record<string, unknown>) => void) =>
      es.addEventListener(type, e => f(JSON.parse((e as MessageEvent).data)));
    es.onopen = () => setLive(true);
    es.onerror = () => setLive(false);

    on("day_started", m => { light("day"); say(`${day(m.day)} begins: the schedule wakes the agents`); });
    on("evidence", m => {
      const statement = m.kind === "manager_statement";
      light(statement ? "stmt" : "paper", key("paper", "books"), "books");
      bump(statement ? "stmt" : "paper");
      if (statement) say(`Keyway's statement arrived (${m.document}); flagged lines go to the anomaly agent`);
    });
    on("task_started", m => {
      const agent = String(m.agent), by = String(m.created_by || "");
      const from = by === "daily_schedule" ? "day" : by === "investigator" ? "investigator"
        : by === "chaser_agent" ? "chaser" : by === "statement_arrived" ? "stmt" : by === "upload" ? "upload" : "";
      light(agent, ...(from ? [from, key(from, agent)] : []));
      bump(agent);
      const target = m.invoice_id === "portfolio" ? "all invoices" : String(m.invoice_id || "");
      const causes: Record<string, string> = { day: "New day", investigator: "Investigator's note",
        chaser: "Chaser hand-off", stmt: "Statement", upload: "Upload" };
      const cause = causes[from] || "Trigger";
      say(`${cause} → ${agent} on ${target}: ${String(m.reason || "").slice(0, 110)}`);
    });
    on("query", m => {
      const a = String(m.agent || "");
      if (a && BY[a]) { light(a, "books", key(a, "books")); bump("books"); }
    });
    on("decision", m => {
      const a = String(m.agent || ""), tool = String(m.tool || "");
      if (tool === "escalate") { light(key(a, "oakline"), "oakline"); bump("oakline"); }
      if (tool === "handoff_to_anomaly") light(key("chaser", "anomaly"));
      if (tool === "hand_to_chaser") light(key("investigator", "chaser"));
    });
    on("flag_raised", m => {
      const a = String(m.agent || "chaser");
      light(key(a, "controller"), "controller"); bump("controller");
      say(`${a} flagged ${m.invoice_id || ""} to the controller: ${String(m.summary || "").slice(0, 100)}`);
    });
    on("email_sent", m => {
      const k = String(m.kind);
      const edge = k === "escalation" ? key("chaser", "oakline") : k === "statement_notice" ? key("anomaly", "email") : key("chaser", "email");
      const node = k === "escalation" ? "oakline" : "email";
      light(edge, node); bump(node);
      say(`Email sent${m.simulated ? " (simulated)" : ""}: ${m.subject || k}`);
    });
    on("task_done", m => {
      const o = (m.outcome || {}) as Record<string, unknown>;
      if (o.decision === "verified_paid") { light(key("chaser", "verified"), "verified"); bump("verified"); }
      if (o.verdict === "needs_review") { light(key("anomaly", "controller"), "controller"); bump("controller"); }
    });
    return () => { es.close(); Object.values(timers.current).forEach(clearTimeout); };
  }, []);

  return (
    <section className={`card ${s.wrap}`}>
      <div className={s.head}>
        <span className="micro">How it runs</span>
        <span className={s.note}>Every box lights up when the system actually does that step</span>
        <span className={`${s.dot} ${live ? s.on : ""}`} /><span className="micro">{live ? "Live" : "Connecting"}</span>
      </div>
      <div className={s.cols}><span>Triggers</span><span>Agents</span><span>Results and people</span></div>
      <svg viewBox={`0 0 ${W} ${H}`} className={s.svg} role="img" aria-label="System map of triggers, agents and results">
        {EDGES.map(([a, b, label]) => {
          const on = key(a, b) in hot, d = path(BY[a], BY[b]);
          return <g key={key(a, b)} className={`${s.edge} ${on ? s.edgeOn : ""} ${a === "agreement" ? s.static : ""}`}>
            <path d={d} />
            {label && on && <text className={s.edgeLabel}><textPath href={`#e-${a}-${b}`} startOffset="50%">{label}</textPath></text>}
            <path id={`e-${a}-${b}`} d={d} className={s.ghost} />
          </g>;
        })}
        {NODES.map(n => (
          <g key={n.id} transform={`translate(${n.x} ${n.y})`} className={`${s.node} ${s[n.kind]} ${n.id in hot ? s.nodeOn : ""}`}>
            <rect width={NW} height={NH} rx={10} />
            <text x={14} y={22} className={s.title}>{n.title}</text>
            <text x={14} y={40} className={s.sub}>{n.sub}</text>
            {!!counts[n.id] && <text x={NW - 12} y={22} textAnchor="end" className={s.count}>{counts[n.id]}</text>}
          </g>
        ))}
      </svg>
      <div className={s.why}>
        <span className="micro">Why each step ran</span>
        {why.length === 0 ? <p className={s.empty}>Press Play or Next day: each step appears here with the trigger that caused it.</p>
          : why.map((w, i) => <p key={i} className={i === 0 ? s.latest : ""}><span>{w.at}</span>{w.text}</p>)}
      </div>
    </section>
  );
}
