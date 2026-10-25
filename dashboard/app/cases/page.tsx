"use client";

import Link from "next/link";
import { Disclosure } from "@/components/Disclosure";
import { useState } from "react";
import { useFeed } from "@/components/Feed";
import { NeedsAttention } from "@/components/NeedsAttention";
import { Badge, Cites, Loading, PageTop, SectionTitle } from "@/components/ui";
import { ALERT, day, money, named, SOURCE } from "@/lib/format";
import type { Case } from "@/lib/types";

const STATUS: Record<Case["status"], string> = {
  pending: "Awaiting controller", approved: "Hold approved", declined: "Declined", superseded: "Superseded by a recheck",
};

function CaseCard({ c }: { c: Case }) {
  const { state, decide, mode } = useFeed();
  const [busy, setBusy] = useState(false);
  const [idv, setIdv] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  if (!state) return null;
  const names = state.meta.names;
  const alerts = state.alerts.filter((a) => c.alerts.includes(a.id));
  const byKind: Record<string, string[]> = {};
  for (const e of c.evidence) (byKind[e.kind] ??= []).push(e.source_id);
  const act = async (approve: boolean) => {
    setBusy(true);
    const r = await decide(c.approval_id, approve, idv);
    setMsg(r.ok ? { ok: true, text: r.summary } : { ok: false, text: r.error ?? "Refused" });
    setBusy(false);
  };
  const dim = c.status === "superseded";

  return (
    <div className="card" style={{ padding: 0, opacity: dim ? 0.55 : 1 }}>
      <div style={{ padding: 24, borderBottom: "1px solid var(--n3)" }}>
        <div className="between case-header">
          <div className="stack" style={{ gap: 8 }}>
            <span className="micro muted">{c.kind === "funding" ? `Funding review · ${money(c.funding_amount ?? 0, false)} to Keyway` : "Vendor bank-detail change"}</span>
            <div className="display h1 num">{money(c.proposed_hold, false)} <span className="h4 muted">hold</span></div>
          </div>
          <span className={`tag ${c.status === "pending" ? "" : c.status === "approved" ? "ok" : "neutral"}`}>{c.kind === "bank_change" && c.status === "approved" ? "Bank change approved" : STATUS[c.status]}</span>
        </div>
        <p className="body muted" style={{ margin: "14px 0 0" }}>{c.kind === "bank_change" ? "Verify identity before approving new bank details." : `${c.alerts.length} alerts support this proposed hold.`}</p>
      </div>

      <Disclosure className="case-review" title={c.status === "pending" ? "Review evidence & decide" : "View case details"} hint="Recommendation, cited records, and alerts" meta={`${c.evidence.length} sources`}>
      <p className="body" style={{ padding: "0 24px" }}>{named(c.summary, names)}</p>
      <div className="grid g2 case-details" style={{ padding: 24, gap: 24 }}>
        <div className="stack" style={{ gap: 12 }}>
          <span className="label muted">Alerts in this case</span>
          {alerts.map((a) => (
            <Link key={a.id} href={`/items/${a.invoice_ids[0]}`} className="arow">
              <Badge kind={a.open ? "flag" : "done"} />
              <div className="arow-main">
                <span className="arow-title">{named(a.headline, names)}</span>
                <span className="micro muted">{ALERT[a.kind]} · verifier {a.verdict.status}{a.open ? "" : " · withdrawn"}</span>
              </div>
            </Link>
          ))}
        </div>
        <div className="stack" style={{ gap: 12 }}>
          <span className="label muted">Evidence pack</span>
          {Object.entries(byKind).map(([k, ids]) => (
            <div key={k} className="case-evidence">
              <span className="body">{SOURCE[k] ?? k}</span>
              <div><Cites cites={ids} /></div>
            </div>
          ))}
        </div>
      </div>

      {c.status === "pending" && (
        <div className="case-actions">
          <span className="micro muted">Proposed by {c.proposed_by} · only a controller can approve, and never the proposer</span>
          <div className="case-action-controls">
            {c.kind === "bank_change" && (
              <label className="identity-check">
                <input type="checkbox" checked={idv} onChange={(e) => setIdv(e.target.checked)} />
                Identity verified by phone
              </label>
            )}
            <button className="pill" disabled={busy || mode !== "live"} onClick={() => act(false)}>Decline</button>
            <button className="btn" disabled={busy || mode !== "live" || (c.kind === "bank_change" && !idv)} onClick={() => act(true)} title={mode !== "live" ? "Needs the live feed" : undefined}>
              Approve {c.kind === "bank_change" ? "bank change" : "hold"} as {state.meta.controller.replace("controller.", "").replace(/^./, (s) => s.toUpperCase())}
            </button>
          </div>
        </div>
      )}
      </Disclosure>
      {c.decided_by && <div className="micro muted" style={{ padding: "12px 24px" }}>Decided by {c.decided_by}</div>}
      {msg && <div className={`notice ${msg.ok ? "ok" : "err"}`} style={{ margin: "0 24px 20px" }}>{msg.text}</div>}
      {mode !== "live" && c.status === "pending" && <div className="notice" style={{ margin: "0 24px 20px" }}>Replay feed: decisions need the live feed, since they go through the rails policy gate.</div>}
    </div>
  );
}

export default function Cases() {
  const { state } = useFeed();
  if (!state) return <><PageTop title="Review queue" /><Loading /></>;
  const order = { pending: 0, approved: 1, declined: 1, superseded: 2 } as const;
  const cases = state.cases.slice().sort((a, b) => order[a.status] - order[b.status] || (a.kind === "funding" ? -1 : 1));
  const blocked = state.guardrails.filter((g) => !g.allowed);
  const caseIds = new Set(state.cases.flatMap((c) => [c.approval_id, c.id]));
  const audit = state.audit.filter((a) => ["case_proposed", "approval_requested", "approval_decided", "chase_sent", "chase_held", "alert_withdrawn", "bank_change_held", "fact_corrected", "funding_instruction", "bank_change_decided", "chase_reply", "reply_read"].includes(a.type)
    || [...caseIds].some((id) => a.what.includes(id)));

  return (
    <>
      <PageTop title="Review queue" sub="What needs someone, and the holds waiting for you." />
      <NeedsAttention state={state} />
      <h2 className="lr-h">Waiting for your decision</h2>
      <div className="case-layout">
        <div className="stack" style={{ gap: 16 }}>
          {cases.filter(c => c.status === "pending").map(c => <CaseCard key={c.approval_id} c={c} />)}
          {!cases.some(c => c.status === "pending") && <div className="card"><h2 className="display h3">You’re all caught up.</h2><p className="body muted">No cases need a decision right now.</p></div>}
          <Disclosure title="Previous decisions" hint="Recorded instructions and completed cases" meta={(state.decisions ?? []).length}>
          <div className="stack" style={{ gap: 16 }}>
          {!cases.some(c => c.status !== "pending") && !(state.decisions ?? []).length && <p className="body muted">No previous decisions yet.</p>}
          {(state.decisions ?? []).slice().reverse().map((d) => (
            <div key={d.approval_id} className={`card ${d.type === "funding_instruction" ? "block-shadow" : ""}`} style={{ borderColor: "var(--n11)" }}>
              {d.type === "funding_instruction" ? (
                <div className="between" style={{ flexWrap: "wrap", gap: 16 }}>
                  <div className="stack" style={{ gap: 4 }}>
                    <span className="label">/ Funding instruction recorded</span>
                    <span className="micro muted">by {d.by} · for a person to carry out · no money moved by the system</span>
                  </div>
                  <div className="row" style={{ gap: 28 }}>
                    <div><div className="micro ok">Send to Keyway</div><div className="display h2 num">{money(d.release, false)}</div></div>
                    <div><div className="micro warn">Hold</div><div className="display h2 num warn">{money(d.hold, false)}</div></div>
                  </div>
                </div>
              ) : (
                <div className="row"><Badge kind={d.allowed ? "done" : "no"} /><span className="body" style={{ color: "var(--ink)" }}>{d.allowed ? "Bank-detail change approved after identity check." : `Bank-detail change still held: ${d.reason}.`}</span></div>
              )}
            </div>
          ))}
          {cases.filter(c => c.status !== "pending").map((c) => <CaseCard key={c.approval_id} c={c} />)}
          </div>
          </Disclosure>
        </div>
        <div className="stack" style={{ gap: 16 }}>
          <Disclosure title="Protection checks" hint="What the policy gate blocked" meta={blocked.length}>
            <div className="rows">
              {blocked.slice(0, 4).map((g) => (
                <div key={g.label} className="arow" style={{ alignItems: "flex-start" }}>
                  <Badge kind="no" />
                  <div className="arow-main">
                    <span className="arow-title" style={{ whiteSpace: "normal" }}>{g.label}</span>
                    <span className="micro warn-ink" style={{ textTransform: "none", letterSpacing: 0 }}>{g.reason}</span>
                  </div>
                </div>
              ))}
            </div>
            <Link href="/guardrails" className="link micro" style={{ display: "inline-block", marginTop: 12 }}>All guardrails</Link>
          </Disclosure>
          <Disclosure title="Audit trail" hint="Latest case events and decisions" meta={`${audit.length} events`}>
            <div className="stack" style={{ gap: 0 }}>
              {audit.map((a) => (
                <div key={a.id} style={{ padding: "10px 0", borderBottom: ".5px solid var(--n5)" }}>
                  <div className="between"><span className="micro warn-ink">{a.actor.replace("_", " ")}</span><span className="micro faint">{day(a.at)}</span></div>
                  <div className="body" style={{ fontSize: 13, marginTop: 2 }}>{named(a.what, state.meta.names)}</div>
                </div>
              ))}
            </div>
          </Disclosure>
        </div>
      </div>
    </>
  );
}
