"use client";

// Live investigation: a real run of outside paperwork through the engine (public/feed/pack_run.json,
// written by the pack runner). Every number here comes from that run's event log.

import { useEffect, useState } from "react";
import { money } from "@/lib/format";

type File = { name: string; kind: string; use: "evidence" | "quarantined" | "on file" | "rejected"; detail: string };
type Row = { invoice_id: string; vendor: string; amount: number; funded: number; paid: number; due: string | null; status: string; cause: string | null; alert: string | null };
type Day = { date: string; writes: number; unexplained: number; reminders: string[]; alerts: string[]; withdrawn: string[]; holds: string[] };
type Run = {
  pack: string; model: string; cost_usd: number; calls: number; files: File[]; ledger: Row[]; writes: number;
  unexplained_start: number; unexplained_end: number; alerts: { kind: string; headline: string; amount: number }[];
  reminders: number; hold: string | null; quarantined: string[]; flags: string[]; days: Day[]; terms: Record<string, number>;
};

const USE_LABEL: Record<File["use"], string> = { evidence: "Used as evidence", quarantined: "Quarantined", "on file": "Kept on file", rejected: "Not readable" };
const dayLabel = (iso: string) => new Date(`${iso}T12:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });

export function LiveRun() {
  const [run, setRun] = useState<Run | null | undefined>(undefined);
  useEffect(() => {
    fetch("/feed/pack_run.json", { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)).then(setRun).catch(() => setRun(null));
  }, []);
  if (run === undefined) return null;
  if (run === null) return <section className="sim-empty"><p>No real paperwork run yet.</p></section>;

  return (
    <section className="lr" aria-label="Real paperwork run">
      <div className="sim-stats lr-stats">
        <div><span>Files read</span><strong>{run.files.filter((f) => f.use !== "rejected").length} / {run.files.length}</strong><small>{run.pack}</small></div>
        <div><span>Unexplained</span><strong>{money(run.unexplained_end)}</strong><small>{money(run.unexplained_start)} on day 1</small></div>
        <div><span>Owner alerts</span><strong>{run.alerts.length}</strong><small>{run.reminders} reminders · recorded, not emailed</small></div>
        <div><span>Event log writes</span><strong>{run.writes}</strong><small>{run.model} · ${run.cost_usd.toFixed(2)} end to end</small></div>
      </div>
      {run.hold && <p className="lr-hold"><b>Recommended:</b> {run.hold.replace(/^Recommend holding/, "hold")}</p>}

      <h2 className="lr-h">What each file was read as</h2>
      <div className="table-scroll lr-box">
        <table className="table lr-table">
          <thead><tr><th>File</th><th>Read as</th><th>Used</th><th>Detail</th></tr></thead>
          <tbody>{run.files.map((f) => (
            <tr key={f.name} className={`u-${f.use.replace(" ", "-")}`}>
              <td className="mono">{f.name}</td><td>{f.kind.replace(/_/g, " ")}</td><td>{USE_LABEL[f.use]}</td><td>{f.detail}</td>
            </tr>))}
          </tbody>
        </table>
      </div>

      <h2 className="lr-h">The ledger, invoice by invoice</h2>
      <div className="table-scroll lr-box">
        <table className="table lr-table">
          <thead><tr><th>Invoice</th><th>Vendor</th><th className="r">Amount</th><th className="r">Funded</th><th className="r">Paid</th><th>Due</th><th>Cause</th><th>Alert</th></tr></thead>
          <tbody>{run.ledger.map((r) => (
            <tr key={r.invoice_id} className={r.alert ? "u-alert" : ""}>
              <td className="mono">{r.invoice_id}</td><td>{r.vendor}</td><td className="r num">{money(r.amount, false)}</td>
              <td className="r num">{money(r.funded, false)}</td><td className="r num">{money(r.paid, false)}</td>
              <td className="mono">{r.due ? dayLabel(r.due) : "—"}</td><td>{r.cause?.replace(/_/g, " ") ?? r.status.replace(/_/g, " ")}</td><td>{r.alert ?? "—"}</td>
            </tr>))}
          </tbody>
        </table>
      </div>

      <h2 className="lr-h">Fifteen days, on its own</h2>
      <ol className="lr-days">
        {run.days.map((d) => (
          <li key={d.date} className={d.reminders.length || d.alerts.length || d.withdrawn.length || d.holds.length ? "busy" : ""}>
            <span className="mono">{dayLabel(d.date)}</span>
            <div>
              {d.alerts.map((a, i) => <p key={`a${i}`} className="w-alert_raised">Owner alerted: {a}</p>)}
              {d.reminders.map((a, i) => <p key={`r${i}`} className="w-chase_sent">Reminder to Keyway Management, {a} · recorded, not emailed</p>)}
              {d.withdrawn.map((a, i) => <p key={`w${i}`} className="w-alert_withdrawn">Alert withdrawn: {a}</p>)}
              {d.holds.map((a, i) => <p key={`h${i}`} className="w-case_proposed">{a}</p>)}
              {!(d.reminders.length || d.alerts.length || d.withdrawn.length || d.holds.length) && <p className="w-quiet">Scheduled check: nothing new</p>}
            </div>
            <span className="mono lr-day-meta">{d.writes} writes · {money(d.unexplained, false)}</span>
          </li>))}
      </ol>

      {(run.quarantined.length > 0 || run.flags.length > 0) && <>
        <h2 className="lr-h">Caught and flagged</h2>
        <ul className="lr-flags">
          {run.quarantined.map((q, i) => <li key={`q${i}`} className="w-alert_raised">{q}</li>)}
          {run.flags.map((q, i) => <li key={`f${i}`}>{q}</li>)}
        </ul>
      </>}
    </section>
  );
}
