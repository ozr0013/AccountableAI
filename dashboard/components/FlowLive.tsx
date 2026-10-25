"use client";

// The investigation's live flow map. Each line is a workflow edge; a blue light travels it when
// the live feed's /api/stream reports that step (workflow/runner.py emits one `step` per node).
// Steps arrive milliseconds apart in rules mode, so they are queued and played ~0.4s apart: the
// real path, at a speed people can follow. With no live feed, the expected path is played and
// labelled as a replay.

import { useEffect, useRef, useState } from "react";
import manifest from "@/feed/samples/manifest.json";
import { useFeed } from "./Feed";
import { money } from "@/lib/format";
import type { ActionResult, Diff } from "@/lib/types";

const FEED_URL = process.env.NEXT_PUBLIC_FEED_URL || "http://127.0.0.1:8765";
const PACE = 420;

type Sample = { file: string; label: string; flow: "statement" | "evidence" | "reply"; expect: string };
const SAMPLES = manifest as Sample[];

type NodeId = "in" | "detect" | "reply" | "assess" | "verify" | "owner" | "chase" | "middleman";
// Top to bottom: paperwork (or the middleman's reply) in, the reader that fits it, the checks,
// then the outcome. Straight segments and right angles only.
const L = 120, C = 320, R = 520;
const NODES: { id: NodeId; x: number; y: number; title: string; tone: string }[] = [
  { id: "in", x: C, y: 36, title: "Paperwork in", tone: "src" },
  { id: "middleman", x: R, y: 36, title: "Middleman", tone: "out" },
  { id: "detect", x: L, y: 136, title: "Anomaly detector", tone: "model" },
  { id: "reply", x: R, y: 136, title: "Chaser reads reply", tone: "model" },
  { id: "assess", x: C, y: 236, title: "Trail + Cause", tone: "code" },
  { id: "verify", x: C, y: 336, title: "Verifier", tone: "ver" },
  { id: "owner", x: L, y: 436, title: "Owner", tone: "human" },
  { id: "chase", x: R, y: 436, title: "Chaser reminds", tone: "code" },
];
const EDGES: Record<string, string> = {
  "in>detect": `M ${C} 58 V 86 H ${L} V 114`,
  "in>assess": `M ${C} 58 V 214`,
  "middleman>reply": `M ${R} 58 V 114`,
  "detect>assess": `M ${L} 158 V 186 H ${C - 40} V 214`,
  "reply>assess": `M ${R} 158 V 186 H ${C + 40} V 214`,
  "assess>verify": `M ${C} 258 V 314`,
  "verify>owner": `M ${C} 358 V 386 H ${L} V 414`,
  "verify>chase": `M ${C} 358 V 386 H ${R} V 414`,
  "chase>middleman": `M ${R + 80} 436 H ${R + 104} V 36 H ${R + 80}`,
};
const JOINTS: [number, number][] = [[C, 86], [C, 386]];          // where a line branches
const NODE_OF: Record<string, NodeId> = {
  route: "in", detect_anomalies: "detect", read_reply: "reply", assess: "assess",
  draft_alerts: "verify", verify: "verify", chase: "chase", funding_review: "owner",
};
// What each flow should look like, for the replay when no live feed is running.
const EXPECTED: Record<Sample["flow"], { step: string; notes?: string[] }[]> = {
  statement: [{ step: "route" }, { step: "detect_anomalies" }, { step: "assess" }, { step: "draft_alerts" },
    { step: "verify", notes: ["verifier approved"] }, { step: "chase" }, { step: "finish" }],
  evidence: [{ step: "route" }, { step: "assess" }, { step: "draft_alerts" },
    { step: "verify", notes: ["withdrawn"] }, { step: "chase" }, { step: "finish" }],
  reply: [{ step: "route" }, { step: "read_reply" }, { step: "assess" }, { step: "draft_alerts" },
    { step: "verify", notes: ["still holds"] }, { step: "chase" }, { step: "finish" }],
};

/** The change in a few words: alerts raised and withdrawn. */
function outcome(d: Diff): string {
  const n = (k: number, what: string) => `${k} alert${k === 1 ? "" : "s"} ${what}`;
  const parts = [d.raised.length ? n(d.raised.length, "raised") : "", d.withdrawn.length ? n(d.withdrawn.length, "withdrawn") : ""].filter(Boolean);
  return parts.length ? parts.join(", ") : "no change";
}

type Beat = { kind: "start"; from: NodeId; caption: string } | { kind: "step"; step: string; notes: string[] };

export function FlowLive() {
  const { mode, busy, addFile, setPanel } = useFeed();
  const live = mode === "live";
  const [pick, setPick] = useState(SAMPLES[0].file);
  const [lit, setLit] = useState<Set<string>>(new Set());          // nodes and edges visited this run
  const [active, setActive] = useState<string | null>(null);        // the node working now
  const [comet, setComet] = useState<{ edge: string; n: number } | null>(null);
  const [caption, setCaption] = useState("Waiting for paperwork");
  const [result, setResult] = useState<ActionResult | null>(null);
  const [connected, setConnected] = useState(false);
  const queue = useRef<Beat[]>([]);
  const prev = useRef<NodeId>("in");
  const start = useRef<NodeId>("in");
  const n = useRef(0);
  const seenRef = useRef<Set<string>>(new Set());
  const sample = SAMPLES.find((s) => s.file === pick) ?? SAMPLES[0];

  // play the queue at a readable pace
  useEffect(() => {
    const timer = window.setInterval(() => {
      const beat = queue.current.shift();
      if (!beat) return;
      const seen = seenRef.current;
      if (beat.kind === "start") {
        start.current = prev.current = beat.from;
        seenRef.current = new Set([beat.from]);
        setCaption(beat.caption);
        setActive(beat.from);
        setLit(new Set(seenRef.current));
        return;
      }
      if (beat.step === "route") { prev.current = start.current; seen.add(start.current); }
      else if (beat.step === "finish") setActive(null);
      else {
        const node = NODE_OF[beat.step];
        if (!node) return;
        const key = `${prev.current}>${node}`;
        if (node !== prev.current && EDGES[key]) { seen.add(key); setComet({ edge: key, n: ++n.current }); }
        seen.add(node);
        setActive(node);
        const notes = beat.notes.join(" ").toLowerCase();
        if (beat.step === "verify" && /approved|withdrawn|still holds|refreshed/.test(notes)) { seen.add("verify>owner"); seen.add("owner"); }
        if (beat.step === "chase" && /reminder sent/.test(notes)) { seen.add("chase>middleman"); seen.add("middleman"); }
        prev.current = node;
      }
      setLit(new Set(seen));
    }, PACE);
    return () => window.clearInterval(timer);
  }, []);

  // the live feed: every workflow step, as it finishes
  useEffect(() => {
    if (!live || typeof EventSource === "undefined") return;
    const es = new EventSource(`${FEED_URL}/api/stream`);
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.addEventListener("action", (e) => {
      const m = JSON.parse((e as MessageEvent).data);
      if (m.status !== "started") return;
      const from: NodeId = m.kind === "reply" ? "middleman" : "in";
      queue.current.push({ kind: "start", from, caption: m.summary });
      if (from === "middleman") queue.current.push({ kind: "step", step: "route", notes: [] });
    });
    es.addEventListener("step", (e) => {
      const m = JSON.parse((e as MessageEvent).data);
      queue.current.push({ kind: "step", step: m.step, notes: m.notes ?? [] });
    });
    return () => es.close();
  }, [live]);

  const add = async () => {
    setResult(null);
    if (!live) {                            // replay: the expected path, labelled as such
      const from: NodeId = sample.flow === "reply" ? "middleman" : "in";
      queue.current.push({ kind: "start", from, caption: `Replay: ${sample.label}` },
        ...EXPECTED[sample.flow].map((b) => ({ kind: "step" as const, step: b.step, notes: b.notes ?? [] })));
      return;
    }
    const r = await addFile(sample.file);
    setPanel(false);                        // the map is the answer here; no side panel
    setResult(r);
  };

  const cls = (id: string) => (id === active ? " active" : lit.has(id) ? " lit" : "");
  const ok = result && result.ok ? result : null;

  return (
    <section className="flow-live" aria-label="Live flow">
      <div className="flow-bar">
        <select aria-label="File" value={pick} onChange={(e) => setPick(e.target.value)}>
          {SAMPLES.map((s) => <option key={s.file} value={s.file}>{s.label}</option>)}
        </select>
        <button className="btn" onClick={add} disabled={busy}>{busy ? "Reading…" : live ? "Add" : "Play"}</button>
        {live && <a className="pill" href={`/api/samples/${encodeURIComponent(sample.file)}`} target="_blank" rel="noreferrer">Open ↗</a>}
        <span className={`chip ${live && connected ? "live" : "replay"}`}>{live && connected ? "Live" : "Replay"}</span>
      </div>

      <svg className="flow-map" viewBox="0 0 640 470" role="img" aria-label={caption}>
        {Object.entries(EDGES).map(([k, d]) => <path key={k} d={d} className={`fl-edge${lit.has(k) ? " lit" : ""}`} />)}
        {JOINTS.map(([x, y]) => <rect key={`${x}-${y}`} x={x - 3} y={y - 3} width="6" height="6" className="fl-joint" />)}
        {comet && <path key={comet.n} d={EDGES[comet.edge]} pathLength={100} className="fl-comet" />}
        {NODES.map((nd) => (
          <g key={nd.id} className={`fl-node t-${nd.tone}${cls(nd.id)}`} transform={`translate(${nd.x - 80} ${nd.y - 22})`}>
            <rect width="160" height="44" />
            <text x="80" y="27" textAnchor="middle">{nd.title}</text>
          </g>
        ))}
      </svg>

      <p className="flow-result" aria-live="polite">
        {ok ? <>Unexplained <b>{money(ok.diff.unexplained[0], false)} → {money(ok.diff.unexplained[1], false)}</b>
          <span> · {outcome(ok.diff)}{ok.parsed?.flags[0] ? ` · flagged: ${ok.parsed.flags[0]}` : ""}</span></>
          : result && !result.ok ? <span className="warn-ink">{result.error}</span> : null}
      </p>
    </section>
  );
}
