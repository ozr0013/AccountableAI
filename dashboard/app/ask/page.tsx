"use client";

import { useState } from "react";
import { useFeed } from "@/components/Feed";
import { Cites, Loading, PageTop, SectionTitle } from "@/components/ui";
import { named } from "@/lib/format";
import type { Briefing } from "@/lib/types";

const SUGGESTED = ["What's open with Keyway?", "Why are we holding money from Keyway?", "Is any of this not Keyway's fault?"];

export default function Ask() {
  const { state, brief, mode } = useFeed();
  const [q, setQ] = useState(SUGGESTED[0]);
  const [b, setB] = useState<Briefing | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!state) return <><PageTop title="Ask about Keyway" /><Loading /></>;
  const names = state.meta.names;

  const ask = async (question: string) => {
    setQ(question); setBusy(true); setErr(null);
    try { setB(await brief(question)); } catch (e) { setErr(String(e)); }
    setBusy(false);
  };

  return (
    <>
      <PageTop title="Ask about Keyway" sub="Answered from the system's own record. Every claim cites a source; uncited or accusatory claims are dropped." />
      <div className="card" style={{ padding: 24 }}>
        <form className="row" style={{ gap: 8 }} onSubmit={(e) => { e.preventDefault(); if (q.trim()) ask(q); }}>
          <input className="input" style={{ height: 52, fontSize: 18 }} value={q} onChange={(e) => setQ(e.target.value)} aria-label="Question about Keyway" />
          <button className="btn" style={{ height: 52, padding: "0 24px" }} disabled={busy}>{busy ? "Reading the record" : "Ask"}</button>
        </form>
        <div className="row" style={{ marginTop: 12, flexWrap: "wrap" }}>
          {SUGGESTED.map((s) => <button key={s} className="kicker" style={{ border: 0, cursor: "pointer" }} onClick={() => ask(s)}>{s}</button>)}
        </div>
        {mode === "replay" && <p className="micro muted" style={{ marginTop: 12 }}>Replay feed: answers to the suggested questions, before or after the moment.</p>}
      </div>

      {err && <div className="notice err" style={{ marginTop: 16 }}>{err}</div>}
      {b && (
        <div className="grid g-main section" style={{ marginTop: 24 }}>
          <div>
            <SectionTitle right={String(b.meta?.mode ?? "code") + " briefing"}>Answer</SectionTitle>
            <div className="card" style={{ padding: 28 }}>
              <p className="lead" style={{ margin: 0, color: "var(--ink)", fontSize: 20, lineHeight: "30px" }}>{named(b.answer, names)}</p>
            </div>
            <SectionTitle right={`${b.claims.length}`}>Claims and sources</SectionTitle>
            <div className="stack" style={{ gap: 0 }}>
              {b.claims.map((c, i) => (
                <div key={i} className="between" style={{ alignItems: "flex-start", gap: 24, padding: "14px 0", borderBottom: ".5px solid var(--n5)" }}>
                  <span className="body" style={{ color: "var(--ink)" }}>{named(c.text, names)}</span>
                  <div style={{ textAlign: "right", flex: "none", maxWidth: "45%" }}><Cites cites={c.citations} /></div>
                </div>
              ))}
            </div>
          </div>
          <div>
            <SectionTitle>Prior rulings</SectionTitle>
            <div className="stack" style={{ gap: 8 }}>
              {b.prior_rulings.length === 0 && <div className="body muted">None yet.</div>}
              {b.prior_rulings.map((r, i) => <div key={i} className="doc">{named(r, names)}</div>)}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
