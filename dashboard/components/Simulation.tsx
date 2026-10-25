"use client";

// Live investigation: 15 days, replayed through the real session one step at a time
// (dashboard/feed/simulation.py). Each step comes back with the workflow steps that ran, the agents
// that ran and every write to the event log; the map and the log animate exactly that. With no feed
// running, a recording of a real run plays instead (public/feed/simulation.json).

import { useCallback, useEffect, useRef, useState } from "react";
import { useFeed } from "./Feed";
import { money } from "@/lib/format";

type Note = { type: string; text: string; invoice_id: string | null };
type Frame = {
  index: number; day: number; label: string; source: "file" | "clock" | "fact"; kind?: string; ok?: boolean;
  steps: string[]; agents: Record<string, number>; writes: { total: number; notes: Note[] };
  unexplained: number; as_of?: string;
};
type Box = "inbox" | "clock" | "judge" | "middleman" | "log" | "anomaly" | "trail" | "chaser" | "verifier" | "case" | "owner";

const DAYS = 15;
const PACE = 360;
const START = 3140;

// Each box once. Straight segments, right angles.
const BOXES: { id: Box; x: number; y: number; w: number; h: number; title: string; tone: string }[] = [
  { id: "inbox", x: 40, y: 20, w: 130, h: 44, title: "Inbox", tone: "src" },
  { id: "clock", x: 200, y: 20, w: 130, h: 44, title: "Clock", tone: "src" },
  { id: "judge", x: 360, y: 20, w: 130, h: 44, title: "New fact", tone: "src" },
  { id: "middleman", x: 590, y: 20, w: 130, h: 44, title: "Middleman", tone: "out" },
  { id: "log", x: 40, y: 110, w: 520, h: 40, title: "EVENT LOG", tone: "log" },
  { id: "anomaly", x: 40, y: 210, w: 140, h: 44, title: "Anomaly detector", tone: "agent" },
  { id: "trail", x: 220, y: 210, w: 140, h: 44, title: "Trail + Cause", tone: "agent" },
  { id: "chaser", x: 400, y: 210, w: 140, h: 44, title: "Chaser", tone: "agent" },
  { id: "verifier", x: 220, y: 320, w: 140, h: 44, title: "Verifier", tone: "check" },
  { id: "case", x: 400, y: 320, w: 140, h: 44, title: "Case · hold", tone: "agent" },
  { id: "owner", x: 220, y: 430, w: 140, h: 44, title: "Owner", tone: "human" },
];
const EDGES: Record<string, string> = {
  "inbox>log": "M 105 64 V 110", "clock>log": "M 265 64 V 110", "judge>log": "M 425 64 V 110",
  "middleman>log": "M 620 64 V 87 H 540 V 110",
  "log>anomaly": "M 110 150 V 210", "log>trail": "M 290 150 V 210", "log>chaser": "M 470 150 V 210",
  "anomaly>verifier": "M 110 254 V 287 H 270 V 320", "trail>verifier": "M 290 254 V 320",
  "chaser>trail": "M 400 232 H 360", "chaser>middleman": "M 540 232 H 655 V 64",
  "verifier>case": "M 360 342 H 400", "verifier>owner": "M 290 364 V 430", "case>owner": "M 470 364 V 452 H 360",
};
const AGENT_BOX: Record<string, Box> = { anomaly: "anomaly", trail: "trail", cause: "trail", chaser: "chaser", verifier: "verifier", case: "case" };

type Beat = { edge?: string; box: Box };

/** The path one step took, from what the backend says ran and wrote. */
function beats(f: Frame): Beat[] {
  const src: Box = f.source === "clock" ? "clock" : f.source === "fact" ? "judge" : f.kind === "reply" ? "middleman" : "inbox";
  const out: Beat[] = [{ box: src }, { edge: `${src}>log`, box: "log" }];
  const seen = new Set<string>();
  let prev = "log" as Box;
  const go = (edge: string, box: Box) => {
    if (!seen.has(edge)) { seen.add(edge); out.push({ edge, box }); }
    prev = box;
  };
  for (const s of f.steps) {
    if (s === "detect_anomalies") go("log>anomaly", "anomaly");
    else if (s === "read_reply") go("log>chaser", "chaser");
    else if (s === "assess") go(prev === "chaser" ? "chaser>trail" : "log>trail", "trail");
    else if (s === "verify") go(prev === "anomaly" ? "anomaly>verifier" : "trail>verifier", "verifier");
    else if (s === "chase") go("log>chaser", "chaser");
    else if (s === "funding_review") go("verifier>case", "case");
    else if (s === "route") prev = "log";
  }
  const wrote = (t: string) => f.writes.notes.some((n) => n.type === t);
  if (wrote("alert_raised") || wrote("alert_withdrawn")) out.push({ edge: "verifier>owner", box: "owner" });
  if (wrote("chase_sent")) out.push({ edge: "chaser>middleman", box: "middleman" });
  if (wrote("case_proposed")) out.push({ edge: "case>owner", box: "owner" });
  return out;
}

const sleep = (ms: number) => new Promise<void>((r) => window.setTimeout(r, ms));
const dateOf = (day: number) => new Date(Date.UTC(2026, 3, 21 + day)).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });

export function Simulation() {
  const { state, mode, refresh } = useFeed();
  const names = state?.meta.names ?? {};
  const named = (t: string) => t.replace(/\((v-[a-z0-9-]+)\)/g, (m, id) => (names[id] ? `(${names[id]})` : m));
  const live = mode === "live";
  const [script, setScript] = useState<{ index: number; day: number; label: string }[]>([]);
  const [playing, setPlaying] = useState(false);
  const [day, setDay] = useState(0);
  const [done, setDone] = useState<Frame[]>([]);
  const [lit, setLit] = useState<Set<string>>(new Set());
  const [active, setActive] = useState<Box | null>(null);
  const [comet, setComet] = useState<{ edge: string; n: number } | null>(null);
  const [flash, setFlash] = useState<{ n: number; writes: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cur, setCur] = useState<Frame | null>(null);
  const stopped = useRef(false);
  const n = useRef(0);
  const logRef = useRef<HTMLOListElement>(null);

  useEffect(() => {
    (async () => {
      try {
        const r = await fetch(live ? "/api/simulation" : "/feed/simulation.json", { cache: "no-store" });
        const data = await r.json();
        setScript(live ? data : data.frames.map((f: Frame) => ({ index: f.index, day: f.day, label: f.label })));
      } catch { setScript([]); }
    })();
    return () => { stopped.current = true; };
  }, [live]);

  useEffect(() => { logRef.current?.lastElementChild?.scrollIntoView({ block: "nearest" }); }, [done]);

  const animate = useCallback(async (f: Frame) => {
    setCur(f);
    setDay(f.day);
    setLit(new Set());
    for (const b of beats(f)) {
      if (stopped.current) return;
      setActive(b.box);
      if (b.edge) setComet({ edge: b.edge, n: ++n.current });
      if (b.box === "log") setFlash({ n: n.current, writes: f.writes.total });
      setLit((old) => new Set([...old, b.box, ...(b.edge ? [b.edge] : [])]));
      await sleep(PACE);
    }
    setActive(null);
  }, []);

  const play = async () => {
    stopped.current = false;
    setPlaying(true); setError(null); setDone([]); setDay(0); setLit(new Set());
    try {
      const recorded: Frame[] = live ? [] : (await (await fetch("/feed/simulation.json")).json()).frames;
      for (let i = 0; i < (live ? script.length : recorded.length); i++) {
        if (stopped.current) break;
        let f: Frame;
        if (live) {
          const r = await fetch(`/api/simulation/${i}`, { method: "POST" });
          const j = await r.json();
          if (!r.ok || j.ok === false) throw new Error(j.error ?? j.detail ?? `step ${i + 1} failed`);
          f = { ...j, unexplained: j.state.headline.unexplained };
        } else f = recorded[i];
        await animate(f);
        setDone((d) => [...d, f]);
        await sleep(500);
      }
      if (live) await refresh();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
    setPlaying(false);
  };

  const last = done.at(-1);
  const unexplained = last?.unexplained ?? START;
  const writes = done.reduce((t, f) => t + f.writes.total, 0);
  const count = (t: string) => done.reduce((c, f) => c + f.writes.notes.filter((x) => x.type === t).length, 0);
  const agentCount = (box: Box) => {
    if (!cur || !active) return 0;
    return Math.max(0, ...Object.entries(cur.agents).filter(([a]) => AGENT_BOX[a] === box).map(([, v]) => v));
  };
  const eventDays = new Set(script.map((s) => s.day));
  const cls = (id: string) => (id === active ? " active" : lit.has(id) ? " lit" : "");

  return (
    <section className="sim" aria-label="15-day simulation">
      <div className="sim-bar">
        <ol className="sim-days" aria-label="Day">
          {Array.from({ length: DAYS }, (_, i) => i + 1).map((d) => (
            <li key={d} className={`${d === day ? "now" : d < day ? "past" : ""}${eventDays.has(d) ? " has" : ""}`}>
              <span>{String(d).padStart(2, "0")}</span>
            </li>
          ))}
        </ol>
        <div className="sim-actions">
          <span className={`chip ${live ? "live" : "replay"}`}>{live ? "Live session" : "Recorded run"}</span>
          <button className="btn" onClick={play} disabled={playing || !script.length}>{playing ? `Day ${String(day).padStart(2, "0")} · ${dateOf(day || 1)}` : done.length ? "Play again" : "Play 15 days"}</button>
        </div>
      </div>

      <div className="sim-stats">
        <div><span>Unexplained</span><strong>{money(unexplained, false)}</strong><small>{money(START, false)} at start</small></div>
        <div><span>Event log writes</span><strong>{writes}</strong><small>every step is recorded</small></div>
        <div><span>Owner alerts</span><strong>{count("alert_raised")}</strong><small>{count("alert_withdrawn")} withdrawn</small></div>
        <div><span>Reminders to Keyway</span><strong>{count("chase_sent")}</strong><small>recorded, not emailed</small></div>
      </div>

      <div className="sim-body">
        <svg className="sim-map" viewBox="0 0 760 500" role="img" aria-label={last ? `Day ${last.day}: ${last.label}` : "Waiting to start"}>
          {Object.entries(EDGES).map(([k, d]) => <path key={k} d={d} className={`sm-edge${lit.has(k) ? " lit" : ""}`} />)}
          {comet && <path key={comet.n} d={EDGES[comet.edge]} pathLength={100} className="sm-comet" />}
          {BOXES.map((b) => (
            <g key={b.id} className={`sm-box t-${b.tone}${cls(b.id)}`} transform={`translate(${b.x} ${b.y})`}>
              <rect width={b.w} height={b.h} />
              <text x={b.id === "log" ? 16 : b.w / 2} y={b.h / 2 + 4.5} textAnchor={b.id === "log" ? "start" : "middle"}>{b.title}</text>
              {b.id === "log" && flash && <text key={flash.n} x={b.w - 16} y={b.h / 2 + 4.5} textAnchor="end" className="sm-writes">+{flash.writes} writes</text>}
              {agentCount(b.id) > 1 && b.id === active && <text x={b.w - 8} y={-6} textAnchor="end" className="sm-count">×{agentCount(b.id)}</text>}
            </g>
          ))}
        </svg>

        <ol className="sim-log" ref={logRef} data-lenis-prevent aria-live="polite">
          {!done.length && <li className="sim-log-empty">Press play. Each day runs through the real workflow; every line here is a write to the event log.</li>}
          {done.map((f) => (
            <li key={f.index}>
              <div className="sim-log-head"><b>Day {String(f.day).padStart(2, "0")}</b><span>{dateOf(f.day)}</span><em>{f.label}</em></div>
              <ul>
                {f.writes.notes.map((w, i) => <li key={i} className={`w-${w.type}`}>{named(w.text)}</li>)}
                <li className="w-total">{f.writes.total} writes · unexplained {money(f.unexplained, false)}</li>
              </ul>
            </li>
          ))}
        </ol>
      </div>
      {error && <p className="warn-ink sim-error">{error}</p>}
    </section>
  );
}
