"use client";

import { Disclosure } from "@/components/Disclosure";
import { useFeed } from "@/components/Feed";
import { MoneyStrip } from "@/components/MoneyStrip";
import { ReplyBox } from "@/components/ReplyBox";
import { Cites, Loading, PageTop, SectionTitle } from "@/components/ui";
import { day, money, month, named } from "@/lib/format";

export default function Keyway() {
  const { state } = useFeed();
  if (!state) return <><PageTop title="Keyway Property Management" /><Loading /></>;
  const r = state.recon;
  const h = state.headline;
  const explained = Math.max(0, r.owed - h.unexplained);
  const parts = [
    { k: "Paid", v: r.paid, c: "var(--g-ink)", note: "bank record or vendor confirmation" },
    { k: "Authorized holds", v: r.held, c: "#8e9fa8", note: "10% until inspection · clause 7.4" },
    { k: "Owed, explained", v: explained, c: "var(--n7)", note: "paperwork, bank-detail check, not due" },
    { k: "Owed, unexplained", v: h.unexplained, c: "var(--o)", note: "alert on Keyway Management" },
    { k: "Funded twice", v: r.duplicate_funded, c: "var(--wine)", note: "duplicate billing flagged" },
  ];
  const total = parts.reduce((s, p) => s + p.v, 0);
  const late = state.items.filter((i) => i.paid_late);
  const months = Object.entries(state.late_months);
  const agreement = Object.values(state.documents).find((d) => d.kind === "agreement");
  const thread = [
    ...state.chases.map((c) => ({ at: c.sent_at, out: true as const, c })),
    ...state.replies.map((r) => ({ at: r.on ?? "", out: false as const, r })),
  ].sort((a, b) => a.at.localeCompare(b.at));

  return (
    <>
      <PageTop title={state.meta.manager} sub="Every dollar from funded to paid." />

      <MoneyStrip items={state.items} />
      <Disclosure title="Where the money went" hint="Reconciliation and payment breakdown">
      <div className="card">
        <div className="bar" style={{ height: 36 }}>
          {parts.map((p) => <div key={p.k} style={{ width: `${(p.v / total) * 100}%`, background: p.c, minWidth: p.v ? 3 : 0 }} title={`${p.k}: ${money(p.v)}`} />)}
        </div>
        <div className="legend">
          {parts.map((p) => (
            <div key={p.k} className="legend-item">
              <span className="sw" style={{ background: p.c }} />
              <div><div className="body" style={{ color: "var(--ink)" }}>{p.k} <span className="num" style={{ fontWeight: 500 }}>{money(p.v)}</span></div><div className="micro muted">{p.note}</div></div>
            </div>
          ))}
        </div>
        <div className="notice" style={{ marginTop: 16 }}>
          Reconciles: funded {money(r.funded)} = paid + holds + owed + funded twice. Oakline also has {money(r.unfunded)} it hasn&apos;t funded yet (the HVAC replacement over the $5,000 approval limit).
        </div>
      </div>

      </Disclosure>
      <Disclosure title="Payment history & agreement" hint="Late payments, confirmed terms, and aging">
      <div className="grid g2">
        <div>
          <SectionTitle right={`${late.length} late payments`}>Paid on time? Against clause 7.2</SectionTitle>
          <div className="months">
            {months.map(([m, isLate]) => {
              const n = late.filter((i) => i.paid_on?.startsWith(m)).length;
              return (
                <div key={m} className={`mon ${isLate ? "late" : "ontime"}`}>
                  <span className="label">{month(m)}</span>
                  <div>
                    <div className={`display h2 ${isLate ? "warn" : "ok"}`}>{isLate ? "Late" : "On time"}</div>
                    <div className="micro muted" style={{ marginTop: 6 }}>{n} invoice{n === 1 ? "" : "s"} paid past due</div>
                  </div>
                </div>
              );
            })}
          </div>
          <p className="body muted" style={{ marginTop: 12 }}>Late in 3 of 4 months, measured against Keyway Management&apos;s own agreement, not other managers. Recommendation: stricter terms.</p>
        </div>
        <div>
          <SectionTitle>Agreement terms (confirmed)</SectionTitle>
          <div className="card stack" style={{ gap: 0, padding: "4px 20px" }}>
            {[
              ["7.2", "Pay vendors within", `${state.terms.vendor_payment_days} days of receipt`],
              ["7.4", "Holdback until inspection", `${state.terms.holdback_pct}%`],
              ["8.1", "Owner approval above", money(Number(state.terms.approval_limit), false)],
            ].map(([c, k, v]) => (
              <div key={c} className="between" style={{ padding: "16px 0", borderBottom: ".5px solid var(--n5)" }}>
                <span className="row"><span className="tag neutral">{c}</span><span className="body">{k}</span></span>
                <span className="display h4 num">{v}</span>
              </div>
            ))}
            <div style={{ padding: "14px 0" }}>{agreement && <Cites cites={[agreement.id]} />}<span className="micro muted"> extracted by the agreement agent, confirmed by the controller</span></div>
          </div>
          <SectionTitle>Aging of what Keyway Management owes</SectionTitle>
          <div className="grid g4" style={{ gap: 8 }}>
            {["current", "1-30", "31-60", "61-90"].map((b) => (
              <div key={b} className="card" style={{ padding: 14 }}>
                <div className="micro muted">{b === "current" ? "Not due" : `${b} days`}</div>
                <div className="h4 display num" style={{ marginTop: 6 }}>{money(state.aging[b] ?? 0, false)}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      </Disclosure>
      <section style={{ marginTop: 24 }}>
        <Disclosure title="Add a reply from Keyway Management" hint="Demo: test a payment claim, with or without proof"><ReplyBox /></Disclosure>
        <Disclosure title="Conversation history" hint="Reminders and Keyway Management’s replies" meta={`${thread.length} messages`}>
        <div className="card" style={{ marginTop: 16 }}>
          <div className="thread">
            {thread.map((t) => t.out ? (
              <div key={t.c.id} className="msg out">
                <div className="msg-meta">
                  <span className={`tag ${t.c.level === "polite" ? "neutral" : ""}`}>{t.c.level}</span>
                  <span className="micro muted">Chaser → Keyway Management · {day(t.c.sent_at)} · {t.c.template_id} · {t.c.sent ? "sent" : `held: ${t.c.policy}`}</span>
                </div>
                {named(t.c.body, state.meta.names)}
              </div>
            ) : (
              <div key={t.r.id} className="msg in">
                <div className="msg-meta">
                  <span className="tag wine">Keyway Management reply{t.r.judge ? " · judge" : ""}</span>
                  <span className="micro muted">{day(t.r.on)} · {t.r.invoice_id}</span>
                </div>
                &ldquo;{t.r.text}&rdquo;
                <div className="micro warn-ink" style={{ marginTop: 8, textTransform: "none", letterSpacing: 0 }}>
                  {t.r.verdict ?? (t.r.claims_paid ? "Claims paid, with no bank record or vendor confirmation: holding position, alert stays open." : "")}
                </div>
              </div>
            ))}
          </div>
        </div>
        </Disclosure>
      </section>
    </>
  );
}
