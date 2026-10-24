"use client";

// The investigation, day by day, from GET /api/timeline: what arrived, what each agent decided
// (with its reasoning), what the workflow did, what a person decided, and the unexplained balance.

import { useEffect, useState } from "react";
import { DecisionCard } from "@/components/AgentStream";
import { Cites } from "@/components/ui";
import { money } from "@/lib/format";
import type { Timeline, TimelineDay } from "@/lib/types";

function Day({ day }: { day: TimelineDay }) {
  const [open, setOpen] = useState(false);
  const delta = day.unexplained_end - day.unexplained_start;
  const agentDecisions = day.decisions.filter(d => d.agent !== "workflow");
  return (
    <article className={`day-card ${day.quiet ? "quiet" : ""}`}>
      <button className="day-head" onClick={() => setOpen(o => !o)} aria-expanded={open} disabled={day.quiet}>
        <span className="simulation-day">{day.label}</span>
        <span className="day-date micro">{day.date}</span>
        <span className="day-counts">{day.quiet ? "Nothing new" : `${day.counts.facts} new facts · ${agentDecisions.length} agent decisions${day.counts.claude ? ` (${day.counts.claude} by Claude)` : ""} · ${day.counts.outcomes} outcomes${day.counts.human ? ` · ${day.counts.human} human` : ""}`}</span>
        <span className={`day-money ${delta < 0 ? "down" : delta > 0 ? "up" : ""}`}>
          {money(day.unexplained_end, false)}{delta !== 0 ? ` (${delta > 0 ? "+" : "−"}${money(Math.abs(delta), false)})` : ""}
        </span>
      </button>
      {open && <div className="day-body">
        {day.facts.length > 0 && <div><span className="label">/ arrived</span><ul>{day.facts.map(f => <li key={f.id}>{f.what} {f.source_id && <Cites cites={[f.source_id]} />}</li>)}</ul></div>}
        {day.outcomes.length > 0 && <div><span className="label">/ what changed</span><ul>{day.outcomes.map((o, i) => <li key={i}><strong>{o.type.replaceAll("_", " ")}</strong>{o.invoice_ids.filter(Boolean).length ? ` · ${o.invoice_ids.filter(Boolean).join(", ")}` : ""}{o.what ? ` — ${o.what}` : ""}</li>)}</ul></div>}
        {day.human.length > 0 && <div><span className="label">/ people decided</span><ul>{day.human.map((h, i) => <li key={i}>{h.what}</li>)}</ul></div>}
        {agentDecisions.length > 0 && <div><span className="label">/ agent reasoning</span><div className="decision-list">{agentDecisions.map(d => <DecisionCard key={d.id} d={d} />)}</div></div>}
      </div>}
    </article>
  );
}

export function DayTimeline({ enabled, version, days = 15 }: { enabled: boolean; version: number; days?: number }) {
  const [data, setData] = useState<Timeline | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!enabled) return;
    fetch(`/api/timeline?days=${days}`, { cache: "no-store" })
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`timeline ${r.status}`)))
      .then(setData).catch(e => setError(String(e)));
  }, [enabled, version, days]);
  return (
    <section className="day-timeline">
      <div className="simulation-heading">
        <div><span className="label">/ last {days} days</span><h2>The investigation, day by day.</h2></div>
        {data && <span className="tag neutral">{data.start} → {data.as_of}</span>}
      </div>
      {!enabled && <p className="micro">Start the live feed to load the history.</p>}
      {error && <p className="micro">Couldn’t load the timeline: {error}</p>}
      {data && <div className="day-list">{[...data.days].reverse().map(d => <Day key={d.date} day={d} />)}</div>}
    </section>
  );
}
