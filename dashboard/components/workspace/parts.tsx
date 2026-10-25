"use client";

// The pieces of the UI revamp 2 workspace, in this dashboard's design: page header with its
// date / filter / export controls, status cards, charts, the ledger, and every dialog.

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { money } from "@/lib/format";
import {
  PERIODS, aging, avgDaysToPay, categories, csv, dateLabel, download, longDate, sums, trend,
  type WsInvoice,
} from "@/lib/workspace";
import { useWs } from "./Workspace";

const usd0 = (n: number) => money(n, false);

// ---- dialogs and toast ------------------------------------------------------------------------
export function Dialog({ title, children }: { title: string; children: ReactNode }) {
  const { closeDialog } = useWs();
  return <div className="ws-dialog" role="dialog" aria-modal="true" aria-label={title}>
    <div className="ws-dialog-head"><h2>{title}</h2><button type="button" className="ws-x" onClick={closeDialog} aria-label="Close dialog">×</button></div>
    {children}
  </div>;
}

export function WorkspaceLayer() {
  const { dialog, closeDialog, toastText } = useWs();
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") closeDialog(); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [closeDialog]);
  return <>
    {dialog && <div className="ws-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) closeDialog(); }}>{dialog}</div>}
    {toastText && <div className="ws-toast" role="status">{toastText}</div>}
  </>;
}

// ---- page header ---------------------------------------------------------------------------
export function WsHead({ crumb, title, extra, exportRows }: { crumb?: ReactNode; title: string; extra?: ReactNode; exportRows: WsInvoice[] }) {
  const ws = useWs();
  const exportCsv = () => { download("accountable-invoices.csv", csv(exportRows, ws.reviews), "text/csv"); ws.toast(`Exported ${exportRows.length} invoices`); };
  return <div className="top ws-top">
    <div className="stack" style={{ gap: 6 }}>
      {crumb && <div className="ws-crumb">{crumb}</div>}
      <h1 className="display h2">{title}</h1>
    </div>
    <div className="ws-actions">
      {extra}
      <button type="button" className="ws-btn" onClick={() => ws.openDialog(<DateDialog />)}>▣ &nbsp;{dateLabel(ws.start, ws.end)} &nbsp;⌄</button>
      <button type="button" className="ws-btn ws-icon" onClick={() => ws.openDialog(<FiltersDialog />)} aria-label="Filters">⚑</button>
      <button type="button" className="ws-btn ws-icon" onClick={exportCsv} aria-label="Export invoices">↧</button>
    </div>
  </div>;
}

function DateDialog() {
  const ws = useWs();
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const d = new FormData(e.currentTarget), s = String(d.get("start")), en = String(d.get("end"));
    if (s > en) { ws.toast("End date must be on or after start date"); return; }
    ws.setDates(s, en); ws.closeDialog();
  };
  return <Dialog title="Date range"><form onSubmit={submit} className="ws-form">
    <label>Start date<input name="start" type="date" defaultValue={ws.start} required /></label>
    <label>End date<input name="end" type="date" defaultValue={ws.end} required /></label>
    <p className="ws-muted">Invoice records are available for {dateLabel(ws.bounds.start, ws.bounds.end)}.</p>
    <div className="ws-dialog-actions">
      <button type="button" className="ws-btn" onClick={() => { ws.setDates(ws.bounds.start, ws.bounds.end); ws.closeDialog(); }}>All invoice dates</button>
      <button className="btn">Apply dates</button>
    </div>
  </form></Dialog>;
}

function FiltersDialog() {
  const ws = useWs();
  const vendors = [...new Set(ws.invoices.map(i => i.vendor))].sort();
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const d = new FormData(e.currentTarget);
    ws.go("/invoices", { filterProperty: String(d.get("property")), vendor: String(d.get("vendor")), status: String(d.get("status")) });
  };
  return <Dialog title="Filter invoices"><form onSubmit={submit} className="ws-form">
    <label>Property<select name="property" defaultValue={ws.filters.filterProperty}>{["All Properties", ...ws.properties].map(p => <option key={p}>{p}</option>)}</select></label>
    <label>Vendor<select name="vendor" defaultValue={ws.filters.vendor}>{["All Vendors", ...vendors].map(v => <option key={v}>{v}</option>)}</select></label>
    <label>Status<select name="status" defaultValue={ws.filters.status}>{STATUSES.map(s => <option key={s}>{s}</option>)}</select></label>
    <div className="ws-dialog-actions">
      <button type="button" className="ws-btn" onClick={() => { ws.closeDialog(); ws.clearFilters(); }}>Reset filters</button>
      <button className="btn">Show invoices</button>
    </div>
  </form></Dialog>;
}
export const STATUSES = ["All Statuses", "Outstanding", "Past Due", "Paid", "Needs Review"];

// ---- invoice review --------------------------------------------------------------------------
export function InvoiceReview({ id }: { id: string }) {
  const ws = useWs();
  const i = ws.invoices.find(x => x.id === id);
  const note = useRef<HTMLTextAreaElement>(null);
  if (!i) return <Dialog title="Invoice Review"><p>Invoice {id} isn&apos;t in the workspace.</p></Dialog>;
  const r = ws.reviews[i.id] ?? { notes: [], reviewed: false };
  const save = () => {
    const n = note.current?.value.trim();
    if (!n) { ws.toast("Enter a note first"); return; }
    ws.addNote(i.id, n); if (note.current) note.current.value = ""; ws.toast("Review note saved");
  };
  const reviewed = () => { ws.markReviewed(i.id, note.current?.value.trim() || undefined); ws.closeDialog(); ws.toast(`${i.vendor} marked reviewed`); };
  return <Dialog title="Invoice Review">
    <div className="ws-crumb">{i.id} &nbsp;·&nbsp; {i.cid}</div>
    <h3 className="ws-h">{i.vendor}</h3>
    <p className="ws-muted">{i.description}</p>
    <div className="ws-details">
      {[["Property", i.property], ["Amount", money(i.amount)], ["Due date", longDate(i.due)], ["Status", i.status]].map(([k, v]) =>
        <div key={k}><small>{k}</small><b>{v}</b></div>)}
    </div>
    {i.issue && <><div className="ws-issue">⚠ {i.issue}{r.reviewed ? " · Reviewed" : ""}</div>{i.cause && <p className="ws-muted">{i.cause}</p>}</>}
    <h3 className="ws-h">Review notes</h3>
    {r.notes.length ? r.notes.map((n, k) => <p key={k} className="ws-note">{n}</p>) : <p className="ws-muted">No notes yet.</p>}
    <label className="ws-form">Add a note<textarea ref={note} rows={3} placeholder="Record your findings or next steps…" /></label>
    <div className="ws-dialog-actions">
      <button type="button" className="ws-btn" onClick={save}>Save note</button>
      {i.issue && !r.reviewed && <button type="button" className="btn" onClick={reviewed}>Mark reviewed</button>}
      <button type="button" className="ws-btn" onClick={() => ws.go("/invoices")}>View ledger</button>
      <Link className="ws-link" href={`/items/${i.id}`} onClick={ws.closeDialog}>Evidence trail ↗</Link>
    </div>
    <p className="ws-muted">Reviewing an invoice does not issue or authorize a payment.</p>
  </Dialog>;
}

// ---- cards and charts --------------------------------------------------------------------------
export function StatButton({ label, value, tone = "", desc, icon = "▤", onClick }: { label: string; value: string; tone?: string; desc: string; icon?: string; onClick: () => void }) {
  return <button type="button" className="ws-stat" onClick={onClick}>
    <span className="ws-arrow">↗</span><span className={`ws-tile ${tone}`}>{icon}</span>
    <label>{label}</label><strong className={tone}>{value}</strong><small>{desc}</small>
  </button>;
}

export function ReviewCard({ rows }: { rows: WsInvoice[] }) {
  const ws = useWs();
  if (!rows.length) return null;
  return <section className="lr-box ws-card ws-review">
    <h2 className="ws-h2"><span className="ws-red">⚠</span> &nbsp;{rows.length} Invoice{rows.length === 1 ? " Needs" : "s Need"} Review</h2>
    {rows.map(i => <div key={i.id} className="ws-reviewrow">
      <div><b>{i.vendor}</b><span className="ws-muted"> &nbsp;•&nbsp; {i.description}</span><div className="ws-issue-line">{i.issue}</div></div>
      <div className="ws-amount">{money(i.amount)}</div>
      <button type="button" className="btn" onClick={() => ws.openDialog(<InvoiceReview id={i.id} />)}>Review</button>
    </div>)}
  </section>;
}

export function StatusCard({ rows, onProperty }: { rows: WsInvoice[]; onProperty: boolean }) {
  const ws = useWs();
  const s = sums(rows), pct = (n: number) => (s.total ? (n / s.total) * 100 : 0);
  const link = (status: string) => ws.go("/invoices", { status, filterProperty: onProperty ? ws.property : "All Properties" });
  const count = (st: string) => rows.filter(r => r.status === st).length;
  return <section className="lr-box ws-card">
    <h3 className="ws-h3"><span className="ws-blue">⚖</span> &nbsp;Status Update</h3>
    <div className="ws-stats">
      <StatButton label="Total Invoiced" value={money(s.total)} desc={`${rows.length} invoices`} onClick={() => link("All Statuses")} />
      <StatButton label="Payments" value={money(s.paid)} tone="green" icon="✓" desc={`${count("Paid")} settled`} onClick={() => link("Paid")} />
      <StatButton label="Outstanding" value={money(s.out)} tone="blue" icon="☑" desc={`${count("Outstanding")} awaiting payment`} onClick={() => link("Outstanding")} />
      <StatButton label="Past Due" value={money(s.past)} tone="red" icon="◷" desc={`${count("Past Due")} need attention`} onClick={() => link("Past Due")} />
    </div>
    <div className="ws-stack"><span style={{ width: `${pct(s.paid)}%`, background: "var(--ws-green)" }} /><span style={{ width: `${pct(s.out)}%`, background: "var(--o)" }} /><span style={{ width: `${pct(s.past)}%`, background: "var(--ws-red)" }} /></div>
    <div className="ws-legend">{([["Payments", s.paid, "var(--ws-green)"], ["Outstanding", s.out, "var(--o)"], ["Past Due", s.past, "var(--ws-red)"]] as const).map(([n, v, c]) =>
      <span key={n}><i style={{ background: c }} />{n} ({money(v)} • {pct(v).toFixed(1)}%)</span>)}</div>
  </section>;
}

export function PeriodTabs() {
  const ws = useWs();
  return <div className="ws-tabs" role="group" aria-label="Period">{PERIODS.map(p =>
    <button key={p} type="button" className={p === ws.period ? "on" : ""} onClick={() => ws.setPeriod(p)}>{p}</button>)}</div>;
}

export function TrendChart({ rows }: { rows: WsInvoice[] }) {
  const ws = useWs();
  const pts = trend(rows, ws.period, ws.end, ws.start);
  if (!pts.some(p => p.value)) return <div className="ws-empty">No invoices in this selection.</div>;
  const max = Math.max(...pts.map(p => p.value)), top = Math.ceil(max / 1000) * 1000 || 1000;
  const x = (i: number) => 50 + (pts.length === 1 ? 265 : (i * 530) / (pts.length - 1)), y = (v: number) => 205 - (v / top) * 165;
  const line = pts.map((p, i) => `${i ? "L" : "M"}${x(i)} ${y(p.value)}`).join(" ");
  const peak = pts.reduce((b, p, i) => (p.value > pts[b].value ? i : b), 0);
  const every = Math.ceil(pts.length / 6);
  return <svg className="ws-chart" viewBox="0 0 600 255" role="img" aria-label={`${ws.period} invoiced amount by due date, peak ${money(pts[peak].value)}`}>
    <defs><linearGradient id="wsfade" x1="0" y1="0" x2="0" y2="1"><stop stopColor="#2d5bff" stopOpacity=".22" /><stop offset="1" stopColor="#2d5bff" stopOpacity=".01" /></linearGradient></defs>
    {[0, 1, 2].map(a => <g key={a}><line x1="50" y1={40 + a * 82.5} x2="580" y2={40 + a * 82.5} stroke="#e6eaf2" /><text x="0" y={44 + a * 82.5}>{a === 2 ? "$0" : usd0(top * (1 - a / 2))}</text></g>)}
    <path d={`${line} L${x(pts.length - 1)} 205 L${x(0)} 205 Z`} fill="url(#wsfade)" />
    <path d={line} fill="none" stroke="#2d5bff" strokeWidth="3" />
    <line x1={x(peak)} y1={y(pts[peak].value)} x2={x(peak)} y2="205" stroke="#ff8a2c" strokeDasharray="4 4" />
    <circle cx={x(peak)} cy={y(pts[peak].value)} r="5" fill="#ff8a2c" stroke="white" strokeWidth="2" />
    <rect x={Math.min(470, Math.max(40, x(peak) - 58))} y={y(pts[peak].value) - 33} width="125" height="25" rx="5" fill="#0b1220" />
    <text x={Math.min(470, Math.max(40, x(peak) - 58)) + 10} y={y(pts[peak].value) - 16} className="ws-peak">● PEAK {usd0(pts[peak].value)}</text>
    {pts.map((p, i) => (i % every === 0 || i === pts.length - 1) && <text key={p.key} x={x(i)} y="239" textAnchor="middle">{p.label}</text>)}
  </svg>;
}

export function Charts({ rows }: { rows: WsInvoice[] }) {
  const ws = useWs();
  const cats = categories(rows), total = cats.reduce((s, c) => s + c.value, 0);
  return <div className="ws-split">
    <section className="lr-box ws-card">
      <div className="ws-charthead"><div><h3 className="ws-h3">Payment Trends</h3><div className="ws-muted">{dateLabel(ws.start, ws.end)}</div></div><PeriodTabs /></div>
      <TrendChart rows={rows} />
      <div className="ws-muted">Invoiced amount by due date</div>
    </section>
    <section className="lr-box ws-card">
      <h3 className="ws-h3">Outstanding</h3>
      <div className="ws-muted">Total: <b>{money(total)}</b></div>
      {cats.length ? cats.map(c => <div key={c.name} className="ws-category">{c.name}<strong>{money(c.value)}</strong>
        <div className="ws-track"><span style={{ width: `${total ? (c.value / total) * 100 : 0}%`, background: c.color }} /></div></div>)
        : <p className="ws-muted" style={{ marginTop: 18 }}>Nothing outstanding in this selection.</p>}
      <div className="ws-muted" style={{ marginTop: 22 }}>Unpaid balance by trade</div>
    </section>
  </div>;
}

export function AgingChart({ rows }: { rows: WsInvoice[] }) {
  const b = aging(rows), max = Math.max(1000, ...b.map(x => x.value)), top = Math.ceil(max / 1000) * 1000;
  return <svg className="ws-chart" viewBox="0 0 600 255" role="img" aria-label={b.map(x => `${x.label} ${money(x.value)}`).join("; ")}>
    {[0, 1, 2].map(a => <g key={a}><line x1="50" y1={40 + a * 82.5} x2="590" y2={40 + a * 82.5} stroke="#e6eaf2" /><text x="0" y={44 + a * 82.5}>{a === 2 ? "$0" : usd0(top * (1 - a / 2))}</text></g>)}
    {b.map((x, i) => { const h = Math.max((x.value / top) * 165, 2); return <g key={x.label}>
      <rect x={75 + i * 103} y={205 - h} width="55" height={h} rx="7" fill={i === 0 ? "#2f5d6e" : i === 1 ? "#5b80ff" : "#c9d1df"} />
      <text x={102 + i * 103} y={195 - h} textAnchor="middle" className="ws-barval">{usd0(x.value)}</text>
      <text x={102 + i * 103} y="239" textAnchor="middle">{x.label}</text>
    </g>; })}
  </svg>;
}

export function Ledger({ rows }: { rows: WsInvoice[] }) {
  const ws = useWs();
  const per = 8, pages = Math.max(1, Math.ceil(rows.length / per)), page = Math.min(ws.page, pages);
  const part = rows.slice((page - 1) * per, page * per);
  const open = (id: string) => ws.openDialog(<InvoiceReview id={id} />);
  return <section className="lr-box ws-card ws-ledger">
    <div className="ws-ledgerhead"><h3 className="ws-h3">Invoice Ledger <span className="ws-badge">{rows.length} items</span></h3><span className="ws-muted">As of {longDate(ws.asOf)}</span></div>
    <div className="table-scroll"><table className="table lr-table">
      <thead><tr>{["Property", "Vendor / Invoice", "Invoice Amount", "Variance", "Due Date", "Days Past Due", "Status"].map(h => <th key={h}>{h}</th>)}</tr></thead>
      <tbody>{part.map(i => { const rv = ws.reviews[i.id]?.reviewed; return <tr key={i.id} tabIndex={0} role="button" aria-label={`Open ${i.id}, ${i.vendor}`} className="ws-row"
        onClick={() => open(i.id)} onKeyDown={(e) => { if (e.key === "Enter") open(i.id); }}>
        <td>{i.property}</td><td>{i.vendor}<div className="mono ws-sub">{i.id}</div></td>
        <td className="num"><strong>{money(i.amount)}</strong></td>
        <td className={i.issue && !rv ? "ws-red" : ""}>{i.issue ? (rv ? "✓ Reviewed" : `⚑ ${i.issue}`) : "—"}</td>
        <td className="mono">{longDate(i.due)}</td><td className={`num ${i.daysLate ? "ws-red" : ""}`}>{i.daysLate}</td>
        <td><span className={`ws-pill ${i.status === "Paid" ? "paid" : i.status === "Past Due" ? "past" : ""}`}>{i.status}</span></td>
      </tr>; })}
      {!part.length && <tr><td colSpan={7} className="ws-empty">No invoices match these filters. Clear filters to see all invoices.</td></tr>}</tbody>
    </table></div>
    <div className="ws-pagination"><span>Showing {rows.length ? (page - 1) * per + 1 : 0}–{Math.min(page * per, rows.length)} of {rows.length} invoices</span>
      <button type="button" className="ws-btn" disabled={page === 1} onClick={() => ws.setPage(page - 1)}>‹ Previous</button>
      <button type="button" className="ws-btn" disabled={page === pages} onClick={() => ws.setPage(page + 1)}>Next ›</button>
    </div>
  </section>;
}

export function InvoiceStats({ rows }: { rows: WsInvoice[] }) {
  const ws = useWs();
  const s = sums(rows), flagged = ws.needs, avg = avgDaysToPay(ws.invoices);
  const link = (status: string) => ws.go("/invoices", { status });
  return <div className="ws-stats ws-stats5">
    <StatButton label="Total Invoiced" value={money(s.total)} desc={`${rows.length} matching invoices`} onClick={() => link("All Statuses")} />
    <StatButton label="Outstanding" value={money(s.out)} tone="blue" icon="◷" desc="Current unpaid invoices" onClick={() => link("Outstanding")} />
    <StatButton label="Past Due" value={money(s.past)} tone="red" icon="ⓘ" desc="Overdue balance" onClick={() => link("Past Due")} />
    <StatButton label="Variance" value={`${flagged.length} Invoices Flagged`} tone="red" icon="⚑" desc={`${money(flagged.reduce((t, i) => t + i.amount, 0))} invoice value`} onClick={() => link("Needs Review")} />
    <StatButton label="Avg. Days to Pay" value={avg == null ? "—" : `${avg} days`} tone="green" icon="▥" desc="From invoice received to vendor paid" onClick={() => link("Paid")} />
  </div>;
}

// ---- top bar: search, AI Assist, notifications, account ------------------------------------------
export function TopBar() {
  const ws = useWs();
  const [pop, setPop] = useState<"bell" | "account" | null>(null);
  const [q, setQ] = useState("");
  const search = useRef<HTMLInputElement>(null);
  const bar = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key === "k") { e.preventDefault(); search.current?.focus(); } if (e.key === "Escape") setPop(null); };
    const click = (e: MouseEvent) => { if (bar.current && !bar.current.contains(e.target as Node)) setPop(null); };
    window.addEventListener("keydown", key); document.addEventListener("mousedown", click);
    return () => { window.removeEventListener("keydown", key); document.removeEventListener("mousedown", click); };
  }, []);
  useEffect(() => {
    if (!q) return;
    const t = window.setTimeout(() => ws.go("/invoices", { query: q }), 250);
    return () => window.clearTimeout(t);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);
  const n = ws.needs;
  return <div className="ws-topbar" ref={bar}>
    <div className="ws-search"><span aria-hidden="true">⌕</span>
      <input ref={search} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search invoice, CID, vendor…" aria-label="Search invoices" />
      <button type="button" className="ws-assist" onClick={() => ws.openDialog(<AssistDialog />)}>✦ AI Assist</button>
    </div>
    <button type="button" className="ws-iconbtn" aria-label="Notifications" onClick={() => setPop(pop === "bell" ? null : "bell")}>
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" /></svg>
      {n.length > 0 && <i className="ws-dot" />}
    </button>
    <button type="button" className="ws-account" onClick={() => setPop(pop === "account" ? null : "account")}>
      <span className="ws-avatar">{ws.profile.photo ? <img src={ws.profile.photo} alt="" /> : initials(ws.profile.name)}</span> Account
    </button>
    {pop === "bell" && <section className="ws-popcard">
      <h3 className="ws-h3">{n.length} Invoice{n.length === 1 ? " Needs" : "s Need"} Review</h3>
      {n.length ? n.map(i => <button type="button" key={i.id} onClick={() => { setPop(null); ws.openDialog(<InvoiceReview id={i.id} />); }}>
        <strong>{i.vendor} <span style={{ float: "right" }}>{money(i.amount)}</span></strong><small>{i.issue}</small><div className="ws-muted">{i.property} · {i.id}</div>
      </button>) : <p className="ws-muted">All flagged invoices have been reviewed.</p>}
      <button type="button" onClick={() => { setPop(null); ws.go("/invoices", { status: "Needs Review" }); }}>View review queue ↗</button>
    </section>}
    {pop === "account" && <section className="ws-popcard">
      <h3 className="ws-h3">{ws.profile.name}</h3><p className="ws-muted">{ws.profile.email}<br />{ws.profile.company}</p>
      <button type="button" onClick={() => { setPop(null); ws.go("/settings"); }}>♙ My profile</button>
      <button type="button" onClick={() => { setPop(null); ws.go("/settings"); }}>⚙ Account settings</button>
      <button type="button" onClick={() => { setPop(null); ws.go("/help"); }}>ⓘ Help &amp; Support</button>
      <button type="button" onClick={() => { setPop(null); ws.openDialog(<LogoutDialog />); }}>⇥ Log out</button>
    </section>}
  </div>;
}
export const initials = (name: string) => name.split(/\s+/).map(w => w[0] ?? "").join("").slice(0, 2).toUpperCase() || "?";

function AssistDialog() {
  const ws = useWs();
  return <Dialog title="AI Assist">
    <p>Jump straight to the invoices that matter. For questions in your own words, use Ask about Keyway.</p>
    <div className="ws-dialog-actions">
      <button type="button" className="ws-btn" onClick={() => ws.go("/invoices", { status: "Needs Review" })}>Show invoices to review</button>
      <button type="button" className="ws-btn" onClick={() => ws.go("/invoices", { status: "Past Due" })}>Show past due invoices</button>
      <button type="button" className="ws-btn" onClick={() => ws.go("/invoices", { status: "Outstanding" })}>Show outstanding balance</button>
    </div>
    <p className="ws-muted">Tip: search by vendor, invoice ID, CID, or property in the search bar. <Link className="ws-link" href="/ask" onClick={ws.closeDialog}>Ask about Keyway ↗</Link></p>
  </Dialog>;
}

export function LogoutDialog() {
  const ws = useWs();
  return <Dialog title="Leave this workspace?">
    <p>Your review notes, profile and preferences are saved on this device. This exits the workspace view; your Google sign-in is managed separately.</p>
    <div className="ws-dialog-actions">
      <button type="button" className="ws-btn" onClick={ws.closeDialog}>Stay here</button>
      <button type="button" className="btn" onClick={() => { ws.closeDialog(); window.location.href = "/"; }}>Leave workspace</button>
    </div>
  </Dialog>;
}

export function SupportDialog() {
  const ws = useWs();
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const d = new FormData(e.currentTarget);
    download("accountable-support-request.txt", `AccountableAI Support Request\nFrom: ${ws.profile.name}\nEmail: ${ws.profile.email}\nSubject: ${d.get("subject")}\n\n${d.get("details")}`, "text/plain");
    ws.closeDialog(); ws.toast("Support request downloaded");
  };
  return <Dialog title="Create support request"><form onSubmit={submit} className="ws-form">
    <label>Subject<input name="subject" required placeholder="What do you need help with?" /></label>
    <label>Details<textarea name="details" rows={5} required placeholder="Describe the issue…" /></label>
    <p className="ws-muted">This downloads a support request for you to share. No message will be sent.</p>
    <div className="ws-dialog-actions"><button className="btn">Download request</button></div>
  </form></Dialog>;
}

export function SecurityDialog() {
  const ws = useWs();
  return <Dialog title="Account security">
    <p>Sign-in uses your Google account. Passwords and two-factor authentication are managed in your Google account settings.</p>
    <div className="ws-dialog-actions"><Link className="ws-btn" href="/agreements" onClick={ws.closeDialog}>Google sign-in ↗</Link><button type="button" className="btn" onClick={ws.closeDialog}>Done</button></div>
  </Dialog>;
}

