"use client";

import Link from "next/link";
import { useEffect, useState, type CSSProperties, type PointerEvent } from "react";
import { Spotlight } from "@/components/Spotlight";
import { Mark, Shield, ListIcon, Spark } from "@/components/Icons";
import "./home.css";

const Arrow = () => <span aria-hidden="true">↗</span>;

type SnapshotRow = { label: string; value: number; note: string; progress: string; tone: string };

const fallbackSnapshot: SnapshotRow[] = [
  { label: "Funded", value: 243710, note: "to Keyway", progress: "100%", tone: "funded" },
  { label: "Reconciled", value: 234600, note: "bank or vendor proof", progress: "96.3%", tone: "reconciled" },
  { label: "Proof needed", value: 3140, note: "3 invoices need a receipt", progress: "1.3%", tone: "proof" },
];

function makeSnapshot(feed: { headline?: { funded?: number; paid?: number; unexplained?: number; unexplained_items?: string[] } }): SnapshotRow[] {
  const funded = Number(feed.headline?.funded) || fallbackSnapshot[0].value;
  const paid = Number(feed.headline?.paid) || fallbackSnapshot[1].value;
  const proof = Number(feed.headline?.unexplained) || fallbackSnapshot[2].value;
  const proofCount = feed.headline?.unexplained_items?.length ?? 3;
  return [
    { label: "Funded", value: funded, note: "to Keyway", progress: "100%", tone: "funded" },
    { label: "Reconciled", value: paid, note: "bank or vendor proof", progress: `${((paid / funded) * 100).toFixed(1)}%`, tone: "reconciled" },
    { label: "Proof needed", value: proof, note: `${proofCount} invoice${proofCount === 1 ? "" : "s"} need a receipt`, progress: `${Math.max(1, (proof / funded) * 100).toFixed(1)}%`, tone: "proof" },
  ];
}

function HeroSnapshot() {
  const [snapshot, setSnapshot] = useState<SnapshotRow[]>(fallbackSnapshot);
  const [values, setValues] = useState(() => fallbackSnapshot.map(() => 0));
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let frame = 0;
    const animate = (next: SnapshotRow[]) => {
      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (reduce) { setValues(next.map((item) => item.value)); setReady(true); return; }
      const started = performance.now();
      const tick = (now: number) => {
        const progress = Math.min(1, (now - started) / 1200);
        const eased = 1 - Math.pow(1 - progress, 3);
        setValues(next.map((item) => Math.round(item.value * eased)));
        if (progress < 1) frame = requestAnimationFrame(tick); else setReady(true);
      };
      
      frame = requestAnimationFrame(tick);
    };
    const load = async () => {
      let next = fallbackSnapshot;
      try {
        const response = await fetch("/feed/state.json", { cache: "no-store" });
        if (response.ok) next = makeSnapshot(await response.json());
      } catch { /* the rendered fallback remains accurate for the demo */ }
      setSnapshot(next);
      animate(next);
    };
    load();
    return () => cancelAnimationFrame(frame);
  }, []);

  return <div className="hero-ledger" data-ready={ready} aria-label="Current reconciliation snapshot">
    <div className="ledger-head"><span>LIVE RECONCILIATION</span><i /></div>
    {snapshot.map((item, index) => <div className={`ledger-row ${item.tone}`} style={{ "--delay": `${160 + index * 140}ms`, "--progress": item.progress } as CSSProperties} key={item.label}>
      <div className="ledger-copy"><span>{item.label}</span><small>{item.note}</small></div>
      <div className="ledger-value"><strong>${values[index].toLocaleString("en-US")}</strong><div className="ledger-track"><i /></div></div>
    </div>)}
  </div>;
}

export default function Home() {
  const [corrected, setCorrected] = useState(false);
  const moveLiquid = (event: PointerEvent<HTMLElement>) => {
    const hero = event.currentTarget;
    const bounds = hero.getBoundingClientRect();
    hero.style.setProperty("--cursor-x", `${((event.clientX - bounds.left) / bounds.width) * 100}%`);
    hero.style.setProperty("--cursor-y", `${((event.clientY - bounds.top) / bounds.height) * 100}%`);
  };
  const resetLiquid = (event: PointerEvent<HTMLElement>) => {
    event.currentTarget.style.setProperty("--cursor-x", "74%");
    event.currentTarget.style.setProperty("--cursor-y", "34%");
  };
  return (
    <div className="home-page">
      <header className="home-nav">
        <Link href="/" className="home-logo"><Mark />AccountableAI<span className="home-beta">/ CAL HACKS</span></Link>
        <nav aria-label="Product"><a href="#how-it-works">How it works</a><a href="#proof">The proof</a><a href="#control">Your control</a></nav>
        <Link href="/overview" className="home-button home-button-dark">Open dashboard <Arrow /></Link>
      </header>

      <main>
        <section className="home-hero" aria-labelledby="home-title" onPointerMove={moveLiquid} onPointerLeave={resetLiquid}>
          <div className="hero-art" aria-hidden="true">
            <div className="hero-grid" />
            <div className="hero-liquid liquid-one" />
            <div className="hero-liquid liquid-two" />
          </div>
          <HeroSnapshot />
          <div className="hero-top"><div className="hero-top-actions"><span className="home-eyebrow">/ ACCOUNTABILITY, BUILT IN.</span><Link href="/overview" className="home-button hero-cta">See it in action <span className="button-orb"><Arrow /></span></Link></div></div>
          <div className="hero-copy">
            <div><h1 id="home-title">You sent the money.<br /><span>Did it reach them?</span></h1><p>AccountableAI follows your funding all the way to the vendor. It finds missing payments, checks the evidence, and brings you a clear decision before you send more.</p></div>
          </div>
          <div className="hero-cards" id="how-it-works">
            <article className="sugar-card"><span className="feature-number">01 / FOLLOW</span><h2><ListIcon />Every payment.</h2><p>Bring invoices, funding records, bank statements, and vendor replies into one payment trail.</p><div className="glass-tags"><span>Invoices</span><span>Bank records</span><span>Source links</span></div></article>
            <article className="sugar-card"><span className="feature-number">02 / QUESTION</span><h2><Spark />Every claim.</h2><p>“We paid” is a starting point. Agents look for proof, challenge conflicting records, and chase what’s missing.</p><div className="glass-tags"><span>Evidence checks</span><span>Follow-ups</span></div></article>
            <article className="sugar-card"><span className="feature-number">03 / DECIDE</span><h2><Shield />Your next move.</h2><p>Get a proposed hold with the evidence attached. A controller makes the call. No agent moves your money.</p><div className="glass-tags"><span>Human approval</span><span>Audit trail</span></div></article>
          </div>
        </section>

        <section className="home-proof" id="proof" aria-labelledby="proof-title">
          <div className="proof-copy"><span className="home-eyebrow">/ LESS GUESSWORK. MORE RECEIPTS.</span><h2 id="proof-title">The answer changes.<br /><span>The evidence says why.</span></h2><p>In our fictional demo, Oakline funds Keyway to pay its vendors. Three invoices appear overdue. But one new fact changes the picture.</p><p>Correct the electrician’s invoice date and watch the proposed hold update. The plumber and cleaner stay open—because their payment proof is still missing.</p><Link href="/console" className="home-text-link">Try your own fact in the Judge Console <Arrow /></Link></div>
          <div className="proof-demo">
            <div className="proof-demo-head"><span className="home-eyebrow">OAKLINE → KEYWAY</span><span className="sample-label">Illustrative demo</span></div>
            <div className="proof-amount"><span>Recommended hold</span><strong aria-live="polite">{corrected ? "$2,240" : "$3,140"}</strong><span>of the next $18,400 in funding</span></div>
            <div className="proof-invoice"><span><b>Rio Grande Plumbing</b><small>No bank or vendor proof</small></span><b>$1,240</b></div>
            <div className="proof-invoice"><span><b>Brightside Cleaning</b><small>No bank or vendor proof</small></span><b>$1,000</b></div>
            <div className={`proof-invoice ${corrected ? "invoice-corrected" : ""}`}><span><b>Sparkline Electric</b><small>{corrected ? "✓ Not due yet · received Apr 15" : "Receipt date recorded as Mar 28"}</small></span><b>{corrected ? "Cleared" : "$900"}</b></div>
            <button className="home-button proof-toggle" aria-pressed={corrected} onClick={() => setCorrected(!corrected)}>{corrected ? "Reset the example" : "Correct invoice date to Apr 15"}<span aria-hidden="true">{corrected ? "↺" : "→"}</span></button>
            <p className="proof-explanation" role="status">{corrected ? "$900 removed from the proposed hold. The other two invoices still need proof." : "Change one fact. See exactly which recommendation depends on it."}</p>
          </div>
        </section>

        <div id="control">
          <Spotlight className="home-control">
            <div className="control-heading"><span className="home-eyebrow">/ AGENTS DO THE LEGWORK.</span><h2>You keep<br /><span>the last word.</span></h2><p>From the first invoice to the final recommendation, every step stays connected to its sources.</p><Link href="/guardrails" className="home-button control-button">Explore the guardrails <Arrow /></Link></div>
            <div className="control-rules"><article><span className="rule-icon">↗</span><div><h3>Evidence you can open.</h3><p>Every alert cites a record. Click through to the source behind the claim.</p></div></article><article><span className="rule-icon">✓</span><div><h3>Claims that get challenged.</h3><p>The verifier checks citations and blocks unsupported or accusatory language.</p></div></article><article><span className="rule-icon">◎</span><div><h3>A human in the decision.</h3><p>Agents recommend. Controllers approve holds. Bank changes need identity verification.</p></div></article></div>
          </Spotlight>
        </div>

        <section className="home-closing"><span className="home-eyebrow">/ FOLLOW THE MONEY. FIND THE PROOF.</span><h2>Meet your new<br />second set of eyes.</h2><Link href="/overview" className="home-button home-button-dark">Open the live demo <Arrow /></Link><p>Fictional vendors. Real workflow. You’re in control.</p></section>
      </main>
      <footer className="home-footer"><Link href="/" className="home-logo"><Mark />AccountableAI</Link><span>Built at Cal Hacks · 2026</span><Link href="/overview">Enter dashboard ↗</Link></footer>
    </div>
  );
}
