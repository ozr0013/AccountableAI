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
import type { ActionResult } from "@/lib/types";

const FEED_URL = process.env.NEXT_PUBLIC_FEED_URL || "http://127.0.0.1:8765";
const PACE = 420;

type Sample = { file: string; label: string; flow: "statement" | "evidence" | "reply"; expect: string };
const SAMPLES = manifest as Sample[];

type NodeId = "in" | "detect" | "reply" | "assess" | "verify" | "owner" | "chase" | "middleman";
const NODES: { id: NodeId; x: number; y: number; title: string; step: string; tone: string }[] = [
  { id: "in", x: 80, y: 150, title: "Paperwork in", step: "route", tone: "src" },
  { id: "detect", x: 270, y: 60, title: "Anomaly detector", step: "detect_anomalies", tone: "model" },
  { id: "reply", x: 270, y: 240, title: "Chaser reads reply", step: "read_reply", tone: "model" },
  { id: "assess", x: 460, y: 150, title: "Trail + Cause", step: "assess", tone: "code" },
  { id: "verify", x: 650, y: 150, title: "Verifier", step: "verify", tone: "ver" },
  { id: "owner", x: 840, y: 60, title: "Owner", step: "alert_raised", tone: "human" },
  { id: "chase", x: 840, y: 240, title: "Chaser reminds", step: "chase", tone: "code" },
  { id: "middleman", x: 650, y: 296, title: "Middleman", step: "chase_sent", tone: "out" },
];
const EDGES: Record<string, string> = {
  "in>detect": "M 150 150 C 175 150, 175 60, 200 60",
  "in>reply": "M 150 150 C 175 150, 175 240, 200 240",
  "in>assess": "M 150 150 L 390 150",
  "detect>assess": "M 340 60 C 365 60, 365 150, 390 150",
  "reply>assess": "M 340 240 C 365 240, 365 150, 390 150",
  "assess>verify": "M 530 150 L 580 150",
  "verify>owner": "M 720 150 C 745 150, 745 60, 770 60",
  "verify>chase": "M 720 150 C 745 150, 745 240, 770 240",
  "chase>middleman": "M 840 266 C 840 296, 760 296, 720 296",
  "middleman>reply": "M 580 296 C 440 296, 380 296, 340 250",
};
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

type Beat = { kind: "start"; from: NodeId; caption: string } | { kind: "step"; step: string; notes: string[] };

export function FlowLive() {
  const { state, mode, busy, addFile } = useFeed();
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
    setResult(await addFile(sample.file));
  };

  const cls = (id: string) => (id === active ? " active" : lit.has(id) ? " lit" : "");
  const paper = state?.meta.paperwork;
  const ok = result && result.ok ? result : null;

  return (
    <section className="flow-live" aria-label="Live flow of the investigation">
      <div className="flow-head">
        <div>
          <span className="label">/ live flow</span>
          <h2>Drop in paperwork. <em className="serif">Watch it travel.</em></h2>
        </div>
        <span className={`chip ${live && connected ? "live" : "replay"}`}>
          {live ? (connected ? "Live · following the workflow" : "Live feed · connecting") : "Replay · feed not running"}
        </span>
      </div>

      <div className="flow-body">
        <div className="flow-controls">
          <label className="flow-field">
            <span className="micro muted">File</span>
            <select value={pick} onChange={(e) => setPick(e.target.value)}>
              {SAMPLES.map((s) => <option key={s.file} value={s.file}>{s.label}</option>)}
            </select>
          </label>
          <p className="flow-expect">{sample.expect}</p>
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            <button className="btn" onClick={add} disabled={busy}>{busy ? "Reading…" : live ? "Add to inbox" : "Play the expected path"}</button>
            {live
              ? <a className="pill" href={`/api/samples/${encodeURIComponent(sample.file)}`} target="_blank" rel="noreferrer">Open file ↗</a>
              : <span className="pill" aria-disabled="true" title="Needs the live feed">Open file ↗</span>}
          </div>
          <p className="flow-paper micro">
            {paper ? `Running on raw files: ${paper.records} records read, ${paper.documents} documents, ${paper.flagged} flagged for a person.`
              : live ? "Running on clean records. Start the feed with FEED_MESSY=1 to read raw PDFs, scans and emails."
                : "Start python dashboard/feed/serve.py to run the real workflow."}
          </p>
        </div>

        <div className="flow-map">
          <svg viewBox="0 0 1000 330" role="img" aria-label={`Flow map. ${caption}`}>
            {Object.entries(EDGES).map(([k, d]) => <path key={k} d={d} className={`fl-edge${lit.has(k) ? " lit" : ""}`} />)}
            {comet && <path key={comet.n} d={EDGES[comet.edge]} pathLength={100} className="fl-comet" />}
            {NODES.map((nd) => (
              <g key={nd.id} className={`fl-node t-${nd.tone}${cls(nd.id)}`} transform={`translate(${nd.x - 70} ${nd.y - 26})`}>
                <rect width="140" height="52" rx="12" />
                <text x="14" y="23" className="fl-title">{nd.title}</text>
                <text x="14" y="39" className="fl-step">{nd.id === "in" ? "paperwork" : nd.step}</text>
              </g>
            ))}
          </svg>
          <p className="flow-caption" aria-live="polite">{caption}</p>
        </div>
      </div>

      {result && (
        <div className="flow-result">
          {ok ? <>
            <div><span className="micro muted">Read as</span><strong>{[ok.parsed?.kind].flat().join(", ").replace(/_/g, " ") || ok.kind}</strong></div>
            <div><span className="micro muted">Unexplained</span><strong>{money(ok.diff.unexplained[0], false)} → {money(ok.diff.unexplained[1], false)}</strong></div>
            <div className="flow-result-wide"><span className="micro muted">What happened</span>
              {ok.responses.slice(0, 3).map((r, i) => <p key={i}><b>{r.agent}</b> {r.what}</p>)}
              {ok.parsed?.flags.map((f, i) => <p key={`f${i}`} className="warn-ink">Flagged: {f}</p>)}
            </div>
          </> : <p className="warn-ink">{result.ok ? "" : result.error}</p>}
        </div>
      )}
    </section>
  );
}
