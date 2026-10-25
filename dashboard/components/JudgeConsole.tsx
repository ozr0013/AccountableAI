"use client";

// Judge Console: give the running engine something (paperwork, an email you write, a fact, or
// time) and watch each part of it wake up, in order, as it really ran (components/EngineFlow plans
// the cards from the action's own timeline). The books are shared: every tab moves with them.

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { type Card, EngineFlow, plan, stageOf } from "./EngineFlow";
import { useFeed } from "./Feed";
import { money } from "@/lib/format";
import type { ActionResult, Books, Diff } from "@/lib/types";
import { type LiveCard, useAgentConsole } from "./useAgentConsole";

type Ok = Extract<ActionResult, { ok: true }>;
type Sample = { file: string; label: string; flow: string; expect: string };
type Tab = "paper" | "email" | "fact" | "time";
type Run = Ok & { label: string };

// "We paid" with no proof first, then the bank record that does move the money.
const ORDER = ["Keyway_Reply_KW-0901.eml", "Keyway_Bank_Export_Apr22.csv", "RioGrande_Payment_Received.eml", "Keyway_Owner_Statement_Apr2026.pdf"];
const rank = (file: string) => (ORDER.includes(file) ? ORDER.indexOf(file) : ORDER.length);
const FACTS = ["the electrician's invoice arrived Apr 15", "Rio Grande Plumbing confirmed they were paid", "Keyway says they paid the electrician"];
const EMAILS = [
  { sender: "keyway", subject: "Re: Payment reminder KW-0402", body: "Hi, Sparkline Electric was paid for KW-0402 last week. Thanks, Keyway AP" },
  { sender: "v-riogrande", subject: "Payment received - KW-0301", body: "Hi Keyway team, we received your payment of $1,240.00 for KW-0301 today. Thank you." },
];
// the same figures, by the same rules, as Overview's boxes (session.py books, lib/balances.ts)
const FIGS: { k: keyof Books; label: string; money: boolean }[] = [
  { k: "paid", label: "Paid", money: true },
  { k: "left", label: "Outstanding", money: true },
  { k: "late", label: "Past due", money: true },
  { k: "attention", label: "Review queue", money: false },
];
const BUDGET = 9000;                     // an action plays in about nine seconds at most

export function JudgeConsole() {
  const { state, mode, busy, addFile, advance, runConsole, write, upload, reset, refresh } = useFeed();
  // Paperwork goes to the real agents (Claude) through the agent API; see useAgentConsole.
  const agent = useAgentConsole();
  const agentRun = agent.run;
  const agentOff = agent.busy || !agent.connected;
  const live = mode === "live";
  const names = state?.meta.names ?? {};
  const named = (t: string) => t.replace(/\s+/g, " ").replace(/\((v-[a-z0-9-]+)\)/g, (m, id) => (names[id] ? `(${names[id]})` : m));

  const [tab, setTab] = useState<Tab>("paper");
  const [samples, setSamples] = useState<Sample[]>([]);
  const [pick, setPick] = useState(ORDER[0]);
  const [fact, setFact] = useState(FACTS[0]);
  const [mail, setMail] = useState(EMAILS[0]);
  const [error, setError] = useState<{ text: string; hints?: string[] } | null>(null);

  const [run, setRun] = useState<Run | null>(null);
  const [cards, setCards] = useState<Card[]>([]);
  const [shown, setShown] = useState(0);            // cards on screen
  const [doneN, setDoneN] = useState(0);            // cards finished
  const [lit, setLit] = useState<Set<string>>(new Set());
  const [active, setActive] = useState<Set<string>>(new Set());
  const [stages, setStages] = useState<Set<string>>(new Set());
  const [playing, setPlaying] = useState(false);
  const [past, setPast] = useState<Run[]>([]);
  const [auto, setAuto] = useState<string | null>(null);
  const skip = useRef(false);
  const halt = useRef(false);
  const waiters = useRef(new Set<() => void>());
  const feedRef = useRef<HTMLOListElement>(null);
  const runRef = useRef<Run | null>(null);

  useEffect(() => {
    if (!live) return;
    fetch("/api/samples", { cache: "no-store" }).then((r) => r.json())
      .then((s: Sample[]) => setSamples([...s].sort((a, b) => rank(a.file) - rank(b.file)))).catch(() => setSamples([]));
  }, [live]);
  useEffect(() => () => { halt.current = true; waiters.current.forEach((w) => w()); }, []);
  useEffect(() => { feedRef.current?.lastElementChild?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [shown, doneN]);

  const vendors = useMemo(() => {
    const seen = new Map<string, string>();
    for (const i of state?.items ?? []) if (!seen.has(i.vendor_id)) seen.set(i.vendor_id, i.vendor);
    return [...seen].sort((a, b) => a[1].localeCompare(b[1]));
  }, [state]);
  const openInvoices = (state?.items ?? []).filter((i) => i.owed > 0 && !i.duplicate_of).sort((a, b) => b.owed - a.owed).slice(0, 8);

  const wait = (ms: number) => new Promise<void>((res) => {
    if (skip.current || halt.current) return res();
    const done = () => { window.clearTimeout(id); waiters.current.delete(done); res(); };
    const id = window.setTimeout(done, ms);
    waiters.current.add(done);
  });

  /** Play one action's real run: each card wakes up, works, and hands on to the next. */
  const play = async (r: Run) => {
    const cs = plan(r);
    const scale = Math.min(1, BUDGET / cs.reduce((t, c) => t + c.ms, 0));
    skip.current = false;
    const prev = runRef.current;
    if (prev) setPast((p) => [prev, ...p].slice(0, 12));
    runRef.current = r;
    setRun(r); setCards(cs); setShown(0); setDoneN(0); setLit(new Set()); setActive(new Set()); setStages(new Set()); setPlaying(true);
    for (const [i, c] of cs.entries()) {
      if (halt.current) return;
      setShown(i + 1);
      setActive(new Set(c.light));
      setLit((old) => new Set([...old, ...c.light]));
      await wait(c.ms * scale * 0.45);
      setDoneN(i + 1);
      if (c.stage) setStages((s) => new Set([...s, c.stage!]));
      await wait(c.ms * scale * 0.55);
    }
    setActive(new Set());
    setStages(new Set(["source", "owner", "email", "controller", "end"]));
    setPlaying(false);
  };

  const send = async (label: string, go: () => Promise<ActionResult>) => {
    if (playing || busy || !live) return;
    setError(null); halt.current = false;
    const res = await go();
    if (!res.ok) { setError({ text: res.error, hints: res.suggestions }); return; }
    await play({ ...res, label });
  };

  const fileIn = async (f: File | undefined) => {
    if (!f) return;
    if (tab === "paper") {                     // the agents read it, like the samples
      const data = await new Promise<string>((res, rej) => {
        const fr = new FileReader();
        fr.onload = () => res(String(fr.result).split(",")[1] ?? "");
        fr.onerror = () => rej(fr.error);
        fr.readAsDataURL(f);
      });
      setRun(null); setCards([]);
      await agent.sendUpload(f.name, data);
      return;
    }
    const data = await new Promise<string>((res, rej) => {
      const fr = new FileReader();
      fr.onload = () => res(String(fr.result).split(",")[1] ?? "");
      fr.onerror = () => rej(fr.error);
      fr.readAsDataURL(f);
    });
    await send(`Your file: ${f.name}`, async () => {
      const res = await upload(f.name, data);
      return res.ok || !/^Nothing usable/.test(res.error) ? res
        : { ...res, error: `The reader read ${f.name} but found nothing it could match to Oakline's books: no invoice, vendor or payment these books know. Outside paperwork usually names other invoices. Try “Write an email” and name one of the open invoices.` };
    });
  };

  const fifteen = async () => {
    if (playing || busy || !live) return;
    setError(null); halt.current = false;
    try {
      const script: { index: number; day: number; label: string }[] = await (await fetch("/api/simulation", { cache: "no-store" })).json();
      setPast([]); setRun(null); runRef.current = null;
      for (const st of script) {
        if (halt.current) break;
        setAuto(`Day ${String(st.day).padStart(2, "0")} of 15 · ${st.label}`);
        const res = await (await fetch(`/api/simulation/${st.index}`, { method: "POST" })).json();
        if (!res.ok) throw new Error(res.error ?? res.detail ?? `step ${st.index + 1} failed`);
        await play({ ...res, label: `Day ${st.day}: ${st.label}` });
        await wait(700);
      }
    } catch (e) {
      setError({ text: String(e instanceof Error ? e.message : e) });
    }
    setAuto(null);
    await refresh();
  };

  const card = (c: Card, i: number) => {
    const running = i >= doneN;
    return (
      <li key={c.key} className={`jc-card t-${c.tone} s-${c.status}${running ? " running" : ""}`}>
        <span className="jc-node" aria-hidden="true" />
        <div className="jc-card-head">
          <b>{c.who}</b>
          <span className="jc-status">{running ? "working…" : { done: "done", held: "holding", blocked: "blocked", allowed: "allowed", raised: "raised", withdrawn: "withdrawn", quiet: "nothing to send" }[c.status]}</span>
        </div>
        {c.question && <p className="jc-q">{c.question}</p>}
        {!running && <>
          <p className="jc-title">{named(c.title)}</p>
          {c.lines && c.lines.length > 0 && <ul className="jc-lines">{c.lines.map((l, k) => (
            <li key={k} className={`k-${l.kind}`}>{named(l.text)}{l.cites.length > 0 && <span className="jc-cite">{l.cites.map((ct) => [ct.source.replace(/^raw:inbox\//, ""), ct.locator].filter(Boolean).join(" ")).slice(0, 2).join(" · ")}</span>}</li>
          ))}</ul>}
          {c.rows && <ul className="jc-rows">{c.rows.map((rw, k) => (
            <li key={k} className={rw.open ? "open" : ""}><span className="mono">{rw.id}</span><span>{named(rw.text)}</span>
              {rw.open && rw.lines && rw.lines.length > 0 && <ul className="jc-lines">{rw.lines.map((l, m) => <li key={m} className={`k-${l.kind}`}>{named(l.text)}</li>)}</ul>}
            </li>
          ))}</ul>}
          {c.meta && <p className="jc-meta">{c.meta}</p>}
        </>}
      </li>
    );
  };

  const liveCard = (c: LiveCard) => (
    <li key={c.key} className={`jc-card t-${c.tone} s-${c.status === "sent" ? "done" : c.status === "failed" ? "blocked" : c.status}${c.status === "working" ? " running" : ""}`}>
      <span className="jc-node" aria-hidden="true" />
      <div className="jc-card-head">
        <b>{c.who}</b>
        <span className="jc-status">{{ working: "working…", done: "done", held: "holding", raised: "raised", sent: "sent", failed: "failed", quiet: "nothing to send" }[c.status]}</span>
      </div>
      {c.question && <p className="jc-q">{c.question}</p>}
      <p className="jc-title">{named(c.title)}</p>
      {c.meta && <p className="jc-meta">{c.meta}</p>}
      {c.lines.length > 0 && <ul className="jc-lines">{c.lines.map((l, k) => (
        <li key={k} className={`k-${l.kind === "sql" ? "check" : l.kind}`}>
          {l.kind === "thinking" ? <em>{l.text.length > 600 ? "…" + l.text.slice(-600) : l.text}</em> : named(l.text)}
          {l.kind === "sql" && !!l.rows?.length && <span className="jc-cite" style={{ display: "block", marginTop: 2 }}>{l.rows.slice(0, 3).map((r) => r.map((v) => (v == null ? "—" : String(v))).join(" · ")).join("  |  ")}</span>}
        </li>))}</ul>}
    </li>
  );

  const notes = run?.writes?.notes ?? [];
  const seen = notes.filter((n) => stages.has(stageOf(n.type)));
  const books = run?.books;
  const settled = !!run && !playing;
  const off = !live || playing || busy;

  return (
    <section className="jc" aria-label="Judge Console">
      <div className="jc-feed">
        <div className="jc-feed-head">
          <div className="jc-tabs" role="tablist" aria-label="What to give the engine">
            {([["paper", "Paperwork"], ["email", "Write an email"], ["fact", "Tell it a fact"], ["time", "Let time pass"]] as [Tab, string][]).map(([id, label]) => (
              <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "on" : ""} onClick={() => setTab(id)}>{label}</button>
            ))}
          </div>
          <span className="jc-asof">Books as of {agentRun?.day ? new Date(agentRun.day + "T00:00:00Z").toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" }) : state ? new Date(state.meta.as_of + "T00:00:00Z").toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" }) : "…"}</span>
          <button className="pill" disabled={off || agent.busy} onClick={async () => { await Promise.all([reset(), agent.resetBooks()]); runRef.current = null; setRun(null); setCards([]); setPast([]); setLit(new Set()); }}>Reset books</button>
        </div>
        {!live && <p className="jc-note">The console needs the live backend. Start it with <code>./demo.sh start</code>.</p>}

        {tab === "paper" && <div className="jc-pane">
          <div className="jc-docs" role="radiogroup" aria-label="Paperwork">
            {samples.map((s) => (
              <button key={s.file} role="radio" aria-checked={pick === s.file} className={`jc-doc${pick === s.file ? " on" : ""}`} onClick={() => setPick(s.file)}>
                <span className="jc-kind">{s.file.split(".").pop()?.toUpperCase()}</span>
                <b>{s.label}</b><small>{s.expect}</small>
              </button>
            ))}
          </div>
          <div className="jc-go">
            <button className="btn" disabled={agentOff || !samples.length} onClick={() => { const s = samples.find((x) => x.file === pick); if (s) { setRun(null); setCards([]); void agent.sendSample(s.file, s.label); } }}>Send to the agents ↗</button>
            <a className="jc-link" href={`/api/samples/${encodeURIComponent(pick)}`} target="_blank" rel="noreferrer">Open this file ↗</a>
            <label className={`pill jc-upload${agentOff ? " off" : ""}`}>Or bring your own (PDF, .eml, bank CSV)
              <input type="file" accept=".pdf,.eml,.csv" disabled={agentOff} onChange={(e) => { void fileIn(e.target.files?.[0]); e.target.value = ""; }} />
            </label>
          </div>
          <p className="jc-fine">Paperwork goes to the AI agents: the reader files it in the books, the checks run, and the agents (Claude) decide, live. {agent.connected ? "" : "Waiting for the agent API on :8773…"}</p>
        </div>}

        {tab === "email" && <div className="jc-pane jc-email">
          <div className="jc-email-form">
            <label>From<select value={mail.sender} disabled={off} onChange={(e) => setMail({ ...mail, sender: e.target.value })}>
              <option value="keyway">Keyway Management (the property manager)</option>
              {vendors.map(([id, n]) => <option key={id} value={id}>{n} (vendor)</option>)}
            </select></label>
            <label>Subject<input className="input" value={mail.subject} disabled={off} onChange={(e) => setMail({ ...mail, subject: e.target.value })} /></label>
            <label>Message<textarea className="input" rows={4} value={mail.body} disabled={off} onChange={(e) => setMail({ ...mail, body: e.target.value })} /></label>
            <div className="jc-go">
              <button className="btn" disabled={agentOff || !mail.body.trim()} onClick={() => { setRun(null); setCards([]); void agent.sendEmail(mail.sender, mail.subject, mail.body, `Email from ${mail.sender === "keyway" ? "Keyway Management" : vendors.find(([id]) => id === mail.sender)?.[1] ?? mail.sender}`); }}>Send the email ↗</button>
              {EMAILS.map((e, k) => <button key={k} className="jc-chip" disabled={agentOff} onClick={() => setMail(e)}>{k === 0 ? "Keyway Management: “we paid”" : "Vendor: “we were paid”"}</button>)}
            </div>
          </div>
          <div className="jc-hint">
            <span className="label muted">/ Open invoices in these books</span>
            <ul>{openInvoices.map((i) => <li key={i.id}><span className="mono">{i.id.replace("INV-", "")}</span>{i.vendor}<b>{money(i.owed)}</b></li>)}</ul>
            <small>Name an invoice (like KW-0402) so the reader can match it.</small>
          </div>
        </div>}

        {tab === "fact" && <div className="jc-pane">
          <div className="jc-go">
            <input className="input jc-fact" value={fact} disabled={agentOff} onChange={(e) => setFact(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && fact.trim()) { setRun(null); setCards([]); void agent.tellFact(fact); } }} aria-label="A fact in plain words" />
            <button className="btn" disabled={agentOff || !fact.trim()} onClick={() => { setRun(null); setCards([]); void agent.tellFact(fact); }}>Tell the agents ↗</button>
          </div>
          <div className="jc-go">{FACTS.map((f) => <button key={f} className="jc-chip" disabled={agentOff} onClick={() => setFact(f)}>{f}</button>)}</div>
          <p className="jc-fine">A fact is a claim, not evidence: the investigator (Claude) checks it against the books and flags only what the documents prove.</p>
        </div>}

        {tab === "time" && <div className="jc-pane">
          <div className="jc-go">
            <button className="btn" disabled={agentOff} onClick={() => { setRun(null); setCards([]); void agent.advance(1); }}>+1 day</button>
            <button className="btn" disabled={agentOff} onClick={() => { setRun(null); setCards([]); void agent.advance(5); }}>+5 days</button>
            <button className="pill" disabled={agentOff} onClick={() => { setRun(null); setCards([]); void agent.play(15, (i) => setAuto(`Day ${i} of 15`)).then(() => setAuto(null)); }}>Play 15 days on its own</button>
            {auto && <button className="pill" onClick={() => agent.stop()}>Stop after this day</button>}
          </div>
          <p className="jc-fine">Nobody prompts the agents: each day new paperwork arrives, the investigator sweeps the books, and the chaser follows up on its own schedule. Each day is real Claude work (roughly $0.10–0.50 a day).</p>
        </div>}
        {error && <div className="jc-note warn-ink">{error.text}{error.hints?.length ? <> {error.hints.slice(0, 3).map((h) => <button key={h} className="jc-chip" onClick={() => { setTab("fact"); setFact(h); }}>{h}</button>)}</> : null}</div>}
      </div>

      <div className="jc-now">
        {auto && <b>{auto}</b>}
        {agentRun ? <span>{agentRun.status === "running" ? `${agentRun.label}: the agents are working…` : agentRun.summary || agentRun.error}</span>
          : run ? <span>{named(run.summary)}</span> : <span>The engine is waiting. Give it something above.</span>}
        {run?.ran_ms != null && <em>engine ran in {run.ran_ms} ms · shown step by step</em>}
        {playing && <button className="pill" onClick={() => { skip.current = true; waiters.current.forEach((w) => w()); }}>Skip to result</button>}
        {playing && auto && <button className="pill" onClick={() => { halt.current = true; skip.current = true; waiters.current.forEach((w) => w()); }}>Stop</button>}
      </div>

      <div className="jc-map"><EngineFlow lit={agentRun ? agent.lit : lit} active={agentRun ? agent.active : active} label={agentRun ? agentRun.label : run ? run.summary : "Waiting"} /></div>

      <div className="jc-body">
        <div className="jc-col">
          <h3>Agents, as they wake up</h3>
          {agentRun && <ol className="jc-cards" data-lenis-prevent>{agent.cards.map(liveCard)}</ol>}
          {!agentRun && !cards.length && <p className="jc-empty">Each part of the engine shows up here when it’s triggered: what it was asked, what it checked, and what it decided.</p>}
          {!agentRun && <ol className="jc-cards" ref={feedRef} data-lenis-prevent>{cards.slice(0, shown).map(card)}</ol>}
        </div>
        <div className="jc-col jc-data">
          <h3>The books</h3>
          {agentRun ? <div className="jc-figs">{FIGS.map((f) => {
            const b = agentRun.books;
            const a = b?.after[f.k as keyof typeof b.after], was = b?.before[f.k as keyof typeof b.before];
            const show = (v: number) => (f.money ? money(v) : String(v));
            const moved = a != null && was != null && a !== was;
            return <div key={f.k} className={moved ? "moved" : ""}><span>{f.label}</span><strong>{a == null ? "—" : show(a)}</strong><small>{moved ? `was ${show(was!)}` : b ? "no change" : agentRun.status === "running" ? "…" : ""}</small></div>;
          })}</div> : <div className="jc-figs">{FIGS.map((f) => {
            const show = (v: number) => (f.money ? money(v) : String(v));
            const a = books?.after[f.k], b = books?.before[f.k];
            const cur = settled ? a : b;
            const moved = settled && a !== b;
            return <div key={f.k} className={moved ? "moved" : ""}><span>{f.label}</span><strong>{cur == null ? "—" : show(cur)}</strong><small>{moved && b != null ? `was ${show(b)}` : settled ? "no change" : playing ? "…" : ""}</small></div>;
          })}</div>}
          <h3>Written to the event log {run?.writes ? <em>{seen.length} of {notes.length}</em> : null}</h3>
          {agentRun && <ol className="jc-writes" data-lenis-prevent>{agentRun.log.map((t, i) => <li key={i}>{t}</li>)}</ol>}
          {!agentRun && <ol className="jc-writes" data-lenis-prevent>
            {seen.map((n, i) => <li key={i} className={`w-${n.type}`}>{named(n.text)}</li>)}
            {settled && run?.writes && run.writes.total > notes.length && <li className="w-rest">+ {run.writes.total - notes.length} bookkeeping records (routing, rechecks, citations)</li>}
          </ol>}
        </div>
      </div>

      {!agentRun && settled && run && <Result r={run} named={named} />}

      {past.length > 0 && <div className="jc-past">
        <span className="label muted">/ Earlier this session</span>
        <ol>{past.map((p, i) => {
          const [a, b] = p.diff.unexplained;
          return <li key={i}><span>{p.label}</span><em>{named(p.summary)}</em><b>{a === b ? "no change" : `unexplained ${money(a)} → ${money(b)}`}</b></li>;
        })}</ol>
      </div>}
    </section>
  );
}

function deltaText(k: string, [a, b]: [unknown, unknown]): string {
  const word = (x: unknown) => (x == null || x === "" ? "none" : String(x).replace(/_/g, " "));
  if (k === "owed") return `owed ${money(Number(a))} → ${money(Number(b))}`;
  if (k === "alerts") return `alerts ${(a as string[]).length} → ${(b as string[]).length}`;
  return `${k} ${word(a)} → ${word(b)}`;
}

function Result({ r, named }: { r: Run; named: (t: string) => string }) {
  const d: Diff = r.diff;
  const total = r.state.items.length;
  const moved = !!r.books && FIGS.some((f) => r.books!.before[f.k] !== r.books!.after[f.k]);
  return (
    <section className="jc-result" aria-live="polite">
      <span className="label muted">/ What changed</span>
      <h2>{d.changed.length === 0 ? "Nothing in the books changed." : `${d.changed.length} invoice${d.changed.length === 1 ? "" : "s"} changed. ${d.untouched} of ${total} untouched.`}</h2>
      <div className="jc-detail">
        <ul>
          {d.changed.map((c) => <li key={c.id}><Link href={`/items/${c.id}`}><b>{c.vendor}</b> <span className="mono">{c.id}</span> ↗</Link><small>{Object.entries(c.delta).map(([k, v]) => deltaText(k, v)).join(" · ")}</small></li>)}
          {d.changed.length === 0 && <li><small>{d.rechecked_unchanged.length ? `Rechecked and unchanged: ${d.rechecked_unchanged.join(", ")}.` : "No invoice changed."}</small></li>}
        </ul>
        <ul>
          {d.raised.map((a) => <li key={a.id} className="raised"><b>Alert raised</b><small>{named(a.headline)}</small></li>)}
          {d.withdrawn.map((a) => <li key={a.id} className="withdrawn"><b>Alert withdrawn</b><small>{named(a.headline)}</small></li>)}
          {!d.raised.length && !d.withdrawn.length && <li><small>No alert raised or withdrawn{d.kept.length ? `; ${d.kept.length} still open` : ""}.</small></li>}
        </ul>
      </div>
      <p className="jc-tabs-links">{moved || d.changed.length ? "Every tab already shows this:" : "Every tab still shows the same books:"} <Link href="/overview">Overview ↗</Link><Link href="/cases">Review queue ↗</Link><Link href="/invoices">Invoices ↗</Link><Link href="/properties">Properties ↗</Link></p>
    </section>
  );
}
