"use client";

import Link from "next/link";
import { useState } from "react";
import { useFeed } from "@/components/Feed";
import { Badge, Cites, Loading, PageTop, SectionTitle } from "@/components/ui";
import { ALERT, day, money, named, SOURCE } from "@/lib/format";
import type { Case } from "@/lib/types";

const STATUS: Record<Case["status"], string> = {
  pending: "Awaiting controller", approved: "Hold approved", declined: "Declined", superseded: "Superseded by a recheck",
};

function CaseCard({ c }: { c: Case }) {
  const { state, decide, mode } = useFeed();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  if (!state) return null;
  const names = state.meta.names;
  const alerts = state.alerts.filter((a) => c.alerts.includes(a.id));
  const byKind: Record<string, string[]> = {};
  for (const e of c.evidence) (byKind[e.kind] ??= []).push(e.source_id);
  const act = async (approve: boolean) => {
    setBusy(true);
    const r = await decide(c.approval_id, approve);
    setMsg(r.ok ? { ok: true, text: approve ? `Approved by ${state.meta.controller}. The hold is a recorded instruction; no money moved.` : "Declined. The funding goes out in full; the alerts stay open." }
      : { ok: false, text: r.error ?? "Refused" });
    setBusy(false);
  };
  const dim = c.status === "superseded";

  return (
    <div className="card" style={{ padding: 0, opacity: dim ? 0.55 : 1 }}>
      <div style={{ padding: 24, borderBottom: "1px solid var(--n3)" }}>
        <div className="between" style={{ alignItems: "flex-start" }}>
          <div className="stack" style={{ gap: 8 }}>
            <span className="micro muted">{c.kind === "funding" ? `Funding review · ${money(c.funding_amount ?? 0, false)} to Keyway` : "Vendor bank-detail change"}</span>
            <div className="display h1 num">{money(c.proposed_hold, false)} <span className="h4 muted">hold</span></div>
          </div>
          <span className={`tag ${c.status === "pending" ? "" : c.status === "approved" ? "ok" : "neutral"}`}>{STATUS[c.status]}</span>
        </div>
        <p className="lead" style={{ margin: "14px 0 0", maxWidth: 820 }}>{named(c.summary, names)}</p>
      </div>

      <div className="grid g2" style={{ padding: 24, gap: 24 }}>
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
            <div key={k} className="between" style={{ alignItems: "flex-start", borderBottom: ".5px solid var(--n5)", paddingBottom: 8 }}>
              <span className="body" style={{ minWidth: 150 }}>{SOURCE[k] ?? k}</span>
              <div style={{ textAlign: "right" }}><Cites cites={ids} /></div>
            </div>
          ))}
        </div>
      </div>

      {c.status === "pending" && (
        <div className="between" style={{ padding: "16px 24px", background: "var(--n0)", borderTop: "1px solid var(--n3)", borderRadius: "0 0 8px 8px" }}>
          <span className="micro muted">Proposed by {c.proposed_by} · only a controller can approve, and never the proposer</span>
          <div className="row">
            <button className="pill" disabled={busy} onClick={() => act(false)}>Decline</button>
            <button className="btn" disabled={busy || mode !== "live"} onClick={() => act(true)} title={mode !== "live" ? "Needs the live feed" : undefined}>
              Approve hold as {state.meta.controller.replace("controller.", "").replace(/^./, (s) => s.toUpperCase())}
            </button>
          </div>
        </div>
      )}
      {c.decided_by && <div className="micro muted" style={{ padding: "12px 24px" }}>Decided by {c.decided_by}</div>}
      {msg && <div className={`notice ${msg.ok ? "ok" : "err"}`} style={{ margin: "0 24px 20px" }}>{msg.text}</div>}
      {mode !== "live" && c.status === "pending" && <div className="notice" style={{ margin: "0 24px 20px" }}>Replay feed: decisions need the live feed, since they go through the rails policy gate.</div>}
    </div>
  );
}

export default function Cases() {
  const { state } = useFeed();
  if (!state) return <><PageTop title="Case queue" /><Loading /></>;
  const order = { pending: 0, approved: 1, declined: 1, superseded: 2 } as const;
  const cases = state.cases.slice().sort((a, b) => order[a.status] - order[b.status] || (a.kind === "funding" ? -1 : 1));
  const blocked = state.guardrails.filter((g) => !g.allowed);
  const caseIds = new Set(state.cases.flatMap((c) => [c.approval_id, c.id]));
  const audit = state.audit.filter((a) => ["case_proposed", "approval_requested", "approval_decided", "chase_sent", "chase_held", "alert_withdrawn", "bank_change_held", "fact_corrected"].includes(a.type)
    || [...caseIds].some((id) => a.what.includes(id)));

  return (
    <>
      <PageTop title="Case queue" sub="Agents propose holds with an evidence pack. You decide. Nothing here moves money." />
      <div className="grid g-main">
        <div className="stack" style={{ gap: 16 }}>
          {cases.map((c) => <CaseCard key={c.approval_id} c={c} />)}
        </div>
        <div className="stack" style={{ gap: 16 }}>
          <div className="card">
            <p className="card-label" style={{ marginBottom: 12 }}>Blocked by the policy gate</p>
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
          </div>
          <div className="card">
            <p className="card-label" style={{ marginBottom: 12 }}>Audit trail</p>
            <div className="stack" style={{ gap: 0 }}>
              {audit.slice(0, 14).map((a) => (
                <div key={a.id} style={{ padding: "10px 0", borderBottom: ".5px solid var(--n5)" }}>
                  <div className="between"><span className="micro warn-ink">{a.actor.replace("_", " ")}</span><span className="micro faint">{a.at.startsWith(state.meta.as_of.slice(0, 4)) && a.at < "2026-05" ? day(a.at) : "live"}</span></div>
                  <div className="body" style={{ fontSize: 13, marginTop: 2 }}>{named(a.what, state.meta.names)}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
