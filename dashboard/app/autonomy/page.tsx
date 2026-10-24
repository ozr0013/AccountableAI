"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Case = {
  id: string; manager_id: string; vendor_id: string; invoice_id: string;
  state: string; summary: string; plan: string[]; open_questions: string[];
  next_action: string | null; next_wake_at: string | null; version: number;
};
type Ticket = { id: string; case_id: string; kind: string; status: string; digest: string; payload: Record<string, unknown> };
type Message = { id: string; case_id: string; recipient: string; body: string; status: string };
type Trace = { id: string; type: string; payload: Record<string, unknown> };

export default function AutonomyPage() {
  const generation = useRef(0);
  const [token, setToken] = useState("");
  const [cases, setCases] = useState<Case[]>([]);
  const [selected, setSelected] = useState("");
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [trace, setTrace] = useState<Trace[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [decision, setDecision] = useState("");
  const [notice, setNotice] = useState("");
  const request = useCallback(async (path: string, body?: unknown) => {
    const response = await fetch(`/api/autonomy${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      cache: "no-store",
    });
    if (!response.ok) {
      const detail: { detail?: string } = await response.json().catch(() => ({}));
      throw new Error(detail.detail || `Request failed (${response.status})`);
    }
    return response.json();
  }, [token]);
  const refresh = useCallback(async () => {
    const epoch = ++generation.current;
    setBusy(true); setError("");
    try {
      const rows: Case[] = await request("/cases");
      if (epoch !== generation.current) return;
      setCases(rows);
      const id = selected || rows[0]?.id;
      if (id) {
        setSelected(id);
        const [approvals, outbox, events] = await Promise.all([
          request(`/approvals?case_id=${encodeURIComponent(id)}`),
          request(`/outbox?case_id=${encodeURIComponent(id)}`),
          request(`/cases/${encodeURIComponent(id)}/events`),
        ]);
        if (epoch !== generation.current) return;
        setTickets(approvals); setMessages(outbox); setTrace(events);
      }
    } catch (e) { setError(e instanceof Error ? e.message : "Unable to load cases"); }
    finally { if (epoch === generation.current) setBusy(false); }
  }, [request, selected]);
  useEffect(() => { if (token) void refresh(); }, [selected]); // Explicit refresh avoids storing the controller token.
  const current = cases.find(c => c.id === selected);
  async function act(path: string, body: unknown, message: string) {
    setBusy(true); setError(""); setNotice("");
    try { await request(path, body); setNotice(message); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : "Action failed"); setBusy(false); }
  }
  const style = { background: "#111827", color: "#e5e7eb", border: "1px solid #374151", padding: 10, borderRadius: 6 };
  return <main style={{ maxWidth: 1100, margin: "40px auto", padding: 24, color: "#e5e7eb", background: "#0b1020", minHeight: "100vh" }}>
    <a href="/" style={{ color: "#93c5fd" }}>← Dashboard</a>
    <h1 style={{ fontSize: 30 }}>Autonomous finance cases</h1>
    <p>Persistent database records, agent plans, human decisions, and queued drafts. Mail delivery requires a configured transport.</p>
    <label>Controller token <input type="password" value={token} onChange={e => setToken(e.target.value)} autoComplete="off" style={style} /></label>{" "}
    <button disabled={busy || !token} onClick={() => void refresh()} style={style}>Refresh</button>
    {error && <p role="alert" style={{ color: "#fca5a5" }}>{error}</p>}
    {notice && <p role="status">{notice}</p>}
    <p><select aria-label="Case" disabled={busy} value={selected} onChange={e => { generation.current++; setBusy(true); setSelected(e.target.value); setTickets([]); setMessages([]); setTrace([]); setDecision(""); }} style={style}>
      {cases.map(c => <option key={c.id} value={c.id}>{c.invoice_id} · {c.manager_id} · {c.state}</option>)}
    </select></p>
    {!current && <p>No cases loaded. Start the autonomous worker to discover database invoices.</p>}
    {current && <>
      <h2>{current.invoice_id} — {current.state}</h2>
      <p>Vendor: {current.vendor_id} · Manager: {current.manager_id} · Revision: {current.version}</p>
      <p>Next: {current.next_action || "Awaiting investigation"} · Wake: {current.next_wake_at || "Database change or daily sweep"}</p>
      <button disabled={busy} style={style} onClick={() => void act(`/cases/${current.id}/wake`, { key: crypto.randomUUID() }, "Investigation queued for the worker.")}>Queue investigation</button>
      <h3>Case memory and plan</h3><pre style={{ whiteSpace: "pre-wrap" }}>{current.summary || "No saved summary yet."}</pre>
      <ol>{current.plan.map((p, i) => <li key={i}>{p}</li>)}</ol>
      <p>{current.open_questions.join(" · ")}</p>
      <h3>Human decisions</h3>
      {tickets.length === 0 && <p>No approval requests.</p>}
      {tickets.map(t => <article key={t.id} style={{ ...style, marginBottom: 12 }}>
        <p>{t.kind} · {t.status}</p><pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(t.payload, null, 2)}</pre>
        {t.status === "pending" && [true, false].map(approve => <button key={String(approve)} disabled={busy} style={style}
          onClick={() => void act(`/approvals/${t.id}/decide`, { approve, digest: t.digest }, approve ? "Exact proposal approved." : "Proposal rejected.")}>{approve ? "Approve exact proposal" : "Reject"}</button>)}
      </article>)}
      <h3>Outbox</h3>{messages.length === 0 && <p>No approved drafts queued.</p>}
      {messages.map(m => <article key={m.id} style={style}><p>{m.recipient} · {m.status}</p><p>{m.body}</p></article>)}
      <h3>Agent trace</h3>
      {trace.map(e => <details key={e.id}><summary>{e.type} · {e.id.slice(-12)}</summary><pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(e.payload, null, 2)}</pre></details>)}
      <h3>Feedback for reviewed learning</h3>
      <select aria-label="Decision to review" value={decision} onChange={e => setDecision(e.target.value)} style={style}>
        <option value="">Select an agent decision</option>{trace.filter(e => e.type.startsWith("supervisor_")).map(e => <option key={e.id} value={e.id}>{e.type} · {e.id.slice(-12)}</option>)}
      </select>
      <p><textarea aria-label="Decision feedback" maxLength={6000} value={feedback} onChange={e => setFeedback(e.target.value)} style={{ ...style, width: "100%" }} /></p>
      <button disabled={busy || !decision || !feedback.trim()} style={style} onClick={() => void act("/feedback", { case_id: current.id, decision_id: decision, text: feedback, key: crypto.randomUUID() }, "Feedback recorded. Guidance changes require evaluation and human promotion.")}>Record feedback</button>
    </>}
  </main>;
}
