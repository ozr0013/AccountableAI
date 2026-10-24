"use client";

import { useState } from "react";
import { Disclosure } from "./Disclosure";
import { useFeed } from "./Feed";
import { day } from "@/lib/format";

/** Demo controls in the sidebar: the demo clock, rules vs Claude agents, spend so far, reset. */
export function DemoBar() {
  const { state, mode, advance, setLive, reset, busy } = useFeed();
  const [err, setErr] = useState<string | null>(null);
  if (!state) return null;
  const m = state.meta;
  const live = m.agents === "live";
  const moved = m.start && m.start !== m.as_of;
  return (
    <Disclosure className="demo-disclosure" title="Demo controls" hint={`${live ? "Claude" : "Rules"} · ${day(m.as_of)}`}><div className="demobar">
      <div className="between">
        <span className="micro muted">Demo clock</span>
        <span className="micro" style={{ color: moved ? "var(--o)" : "var(--n10)" }}>{day(m.as_of, true)}</span>
      </div>
      <div className="row" style={{ gap: 6 }}>
        {[1, 5].map((d) => (
          <button key={d} className="mini" disabled={busy || mode !== "live"} onClick={() => advance(d)}>+{d} day{d > 1 ? "s" : ""}</button>
        ))}
        <button className="mini" disabled={busy} onClick={() => reset()} title="Rebuild the demo world">Reset</button>
      </div>
      <div className="between" style={{ marginTop: 4 }}>
        <span className="micro muted">Agents</span>
        <div className="seg" role="group" aria-label="Agent mode">
          <button aria-pressed={!live} disabled={busy || mode !== "live"} onClick={async () => { setErr(null); await setLive(false); }}>Rules</button>
          <button aria-pressed={live} disabled={busy || mode !== "live" || !m.live_available}
            title={m.live_available ? `Claude for the next events, estimated spend threshold $${m.meter?.limit.toFixed(2)}` : "No API key in the feed's environment"}
            onClick={async () => { const r = await setLive(true); setErr(r.ok ? null : r.error ?? "refused"); }}>Claude</button>
        </div>
      </div>
      {m.meter && (
        <div className="meter" title={`${m.meter.calls} model calls`}>
          <div style={{ width: `${Math.min(100, (m.meter.spent / m.meter.limit) * 100)}%` }} />
        </div>
      )}
      {m.meter && <span className="micro faint">${m.meter.spent.toFixed(3)} of ${m.meter.limit.toFixed(2)} · {m.meter.calls} calls</span>}
      {mode !== "live" && <span className="micro faint" style={{ textTransform: "none", letterSpacing: 0 }}>Replay feed: start the live feed for these.</span>}
      {err && <span className="micro warn-ink" style={{ textTransform: "none", letterSpacing: 0 }}>{err}</span>}
    </div></Disclosure>
  );
}
