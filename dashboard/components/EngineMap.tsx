"use client";

// The engine as one map, each box once, lit by what the backend says ran and wrote for one action
// (dashboard/feed/session.py `_act`: steps, agents, writes). Used by Simulated flow and Live investigation.

import { useCallback, useRef, useState } from "react";

export type Note = { type: string; text: string; invoice_id: string | null };
export type Frame = {
  source: "file" | "clock" | "fact"; kind?: string;
  steps: string[]; agents: Record<string, number>; writes: { total: number; notes: Note[] };
};
export type Box = "inbox" | "clock" | "judge" | "middleman" | "log" | "anomaly" | "trail" | "chaser" | "verifier" | "case" | "owner";

export const PACE = 360;

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

/** The path one action took, from what the backend says ran and wrote. */
export function beats(f: Frame): Beat[] {
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

export const sleep = (ms: number) => new Promise<void>((r) => window.setTimeout(r, ms));

/** Animation state for the map: `animate(frame)` walks the path, a glow along each line. */
export function useEngineMap() {
  const [lit, setLit] = useState<Set<string>>(new Set());
  const [active, setActive] = useState<Box | null>(null);
  const [comet, setComet] = useState<{ edge: string; n: number } | null>(null);
  const [flash, setFlash] = useState<{ n: number; writes: number } | null>(null);
  const [cur, setCur] = useState<Frame | null>(null);
  const stopped = useRef(false);
  const n = useRef(0);

  const animate = useCallback(async (f: Frame, onBeat?: (i: number, of: number) => void) => {
    stopped.current = false;
    setCur(f);
    setLit(new Set());
    const path = beats(f);
    for (const [i, b] of path.entries()) {
      if (stopped.current) return;
      setActive(b.box);
      if (b.edge) setComet({ edge: b.edge, n: ++n.current });
      if (b.box === "log") setFlash({ n: n.current, writes: f.writes.total });
      setLit((old) => new Set([...old, b.box, ...(b.edge ? [b.edge] : [])]));
      onBeat?.(i + 1, path.length);
      await sleep(PACE);
    }
    setActive(null);
  }, []);
  const stop = useCallback(() => { stopped.current = true; }, []);
  const clear = useCallback(() => { setLit(new Set()); setActive(null); setComet(null); setFlash(null); setCur(null); }, []);
  return { lit, active, comet, flash, cur, animate, stop, clear };
}

export function EngineMap({ map, label }: { map: ReturnType<typeof useEngineMap>; label: string }) {
  const { lit, active, comet, flash, cur } = map;
  const agentCount = (box: Box) => {
    if (!cur || !active) return 0;
    return Math.max(0, ...Object.entries(cur.agents).filter(([a]) => AGENT_BOX[a] === box).map(([, v]) => v));
  };
  const cls = (id: string) => (id === active ? " active" : lit.has(id) ? " lit" : "");
  return (
    <svg className="sim-map" viewBox="0 0 760 500" role="img" aria-label={label}>
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
  );
}
