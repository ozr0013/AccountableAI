"use client";

import Link from "next/link";
import { useState } from "react";
import { useFeed } from "@/components/Feed";
import { Badge, Loading, PageTop, SectionTitle } from "@/components/ui";
import { ALERT, day, money, named } from "@/lib/format";
import type { ConsoleResult } from "@/lib/types";

const FIELD: Record<string, string> = { state: "status", cause: "cause", due: "due", received: "received", owed: "owed", alerts: "alerts" };

function fmt(k: string, v: unknown): string {
  if (v == null) return "none";
  if (k === "due" || k === "received") return day(String(v));
  if (k === "owed") return money(Number(v));
  if (k === "alerts") return `${(v as string[]).filter((a) => !a.startsWith("al-recurring")).length} item alert(s)`;
  return String(v).replaceAll("_", " ");
}

export default function Console() {
  const { state, presets, runConsole, reset, mode, last } = useFeed();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<ConsoleResult | null>(last);
  if (!state) return <><PageTop title="Judge Console" /><Loading /></>;
  const names = state.meta.names;

  const run = async (q: { preset?: string; text?: string }) => {
    setBusy(true);
    try { setRes(await runConsole(q)); } catch (e) { setRes({ ok: false, error: String(e) }); }
    setBusy(false);
  };
  const d = res?.ok ? res.diff : null;

  return (
    <>
      <PageTop title="Judge Console" sub="Play Keyway, or add one real fact. The system rechecks only what depends on it and shows what changed and what didn't.">
        <button className="pill" onClick={async () => { await reset(); setRes(null); }}>Reset demo</button>
      </PageTop>

      <div className="grid g-main">
        <div className="stack" style={{ gap: 16 }}>
          <div className="card">
            <p className="card-label">Say it in plain words</p>
            <form className="row" style={{ gap: 8, marginTop: 10 }} onSubmit={(e) => { e.preventDefault(); if (text.trim()) run({ text }); }}>
              <input className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="the electrician's invoice arrived Apr 15" aria-label="Fact for the Judge Console" />
              <button className="btn" disabled={busy || !text.trim()}>Inject fact</button>
            </form>
            <div className="row" style={{ marginTop: 10, flexWrap: "wrap" }}>
              {["we paid the electrician", "the electrician's invoice arrived Apr 15", "Rio Grande confirms it was paid Apr 22"].map((s) => (
                <button key={s} className="cite" style={{ fontSize: 11, height: 22 }} onClick={() => setText(s)}>{s}</button>
              ))}
            </div>
            <p className="micro muted" style={{ marginTop: 10, textTransform: "none", letterSpacing: 0 }}>
              A constrained parser maps text onto a known event. If it can&apos;t, it says so and suggests one. It never guesses.
            </p>
          </div>

          {res && !res.ok && <div className="notice err">{res.error}{res.suggestions?.length ? <div className="micro" style={{ marginTop: 6 }}>Try: {res.suggestions.slice(0, 6).join(", ")}</div> : null}</div>}

          {res?.ok && d && (
            <div className="card pulse" style={{ padding: 0 }}>
              <div style={{ padding: 24, borderBottom: "1px solid var(--n3)" }}>
                <span className="label muted">/ New fact</span>
                <p className="lead" style={{ margin: "10px 0 0", color: "var(--ink)" }}>{named(res.summary, names)}</p>
              </div>
              <div className="grid g3" style={{ padding: 24, gap: 24, borderBottom: "1px solid var(--n3)" }}>
                <div><div className="micro muted">Rechecked</div><div className="display h2 num">{d.rechecked.length}</div><div className="micro muted">{d.rechecked.join(", ") || "none"} · from dependents_of()</div></div>
                <div><div className="micro muted">Changed</div><div className="display h2 num warn">{d.changed.length}</div><div className="micro muted">items whose cause or status moved</div></div>
                <div><div className="micro muted">Left alone</div><div className="display h2 num ok">{d.untouched}</div><div className="micro muted">items not rerun</div></div>
              </div>
              <div style={{ padding: 24 }}>
                <div className="stack" style={{ gap: 18 }}>
                  {d.changed.map((c) => (
                    <div key={c.id}>
                      <div className="row" style={{ marginBottom: 8 }}><span className="h4 display">{c.vendor}</span><Link className="cite" href={`/items/${c.id}`}>{c.id}</Link></div>
                      <div className="diff">
                        {Object.entries(c.delta).map(([k, [a, b]]) => (
                          <div key={k} className="row" style={{ gap: 6, flexWrap: "wrap" }}>
                            <span className="minus">{FIELD[k] ?? k} {fmt(k, a)}</span>
                            <span className="plus">{FIELD[k] ?? k} {fmt(k, b)}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                  {res.trace.filter((t) => t.startsWith("reply read:")).map((t) => (
                    <div key={t} className="arow" style={{ alignItems: "flex-start" }}>
                      <Badge kind="flag" />
                      <div className="arow-main"><span className="arow-title">Reply read</span><span className="body" style={{ fontSize: 13 }}>{t.replace("reply read: ", "")}. The alert stays open.</span></div>
                    </div>
                  ))}
                  {d.changed.length === 0 && <div className="notice">Nothing changed. {d.rechecked_unchanged.length ? `${d.rechecked_unchanged.join(", ")} rechecked and still holds.` : ""}</div>}
                  {d.withdrawn.map((a) => (
                    <div key={a.id} className="arow" style={{ alignItems: "flex-start" }}>
                      <Badge kind="done" />
                      <div className="arow-main"><span className="arow-title">Withdrew: {ALERT[a.kind]} on {a.invoice_ids.join(", ")}</span><span className="body" style={{ fontSize: 13 }}>{named(a.withdrawn_reason, names)}</span></div>
                    </div>
                  ))}
                  {d.raised.map((a) => (
                    <div key={a.id} className="arow"><Badge kind="flag" /><div className="arow-main"><span className="arow-title">Raised: {named(a.headline, names)}</span></div></div>
                  ))}
                </div>
              </div>
              <div className="moment" style={{ borderRadius: "0 0 8px 8px", gridTemplateColumns: "1fr auto" }}>
                <div>
                  <span className="label">/ Unexplained, still owed by Keyway</span>
                  <div className="moment-amount num" style={{ marginTop: 12, fontSize: 48, lineHeight: "48px" }}>
                    {d.unexplained[0] !== d.unexplained[1] && <span className="strike" style={{ opacity: .45, marginRight: 14 }}>{money(d.unexplained[0], false)}</span>}
                    {money(d.unexplained[1], false)}
                  </div>
                  <p style={{ fontSize: 14, lineHeight: "20px" }}>Kept: {d.unexplained_items[1].map((i) => state.items.find((x) => x.id === i)?.vendor ?? i).join(", ") || "nothing"}.</p>
                </div>
                <div className="moment-actions"><Link href="/cases" className="btn on-wine">Updated case</Link><Link href="/" className="pill on-wine">Overview</Link></div>
              </div>
            </div>
          )}
          {res?.ok && res.trace.length > 0 && (
            <details className="card flat">
              <summary className="label muted" style={{ cursor: "pointer" }}>Workflow trace ({res.trace.length} lines)</summary>
              <pre className="mono" style={{ fontSize: 11, lineHeight: "17px", whiteSpace: "pre-wrap", margin: "12px 0 0", color: "var(--n10)" }}>{res.trace.join("\n")}</pre>
            </details>
          )}
        </div>

        <div className="stack" style={{ gap: 10 }}>
          <SectionTitle right={mode === "replay" ? "replay: moment only" : `${presets.length}`}>Presets</SectionTitle>
          {presets.map((p) => (
            <button key={p.id} className={`preset ${p.id === "invoice_received_correction" ? "featured" : ""}`} disabled={busy} onClick={() => run({ preset: p.id })}>
              <div className="between"><span className="h4 display">{p.label}</span>{p.id === "invoice_received_correction" && <span className="tag">The moment</span>}</div>
              <span className="body muted" style={{ fontSize: 13 }}>{named(p.description, names)}</span>
              <span className="micro faint">{Object.entries(p.defaults).map(([k, v]) => `${k}=${v}`).join(" · ")}</span>
            </button>
          ))}
          {state.history.length > 0 && (
            <>
              <SectionTitle right={`${state.history.length}`}>Facts so far</SectionTitle>
              {state.history.map((h, i) => (
                <div key={i} className="doc"><span className="micro muted">#{i + 1}</span> {named(h.summary, names)}</div>
              ))}
            </>
          )}
        </div>
      </div>
    </>
  );
}
