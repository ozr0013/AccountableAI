"use client";

// Live investigation: feed the running engine one document (or move the clock) and watch it work.
// Everything shown comes back from the backend for that one action (dashboard/feed/session.py
// `_act`): the steps that ran (the map), what each agent did, every write to the event log, and the
// before/after of the books. The books are shared, so every other tab moves with them.

import Link from "next/link";
import { useEffect, useState } from "react";
import { EngineMap, useEngineMap } from "./EngineMap";
import { useFeed } from "./Feed";
import { money } from "@/lib/format";
import type { ActionResult, Books, Diff } from "@/lib/types";

type Sample = { file: string; label: string; flow: string; expect: string };
type Done = Extract<ActionResult, { ok: true }> & { label: string };

// "We paid" with no proof first, then the bank record that does move the money.
const ORDER = ["Keyway_Reply_KW-0901.eml", "Keyway_Bank_Export_Apr22.csv", "RioGrande_Payment_Received.eml", "Keyway_Owner_Statement_Apr2026.pdf"];
const rank = (file: string) => (ORDER.includes(file) ? ORDER.indexOf(file) : ORDER.length);
const WHAT: Record<string, string> = { reply: "Keyway reply", evidence: "Payment evidence", statement: "Keyway statement" };

// Before and after come from the backend, so another viewer acting meanwhile can't skew them.
const FIGS: { k: keyof Books; label: string; money: boolean }[] = [
  { k: "unexplained", label: "Unexplained", money: true },
  { k: "keyway", label: "Keyway still owes", money: true },
  { k: "open", label: "Open vendor balance", money: true },
  { k: "attention", label: "Review queue", money: false },
];

function deltaText(k: string, [a, b]: [unknown, unknown]): string {
  const word = (x: unknown) => (x == null || x === "" ? "none" : String(x).replace(/_/g, " "));
  if (k === "owed") return `owed ${money(Number(a))} → ${money(Number(b))}`;
  if (k === "alerts") return `alerts ${(a as string[]).length} → ${(b as string[]).length}`;
  return `${k} ${word(a)} → ${word(b)}`;
}

export function LiveDesk() {
  const { state, mode, busy, addFile, advance, reset } = useFeed();
  const live = mode === "live";
  const names = state?.meta.names ?? {};
  const named = (t: string) => t.replace(/\s+/g, " ").replace(/\((v-[a-z0-9-]+)\)/g, (m, id) => (names[id] ? `(${names[id]})` : m));
  const map = useEngineMap();
  const [samples, setSamples] = useState<Sample[]>([]);
  const [pick, setPick] = useState<string>(ORDER[0]);
  const [running, setRunning] = useState<string | null>(null);
  const [shown, setShown] = useState(0);
  const [cur, setCur] = useState<Done | null>(null);
  const [past, setPast] = useState<Done[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!live) return;
    fetch("/api/samples", { cache: "no-store" }).then((r) => r.json())
      .then((s: Sample[]) => setSamples([...s].sort((a, b) => rank(a.file) - rank(b.file))))
      .catch(() => setSamples([]));
    return () => map.stop();
  }, [live, map.stop]);

  const run = async (label: string, go: () => Promise<ActionResult>) => {
    if (!state || running) return;
    setRunning(label); setError(null); setShown(0); map.clear();
    if (cur) setPast((p) => [cur, ...p].slice(0, 6));
    setCur(null);
    const res = await go();
    if (!res.ok) { setError(res.error); setRunning(null); return; }
    const done: Done = { ...res, label };
    setCur(done);
    const total = (res.responses?.length ?? 0) + (res.writes?.notes.length ?? 0);
    await map.animate(
      { source: res.kind === "clock" ? "clock" : res.kind === "fact" ? "fact" : "file", kind: res.kind,
        steps: res.steps ?? [], agents: res.agents ?? {}, writes: res.writes ?? { total: 0, notes: [] } },
      (i, of) => setShown(Math.ceil((total * i) / of)),
    );
    setShown(total);
    setRunning(null);
  };

  const sample = samples.find((s) => s.file === pick);
  const responses = cur?.responses ?? [];
  const notes = cur?.writes?.notes ?? [];
  const settled = !!cur && !running;

  return (
    <section className="ld" aria-label="Live investigation">
      <div className="ld-feed">
        <div className="ld-feed-head">
          <span className="label muted">/ Feed the engine</span>
          <span className="ld-asof">Books as of {state ? new Date(state.meta.as_of + "T00:00:00Z").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }) : "…"}</span>
          <button className="pill" onClick={async () => { await reset(); setCur(null); setPast([]); map.clear(); }} disabled={!live || !!running || busy}>Reset books</button>
        </div>
        {!live && <p className="ld-note">Feeding the engine needs the live backend. Start it with <code>./demo.sh start</code>.</p>}
        <div className="ld-docs" role="radiogroup" aria-label="Document to send">
          {samples.map((s) => (
            <div key={s.file} role="radio" aria-checked={pick === s.file} tabIndex={0}
              className={`ld-doc${pick === s.file ? " on" : ""}`}
              onClick={() => setPick(s.file)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setPick(s.file); } }}>
              <span className="ld-doc-kind">{s.file.split(".").pop()?.toUpperCase()} · {WHAT[s.flow] ?? s.flow}</span>
              <b>{s.label}</b>
              <small>{s.expect}</small>
              <a href={`/api/samples/${encodeURIComponent(s.file)}`} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>Open the file ↗</a>
            </div>
          ))}
        </div>
        <div className="ld-run">
          <button className="btn" disabled={!live || !sample || !!running || busy}
            onClick={() => sample && run(sample.label, () => addFile(sample.file, true))}>
            {running && running !== "+1 day" && running !== "+5 days" ? "Engine working…" : "Send to the engine ↗"}
          </button>
          <span className="ld-or">or let time pass</span>
          <button className="pill" disabled={!live || !!running || busy} onClick={() => run("+1 day", () => advance(1, true))}>+1 day</button>
          <button className="pill" disabled={!live || !!running || busy} onClick={() => run("+5 days", () => advance(5, true))}>+5 days</button>
          <span className="ld-fine">Rules engine, no model cost · reminders are recorded, not emailed · every tab updates with the books</span>
        </div>
        {error && <p className="warn-ink ld-note">{error}</p>}
      </div>

      <div className="sim-body ld-body">
        <EngineMap map={map} label={cur ? cur.summary : "Waiting for a document"} />
        <div className="ld-side" data-lenis-prevent>
          <div className="ld-col">
            <h3>What the agents did</h3>
            {!cur && <p className="ld-empty">Pick a document and send it. Each line here is an agent acting on it, as recorded by the engine.</p>}
            <ol>{responses.slice(0, shown).map((r, i) => <li key={i}><b>{r.agent}</b><span>{named(r.what)}</span></li>)}</ol>
          </div>
          <div className="ld-col">
            <h3>Written to the event log {cur?.writes ? <em>{cur.writes.total} writes</em> : null}</h3>
            <ol>
              {notes.slice(0, Math.max(0, shown - responses.length)).map((n, i) => <li key={i} className={`w-${n.type}`}>{named(n.text)}</li>)}
              {settled && cur?.writes && cur.writes.total > notes.length && <li className="w-rest">+ {cur.writes.total - notes.length} bookkeeping records (routing, rechecks, citations)</li>}
            </ol>
          </div>
        </div>
      </div>

      {settled && cur && <Result r={cur} named={named} />}

      {past.length > 0 && (
        <div className="ld-past">
          <span className="label muted">/ Earlier this session</span>
          <ol>{past.map((p, i) => {
            const [a, b] = p.diff.unexplained;
            return <li key={i}><span>{p.label}</span><em>{named(p.summary)}</em><b>{a === b ? "no change" : `unexplained ${money(a)} → ${money(b)}`}</b></li>;
          })}</ol>
        </div>
      )}
    </section>
  );
}

function Result({ r, named }: { r: Done; named: (t: string) => string }) {
  const d: Diff = r.diff;
  const { before, after } = r.books ?? { before: null, after: null };
  const total = r.state.items.length;
  const flags = r.parsed?.flags ?? [];
  const moved = !!before && !!after && FIGS.some((f) => before[f.k] !== after[f.k]);
  return (
    <section className="ld-result" aria-live="polite">
      <div className="ld-result-head">
        <span className="label muted">/ What changed</span>
        <h2>{d.changed.length === 0 ? "Nothing in the books changed." : `${d.changed.length} invoice${d.changed.length === 1 ? "" : "s"} changed. ${d.untouched} of ${total} untouched.`}</h2>
        <p>{named(r.summary)}{flags.length ? ` The reader noticed: ${flags.join("; ")}.` : ""}</p>
      </div>
      <div className="ld-figs">
        {before && after && FIGS.map((f) => {
          const a = after[f.k], b = before[f.k], same = a === b;
          const show = (v: number) => (f.money ? money(v) : String(v));
          return <div key={f.k} className={same ? "" : "moved"}><span>{f.label}</span><strong>{show(a)}</strong><small>{same ? "no change" : `was ${show(b)}`}</small></div>;
        })}
      </div>
      <div className="ld-detail">
        <div>
          <h3>Invoices</h3>
          {d.changed.length === 0 && <p className="muted">None changed{d.rechecked_unchanged.length ? `. Rechecked and unchanged: ${d.rechecked_unchanged.join(", ")}` : ""}.</p>}
          <ul>{d.changed.map((c) => <li key={c.id}><Link href={`/items/${c.id}`}><b>{c.vendor}</b> <span className="mono">{c.id}</span> ↗</Link><small>{Object.entries(c.delta).map(([k, v]) => deltaText(k, v)).join(" · ")}</small></li>)}</ul>
        </div>
        <div>
          <h3>Alerts</h3>
          {!d.raised.length && !d.withdrawn.length && <p className="muted">None raised or withdrawn{d.kept.length ? `; ${d.kept.length} still open` : ""}.</p>}
          <ul>
            {d.raised.map((a) => <li key={a.id} className="raised"><b>Raised</b><small>{named(a.headline)}</small></li>)}
            {d.withdrawn.map((a) => <li key={a.id} className="withdrawn"><b>Withdrawn</b><small>{named(a.headline)}</small></li>)}
          </ul>
        </div>
      </div>
      <p className="ld-tabs">{moved || d.changed.length ? "Every tab already shows this:" : "Every tab still shows the same books:"} <Link href="/overview">Overview ↗</Link><Link href="/cases">Review queue ↗</Link><Link href="/invoices">Invoices ↗</Link><Link href="/properties">Properties ↗</Link></p>
    </section>
  );
}
