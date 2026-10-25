"use client";

import { useEffect, useRef, useState } from "react";

// "Where is it now": which agent is awake, what it is doing, and how long it has been at it, so a
// run never looks like a black box. It reads the same lit/active box ids the engine map uses, in
// both the live-agent run and the rules replay.

const STAGES: { label: string; boxes: string[] }[] = [
  { label: "Arrives", boxes: ["inbox", "clock", "you"] },
  { label: "Reader", boxes: ["reader"] },
  { label: "Event log", boxes: ["log"] },
  { label: "Checks & routing", boxes: ["router"] },
  { label: "Agents", boxes: ["agreement", "chaser", "anomaly"] },
  { label: "Verifier", boxes: ["verifier"] },
  { label: "Policy gate", boxes: ["gate"] },
  { label: "Owner & Keyway", boxes: ["owner", "email", "controller"] },
];
const AGENTS: { label: string; box: string }[] = [
  { label: "Agreement agent", box: "agreement" },
  { label: "Chaser agent", box: "chaser" },
  { label: "Anomaly agent", box: "anomaly" },
  { label: "Verifier agent", box: "verifier" },
];

export function WhereNow({ lit, active, running, started, doing, stalled }: {
  lit: Set<string>; active: Set<string>; running: boolean; started: boolean; doing: string | null; stalled?: boolean;
}) {
  const [secs, setSecs] = useState(0);
  const since = useRef(Date.now());
  useEffect(() => { since.current = Date.now(); setSecs(0); }, [doing]);
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(() => setSecs(Math.floor((Date.now() - since.current) / 1000)), 1000);
    return () => window.clearInterval(t);
  }, [running]);

  const state = (boxes: string[]) => (boxes.some((b) => active.has(b)) ? "now" : boxes.some((b) => lit.has(b)) ? "done" : "");
  const agentNote = (box: string) =>
    active.has(box) ? "working now" : lit.has(box) ? "done" : running ? "waiting" : started ? "didn’t need to wake" : "asleep";

  return (
    <div className="wn" aria-live="polite">
      <p className={`wn-now${running ? " live" : ""}`}>
        <span className="wn-dot" aria-hidden="true" />
        <b>{stalled ? "Lost the connection to the agents" : running ? "Now" : started ? "Finished" : "Waiting for input"}</b>
        <span>{stalled ? "Reset books and try again." : doing ?? (started ? "Every step that ran is below." : "Give the engine something above.")}</span>
        {running && !stalled && doing && <em>{secs}s{secs >= 8 ? " · a model is reading the books, this can take up to a minute" : ""}</em>}
      </p>
      <ol className="wn-stages" aria-label="Where the work is">
        {STAGES.map((s) => <li key={s.label} className={state(s.boxes)}>{s.label}</li>)}
      </ol>
      <ul className="wn-agents" aria-label="The four agents">
        {AGENTS.map((a) => (
          <li key={a.box} className={state([a.box])}><b>{a.label}</b><span>{agentNote(a.box)}</span></li>
        ))}
      </ul>
    </div>
  );
}
