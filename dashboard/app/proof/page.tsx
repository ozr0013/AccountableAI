"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Disclosure } from "@/components/Disclosure";
import { Badge, PageTop, SectionTitle } from "@/components/ui";

// Newest run first; every earlier scored run stays below, so the record is tracked over time.
// The latest comes from public/feed/pack_run.json (world/pack: run, score, export); the history
// rows from the result files in evals/.

type Check = { id: string; what: string; expect: string; passed: boolean };
type Pack = { cost_usd: number; ran_on: string; files: { use: string }[]; quarantined: string[]; score: { passed: number; total: number; checks: Check[] } };

const HISTORY = [
  ["Oct 24, 2026", "20 outside PDFs (world/pack)", "Sonnet 5.5 end to end, 15 simulated days", "12 / 15 checks", "$1.22", "latest"],
  ["Oct 24, 2026 · 05:15", "Demo world · 132 documents", "One strong model, every document (baseline)", "8 / 9", "$0.31", ""],
  ["Oct 24, 2026 · 04:42", "Demo world on Postgres", "Live Sonnet agents + Haiku verifier", "8 / 9", "$2.41", ""],
  ["Oct 24, 2026 · 01:25", "8 planted bad alerts", "Verifier: none / code rules / live", "8 / 8 stopped", "$0.015", ""],
  ["Oct 24, 2026 · 00:56", "Demo world · 9 planted scenarios", "Rules workflow + Haiku verifier", "9 / 9", "$0.09", ""],
];

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
const MISSED: Record<string, string> = {
  "4471_materiality": "No materiality threshold yet: a $0.50 shortfall became an owner alert.",
  summit_statement: "The statement check compares amounts only; it doesn't read \"$200 retained\".",
  greenleaf_dup: "It matched the March landscaping payment to G-0291 instead of catching the re-entry.",
};

export default function Proof() {
  const [pack, setPack] = useState<Pack | null>(null);
  useEffect(() => { fetch("/feed/pack_run.json", { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)).then(setPack).catch(() => setPack(null)); }, []);
  const read = pack ? pack.files.filter((f) => f.use !== "rejected").length : 0;

  return <>
    <PageTop title="Proof & evals" sub="Scored runs, newest first." />

    {pack && <>
      <section className="proof-hero">
        <div><span className="label">/ Latest · {pack.ran_on} · outside paperwork</span><h1>20 PDFs we didn’t generate.</h1>
          <p>An agreement, a bank export, a statement, invoices and emails from a separate company, including a spoofed vendor and hidden instructions. Read by Sonnet, run through the engine for 15 days, scored against an answer key written from the documents.</p></div>
        <Link className="btn on-wine" href="/console">See the run ↗</Link>
      </section>
      <div className="proof-metrics">
        <article><strong>{pack.score.passed} / {pack.score.total}</strong><span>answer-key checks</span><small>world/pack/answer_key.json</small></article>
        <article><strong>{read} / {pack.files.length}</strong><span>files read</span><small>every value checked against the page</small></article>
        <article><strong>2 / 2</strong><span>adversarial emails stopped</span><small>{pack.quarantined.length} quarantined, hidden instructions ignored</small></article>
        <article><strong>${pack.cost_usd.toFixed(2)}</strong><span>end to end</span><small>Sonnet 5.5 · 15 simulated days</small></article>
      </div>
      <section className="proof-panel">
        <SectionTitle right="world/pack/score.py">Each check, as scored</SectionTitle>
        <div className="table-scroll"><table className="table proof-table">
          <thead><tr><th>Check</th><th>Expected</th><th>Result</th></tr></thead>
          <tbody>{pack.score.checks.map((c) => <tr key={c.id}>
            <td><span className="row"><Badge kind={c.passed ? "done" : "flag"} />{c.what}</span></td>
            <td className="muted">{c.expect}</td>
            <td>{c.passed ? <span className="tag ok">Pass</span> : <span className="tag">Missed</span>}</td>
          </tr>)}</tbody>
        </table></div>
        <p className="proof-caption">What it missed: {pack.score.checks.filter((c) => !c.passed).map((c) => MISSED[c.id] ?? c.expect).join(" ")}</p>
      </section>
    </>}

    <section className="proof-panel">
      <SectionTitle right="tracked">History</SectionTitle>
      <div className="table-scroll"><table className="table proof-table">
        <thead><tr><th>When</th><th>Data</th><th>Run</th><th>Score</th><th>Cost</th></tr></thead>
        <tbody>{HISTORY.map(([when, data, run, sc, cost, tag]) => <tr key={when + run}>
          <td className="num">{when}{tag && <span className="tag ok" style={{ marginLeft: 8 }}>{tag}</span>}</td><td>{data}</td><td className="muted">{run}</td><td>{sc}</td><td className="num">{cost}</td>
        </tr>)}</tbody>
      </table></div>
    </section>

    <Disclosure title="Earlier run: the generated demo world" hint="Nine planted scenarios, the baselines and the verifier study (Oct 24, 2026)">
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
    </Disclosure>

    <section className="proof-callout"><div><span className="label">/ Safety boundary</span><h2>Zero is the only acceptable number for prohibited actions.</h2><p>The policy gate refuses money movement, outbound reminders outside approved templates, self-approval, and unverified bank-detail changes—before an agent’s text can become an action.</p></div><Link href="/guardrails" className="pill">Inspect the guardrails ↗</Link></section>
  </>;
}
