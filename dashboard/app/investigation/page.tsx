"use client";

import Link from "next/link";
import { useState } from "react";
import { useFeed } from "@/components/Feed";
import { Badge, Cites, Loading, PageTop } from "@/components/ui";
import { money, named } from "@/lib/format";
import type { ActionResult, Alert, Citation, Response } from "@/lib/types";

const STEPS = [
  ["01", "New evidence", "A fact arrives. We preserve the original record and recheck only its dependents."],
  ["02", "Map the money", "The payment-trail agent reconciles funding, claims, bank lines, and vendor proof."],
  ["03", "Challenge the claim", "The verifier tries to disprove the alert from the cited record alone."],
  ["04", "Propose a hold", "The case agent packages the surviving evidence. It cannot move money."],
  ["05", "Omar decides", "Only the controller can approve or decline the proposed instruction."],
] as const;

function AgentLine({ response, index }: { response: Response; index: number }) {
  return <div className="investigation-response" style={{ animationDelay: `${index * 90}ms` }}>
    <Badge kind={response.agent === "Verifier" ? "done" : "flag"} />
    <div><span className="micro warn-ink">{response.agent}</span><p>{response.what}</p></div>
  </div>;
}

function citations(alerts: Alert[]): Citation[] {
  const seen = new Set<string>();
  return alerts.flatMap(a => a.claims.flatMap(c => c.citations)).filter(c => {
    const id = `${c.source_id}:${c.locator ?? ""}`;
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}

type SimulationEvent = { day: string; title: string; detail: string; responses: Response[] };
const wait = (ms: number) => new Promise<void>(resolve => window.setTimeout(resolve, ms));

const SAVED_REPLAY: SimulationEvent[] = [
  {
    day: "Day 01",
    title: "A claim enters the record",
    detail: "Keyway says Rio Grande was paid. The claim is preserved, but no bank or vendor proof accompanies it.",
    responses: [{ agent: "Payment-trail agent", what: "The manager statement conflicts with Rio Grande’s past-due notice and the absence of a matching bank payment.", type: "finding", invoice_id: "INV-KW-0301" }],
  },
  {
    day: "Day 01",
    title: "The claim is challenged",
    detail: "The verifier tests the cited sources instead of accepting the manager’s wording.",
    responses: [{ agent: "Verifier", what: "Claim does not clear: no bank transfer or vendor confirmation supports the statement. The alert remains evidence-backed.", type: "verdict", invoice_id: "INV-KW-0301" }],
  },
  {
    day: "Day 06",
    title: "The approved chase runs",
    detail: "The chaser follows the controller-approved schedule and records the response for the next recheck.",
    responses: [{ agent: "Chaser", what: "Reminder sent from an approved template. Keyway’s response is logged as a claim, not payment proof.", type: "chase", invoice_id: "INV-KW-0301" }],
  },
  {
    day: "Day 10",
    title: "New proof changes one trail",
    detail: "A bank record is supplied for Rio Grande. The workflow learns from it and reruns only that linked item.",
    responses: [{ agent: "Recheck", what: "Rio Grande is reconciled from the new bank record. Unrelated open invoices are not rerun.", type: "recheck", invoice_id: "INV-KW-0301" }],
  },
  {
    day: "Day 15",
    title: "The controller gets the revised case",
    detail: "The recommendation reflects the latest evidence, with a smaller hold and a complete audit trail.",
    responses: [{ agent: "Case agent", what: "Updated evidence pack is ready. The controller—not an agent—decides whether to approve the proposed hold.", type: "case", invoice_id: "INV-KW-0301" }],
  },
];

export default function Investigation() {
  const { state, mode, busy, runConsole, reply, advance, reset, decide } = useFeed();
  const [running, setRunning] = useState(false);
  const [decisionBusy, setDecisionBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [simulation, setSimulation] = useState<SimulationEvent[]>([]);
  if (!state) return <><PageTop title="Live investigation" /><Loading /></>;

  const last = state.history.at(-1);
  const caseToReview = state.cases.find(c => c.status === "pending" && c.kind === "funding");
  const caseAlerts = state.alerts.filter(a => caseToReview?.alerts.includes(a.id) && a.open);
  const focalItem = state.items.find(i => caseAlerts.some(a => a.invoice_ids.includes(i.id))) ?? state.items.find(i => i.attention);
  const work = last?.responses ?? [];
  const replayWork = simulation.flatMap(event => event.responses);
  const shownWork = work.length ? work : replayWork;
  const trailWork = shownWork.filter(r => /trail|cause/i.test(r.agent));
  const verifierWork = shownWork.filter(r => /verifier|recheck/i.test(r.agent));
  const caseWork = shownWork.filter(r => /case/i.test(r.agent));
  const hasRun = state.history.length > 0;
  const isLive = mode === "live";
  const watchingReplay = !isLive && simulation.length > 0;
  const hasEvidenceTrace = hasRun || watchingReplay;
  const settled = state.decisions?.some(d => d.type === "funding_instruction" && d.approval_id === caseToReview?.approval_id);

  const start = async () => {
    setRunning(true); setMessage(null);
    const r = await runConsole({ preset: "manager_replies_paid" });
    setMessage(r.ok ? "Keyway’s claim was added. The workflow has rechecked the linked payment trail." : r.error);
    setRunning(false);
  };
  const runLoop = async () => {
    setRunning(true); setMessage(null); setSimulation([]);
    if (!isLive) {
      for (const event of SAVED_REPLAY) {
        await wait(1150);
        setSimulation(previous => [...previous, event]);
      }
      setMessage("Saved replay complete. Start the live Python feed to run agents against a new event or record a controller decision.");
      setRunning(false);
      return;
    }
    const add = (day: string, title: string, detail: string, result?: ActionResult) => {
      setSimulation(previous => [...previous, { day, title, detail, responses: result?.ok ? result.responses : [] }]);
    };
    await reset();
    add("Day 01", "A claim enters the record", "Keyway says Rio Grande was paid. No bank or vendor proof is attached.");
    await wait(700);
    const claim = await runConsole({ preset: "manager_replies_paid" });
    add("Day 01", "Agents challenge the claim", claim.ok ? "The trail and verifier test the claim against the evidence already on file." : claim.error, claim);
    await wait(1500);
    const chase = await advance(5);
    add("Day 06", "The clock advances", chase.ok ? "The chaser follows the approved schedule and the open evidence is reviewed again." : chase.error, chase);
    await wait(1500);
    await advance(4);
    await wait(450);
    const proof = await reply("INV-KW-0301", "We located the transfer and attached the bank record.", "bank");
    add("Day 10", "Keyway supplies proof", proof.ok ? "New bank evidence is ingested. The agents learn from it and rerun only the linked payment trail." : proof.error, proof);
    await wait(1500);
    const followUp = await advance(5);
    add("Day 15", "The loop closes", followUp.ok ? "The workflow updates its recommendation from the latest evidence. The controller sees the revised case, not a stale alert." : followUp.error, followUp);
    setMessage("15 simulated days completed in about 10 seconds. Every event ran through the same feed and policy rails as the dashboard.");
    setRunning(false);
  };
  const makeDecision = async (approve: boolean) => {
    if (!caseToReview) return;
    setDecisionBusy(true); setMessage(null);
    const r = await decide(caseToReview.approval_id, approve);
    setMessage(r.ok ? r.summary : r.error ?? "The policy gate refused that decision.");
    setDecisionBusy(false);
  };

  return <>
    <PageTop title="Live investigation" sub="One evidence trail, from a new claim to your decision. Agents recommend; Omar controls the money.">
      <span className={`chip ${isLive ? "live" : "replay"}`}>{isLive ? (state.meta.agents === "live" ? "Claude agents · metered" : "Rules agents · live feed") : "Replay only · agents are not running"}</span>
    </PageTop>

    <section className="investigation-hero">
      <div>
        <span className="label">/ Keyway investigation</span>
        <h1>Follow one claim<br />all the way through.</h1>
        <p>{isLive ? "Inject the manager’s claim. Watch the team reconcile it against the evidence before you decide what happens next." : "This view is showing saved demo data. Start the live Python feed to run agents or make a controller decision."}</p>
      </div>
      <div className="investigation-control">
        <span className="micro">Agent mode</span>
        <strong>{state.meta.agents === "live" ? "Claude" : "Rules"}</strong>
        <span>{state.meta.agents === "live" ? `Metered · $${state.meta.meter?.spent.toFixed(3) ?? "0.000"} spent` : "Deterministic workflow · $0"}</span>
        <button className="btn" onClick={runLoop} disabled={running || busy}>{running || busy ? "Agents are investigating…" : isLive ? "Play 15-day loop" : "Watch saved 15-day replay"}</button>
        <button className="investigation-link" onClick={start} disabled={!isLive || running || busy}>{hasRun ? "Inject another claim" : "Inject one claim"}</button>
        {!isLive && <small>Run <code>python dashboard/feed/serve.py</code> to enable this path.</small>}
      </div>
    </section>

    <ol className="investigation-steps" aria-label="Investigation progress">
      {STEPS.map(([number, title, description], index) => {
        const complete = index === 0 ? hasRun : index < 3 ? hasRun && work.length > 0 : index === 3 ? !!caseToReview || settled : settled;
        const active = (index === 0 && !hasRun) || (index === 4 && !!caseToReview);
        return <li key={number} className={`${complete ? "complete" : ""} ${active ? "active" : ""}`}>
          <span>{complete ? "✓" : number}</span><div><strong>{title}</strong><small>{description}</small></div>
        </li>;
      })}
    </ol>

    {message && <div className="notice investigation-notice">{message}</div>}

    <section className={`simulation-panel ${simulation.length ? "playing" : ""}`}>
      <div className="simulation-heading"><div><span className="label">/ accelerated investigation loop</span><h2>15 days of agent work, compressed.</h2></div><span className={`tag ${isLive && state.meta.agents === "live" ? "" : "neutral"}`}>{isLive ? (state.meta.agents === "live" ? "Claude run" : "Rules run") : "Saved replay"}</span></div>
      <p>{isLive ? "Each beat calls the live feed, records a new fact, then displays the agents’ evidence-backed decision trace." : "This is a clearly labeled saved demonstration of the same scenario. It never makes a live claim, sends a message, or enables an approval."}</p>
      <div className="simulation-track">
        {simulation.length === 0 && <div className="simulation-empty"><span>01</span><div><strong>Ready to start.</strong><small>Play the loop to watch claims, reminders, proof, rechecks, and a revised recommendation arrive.</small></div></div>}
        {simulation.map((event, index) => <article key={`${event.day}-${index}`} className="simulation-event" style={{ animationDelay: `${index * 90}ms` }}>
          <span className="simulation-day">{event.day}</span><div className="simulation-copy"><strong>{event.title}</strong><p>{event.detail}</p>{event.responses.slice(0, 3).map((response, responseIndex) => <AgentLine key={responseIndex} response={response} index={responseIndex} />)}</div>
        </article>)}
      </div>
    </section>

    <section className="investigation-grid">
      <article className={`investigation-stage ${hasEvidenceTrace ? "revealed" : ""}`}>
        <div className="stage-top"><span className="label">01 / incoming claim</span><span className="tag neutral">Preserved as a record</span></div>
        <h2>“We paid the vendor.”</h2>
        <p>Keyway’s message is never treated as proof by itself. It becomes a fact for the workflow to test.</p>
        {focalItem && <div className="investigation-fact"><span>{focalItem.vendor} · {focalItem.id}</span><strong>{money(focalItem.owed, false)} still requires proof</strong></div>}
      </article>

      <article className={`investigation-stage ${hasEvidenceTrace ? "revealed" : ""}`}>
        <div className="stage-top"><span className="label">02 / payment trail agent</span><Badge kind={trailWork.length ? "done" : "idle"} /></div>
        <h2>Money trail, not a story.</h2>
        {trailWork.length ? trailWork.slice(0, 2).map((r, i) => <AgentLine key={i} response={r} index={i} />) : <p>When the investigation runs, this agent compares the sources and records which one wins.</p>}
        {focalItem?.trail && <div className="investigation-evidence"><span className="micro">Winning record</span><p>{focalItem.trail.reasoning}</p><Cites cites={focalItem.trail.winning} /></div>}
      </article>

      <article className={`investigation-stage ${hasEvidenceTrace ? "revealed" : ""}`}>
        <div className="stage-top"><span className="label">03 / verifier</span><Badge kind={verifierWork.length ? "done" : "idle"} /></div>
        <h2>Try to prove us wrong.</h2>
        {verifierWork.length ? verifierWork.slice(0, 2).map((r, i) => <AgentLine key={i} response={r} index={i} />) : <p>Every alert must survive a citation and wording check. If the evidence does not hold, the alert fails closed.</p>}
        {caseAlerts.length > 0 && <div className="investigation-evidence"><span className="micro">Cited evidence</span><Cites cites={citations(caseAlerts).map(c => c.source_id)} /></div>}
      </article>

      <article className={`investigation-stage ${caseToReview || settled ? "revealed" : ""}`}>
        <div className="stage-top"><span className="label">04 / case agent</span><Badge kind={caseToReview ? "flag" : "idle"} /></div>
        <h2>{caseToReview ? "A hold is proposed." : "No funding case yet."}</h2>
        {caseWork.length ? caseWork.slice(0, 2).map((r, i) => <AgentLine key={i} response={r} index={i} />) : <p>The case agent assembles only the evidence that survived verification. It can propose; it cannot approve.</p>}
        {caseToReview && <div className="investigation-fact"><span>Recommended hold</span><strong>{money(caseToReview.proposed_hold, false)}</strong></div>}
      </article>
    </section>

    <section className={`controller-moment ${caseToReview ? "ready" : ""}`}>
      <div><span className="label">05 / controller decision</span><h2>{settled ? "Decision recorded." : caseToReview ? "Omar, this is your call." : "The investigation is still gathering proof."}</h2><p>{settled ? "The instruction is in the audit trail. No agent moved money." : caseToReview ? `${money(caseToReview.proposed_hold, false)} is proposed for hold. The evidence pack is ready for your review.` : "A controller decision only appears after a verified case is proposed."}</p></div>
      {caseToReview && <div className="controller-actions"><Link className="pill" href="/cases">Inspect evidence pack ↗</Link><button className="pill" disabled={!isLive || decisionBusy} onClick={() => makeDecision(false)}>Decline</button><button className="btn" disabled={!isLive || decisionBusy} onClick={() => makeDecision(true)}>{decisionBusy ? "Recording…" : `Approve ${money(caseToReview.proposed_hold, false)} hold`}</button>{!isLive && <small>Controller decisions require the live feed and policy gate.</small>}</div>}
    </section>
  </>;
}
