"use client";

import Link from "next/link";
import { Badge, PageTop, SectionTitle } from "@/components/ui";

const SYSTEM = [
  ["9 / 9", "planted scenarios passed", "Rules workflow, fresh run"],
  ["100%", "cited claims", "No uncited alert gets through"],
  ["0", "prohibited actions", "Money movement, self-approval, and unsafe sends are blocked"],
  ["$0", "rules-mode cost", "Deterministic workflow can run without an API key"],
];

const COMPARE = [
  ["AccountableAI workflow", "9/9", "100%", "yes", "$0.00", "Rules-mode harness · current local run"],
  ["Live verifier", "9/9", "100%", "yes", "$0.0883", "Haiku verifier run · 59 calls"],
  ["One strong model, all docs", "8/9", "83%", "no", "$0.3075", "Opus baseline · 132 documents, 2 calls"],
];

const ADVERSARIAL = [
  ["No verifier", "0 / 8", "Approved every planted bad draft"],
  ["Code rules only", "4 / 8", "Caught wording and citation failures"],
  ["Live verifier", "8 / 8", "Blocked or refuted every planted bad draft"],
];

export default function Proof() {
  return <>
    <PageTop title="Proof, not promises" sub="Every number here comes from the repository’s scored evaluation artifacts. Open the demo to see the same rails in action." />

    <section className="proof-hero">
      <div><span className="label">/ Evaluation snapshot</span><h1>Agents can reason.<br />The rails decide.</h1><p>Nine planted scenarios test source conflicts, time, selective recheck, controller-only holds, citations, and reconciliation.</p></div>
      <Link className="btn on-wine" href="/investigation">Watch the investigation ↗</Link>
    </section>

    <div className="proof-metrics">
      {SYSTEM.map(([value, label, note]) => <article key={label}><strong>{value}</strong><span>{label}</span><small>{note}</small></article>)}
    </div>

    <section className="proof-panel">
      <SectionTitle right="same answer key">Workflow vs. “just ask a model”</SectionTitle>
      <div className="table-scroll"><table className="table proof-table"><thead><tr><th>System</th><th>Scenarios</th><th>Cause accuracy</th><th>Moment recheck</th><th>Cost</th><th>Run</th></tr></thead><tbody>{COMPARE.map(([name, scenarios, accuracy, moment, cost, run]) => <tr key={name}><td><span className="row"><Badge kind={name === "One strong model, all docs" ? "idle" : "done"} />{name}</span></td><td>{scenarios}</td><td>{accuracy}</td><td>{moment}</td><td className="num">{cost}</td><td className="muted">{run}</td></tr>)}</tbody></table></div>
      <p className="proof-caption">The baseline saw all the same documents. It missed an on-time vendor-side case and rechecked more than one alert after the changed fact.</p>
    </section>

    <section className="proof-panel">
      <SectionTitle right="planted bad drafts">The verifier earns its place</SectionTitle>
      <div className="proof-adversarial">{ADVERSARIAL.map(([name, score, note], i) => <article key={name} className={i === 2 ? "winner" : ""}><span className="label">{name}</span><strong>{score}</strong><p>{note}</p></article>)}</div>
      <p className="proof-caption">Bad drafts include accusations, invented quotes, fake sources, unsupported citations, claims contradicted by bank/vendor proof, and attempts to blame Keyway for authorized or owner-side delays.</p>
    </section>

    <section className="proof-callout"><div><span className="label">/ Safety boundary</span><h2>Zero is the only acceptable number for prohibited actions.</h2><p>The policy gate refuses money movement, outbound reminders outside approved templates, self-approval, and unverified bank-detail changes—before an agent’s text can become an action.</p></div><Link href="/guardrails" className="pill">Inspect the guardrails ↗</Link></section>
  </>;
}
