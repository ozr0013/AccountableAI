"use client";

// Simulated flow: 15 days, replayed through the real session one step at a time
// (dashboard/feed/simulation.py). Each step comes back with the workflow steps that ran, the agents
// that ran and every write to the event log; the map and the log animate exactly that. With no feed
// running, a recording of a real run plays instead (public/feed/simulation.json).

import { useEffect, useRef, useState } from "react";
import { EngineMap, type Frame as MapFrame, sleep, useEngineMap } from "./EngineMap";
import { useFeed } from "./Feed";
import { money } from "@/lib/format";

type Frame = MapFrame & { index: number; day: number; label: string; ok?: boolean; unexplained: number; as_of?: string };

const DAYS = 15;
const START = 3140;

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
  const [error, setError] = useState<string | null>(null);
  const map = useEngineMap();
  const stopped = useRef(false);
  const logRef = useRef<HTMLOListElement>(null);

  useEffect(() => {
    (async () => {
      try {
        const r = await fetch(live ? "/api/simulation" : "/feed/simulation.json", { cache: "no-store" });
        const data = await r.json();
        setScript(live ? data : data.frames.map((f: Frame) => ({ index: f.index, day: f.day, label: f.label })));
      } catch { setScript([]); }
    })();
    return () => { stopped.current = true; map.stop(); };
  }, [live, map.stop]);

  useEffect(() => { logRef.current?.lastElementChild?.scrollIntoView({ block: "nearest" }); }, [done]);

  const animate = async (f: Frame) => {
    setDay(f.day);
    await map.animate(f);
  };

  const play = async () => {
    stopped.current = false;
    setPlaying(true); setError(null); setDone([]); setDay(0); map.clear();
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
  const eventDays = new Set(script.map((s) => s.day));

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
        <EngineMap map={map} label={last ? `Day ${last.day}: ${last.label}` : "Waiting to start"} />

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
