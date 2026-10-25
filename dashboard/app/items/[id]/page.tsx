"use client";

import Link from "next/link";
import { Disclosure } from "@/components/Disclosure";
import { useParams } from "next/navigation";
import { useFeed } from "@/components/Feed";
import { causeTag, onManager } from "@/components/ItemsTable";
import { Timeline } from "@/components/Timeline";
import { Badge, Cites, Loading, PageTop, SectionTitle } from "@/components/ui";
import { ALERT, CAUSE, day, money, named, SOURCE } from "@/lib/format";

export default function ItemPage() {
  const { id } = useParams<{ id: string }>();
  const { state } = useFeed();
  if (!state) return <><PageTop title={id} /><Loading /></>;
  const it = state.items.find((i) => i.id === id);
  if (!it) return <><PageTop title={id} /><div className="notice err">No invoice {id} for Keyway Management.</div></>;
  const names = state.meta.names;
  const alerts = state.alerts.filter((a) => a.invoice_ids.includes(it.id) && a.kind !== "recurring_pattern");
  const docs = Object.values(state.documents).filter((d) => d.data?.invoice_id === it.id || it.trail?.entries.some((e) => e.source_id === d.id));
  const changed = state.history.flatMap((h) => h.diff.changed.filter((c) => c.id === it.id));

  const facts: [string, string, string?][] = [
    ["Invoiced", money(it.amount), day(it.received)],
    ["Oakline funded", money(it.funded), day(it.funded_on)],
    ["Paid to vendor", money(it.paid), it.paid_on ? day(it.paid_on) : "no proof"],
    ["Still owed", money(it.owed), it.owed > 0 && it.days_late > 0 ? `${it.days_late} days past due` : ""],
    ["Due", day(it.due), `${state.terms.vendor_payment_days ?? 10} days from receipt · 7.2`],
  ];

  return (
    <>
      <PageTop title={it.vendor} sub={<><span className="mono">{it.id}</span> · {it.trade} · <Link className="link" href="/items">All open items</Link></>}>
        {causeTag(it)}
      </PageTop>

      <div className="grid invoice-facts">
        {facts.map(([k, v, s]) => (
          <div key={k} className="card" style={{ padding: 16 }}>
            <p className="card-label" style={{ fontSize: 13 }}>{k}</p>
            <div className="display h3 num" style={{ color: k === "Still owed" && onManager(it) ? "var(--o)" : undefined }}>{v}</div>
            <div className="micro muted" style={{ marginTop: 6 }}>{s}</div>
          </div>
        ))}
      </div>

      {changed.length > 0 && (
        <Disclosure title="What changed" hint="Corrections to this invoice" meta={changed.length}>
          <div className="diff">
            {changed.flatMap((c) => Object.entries(c.delta).filter(([k]) => k !== "alerts").map(([k, [a, b]]) => (
              <div key={k} className="stack" style={{ gap: 2 }}>
                <span className="minus">{k} {String(a ?? "none")}</span>
                <span className="plus">{k} {String(b ?? "none")}</span>
              </div>
            )))}
          </div>
        </Disclosure>
      )}

      <Disclosure title="Payment timeline" hint="Compare the records behind this invoice" meta={it.trail?.conflict ? "Sources disagree" : undefined}>
        <div className="card" style={{ paddingTop: 28 }}>
          <Timeline item={it} asOf={state.meta.as_of} />
          {it.trail && (
            <div className="notice" style={{ marginTop: 20 }}>
              <strong>Source-trust rules:</strong> {it.trail.reasoning} <span className="muted">Winner: {it.trail.winning_claim}.</span>
            </div>
          )}
        </div>
      </Disclosure>

      <div className="grid g2 section">
        <div>
          <SectionTitle>Delay cause</SectionTitle>
          <div className="card">
            {it.cause ? (
              <div className="stack" style={{ gap: 12 }}>
                <div className="between">
                  <span className="display h3">{CAUSE[it.cause]}</span>
                  {it.cause_confidence != null && <span className="micro muted">confidence {(it.cause_confidence * 100).toFixed(0)}%</span>}
                </div>
                <p className="body" style={{ margin: 0 }}>{named(it.cause_text, names)}</p>
                <div>{it.cause_citations && <Cites cites={it.cause_citations} />}</div>
                <div className="micro" style={{ color: onManager(it) ? "var(--o)" : "var(--g-ink)" }}>
                  {onManager(it) ? "Unexplained: alert on Keyway Management, chaser follows up" : "Not Keyway Management's fault: no alert on the manager"}
                </div>
              </div>
            ) : (
              <p className="body muted" style={{ margin: 0 }}>{it.note ? named(it.note, names) : "No cause needed."}</p>
            )}
          </div>
        </div>
        <div>
          <SectionTitle>Alerts on this item</SectionTitle>
          <div className="stack" style={{ gap: 12 }}>
            {alerts.length === 0 && <div className="card body muted">No alert. The evidence supports Keyway Management on this one.</div>}
            {alerts.map((a) => (
              <div key={a.id} className="card">
                <div className="between" style={{ marginBottom: 10 }}>
                  <div className="row"><Badge kind={a.open ? (a.kind === "unexplained_unpaid" ? "flag" : "idle") : "done"} /><span className="h4 display">{ALERT[a.kind]}</span></div>
                  <span className={`tag ${a.open ? "" : "neutral"}`}>{a.open ? "Open" : "Withdrawn"}</span>
                </div>
                <Disclosure title="Claims & citations" meta={a.claims.length}>
                {a.claims.map((c, i) => (
                  <div key={i} style={{ marginBottom: 10 }}>
                    <p className="body" style={{ margin: "0 0 6px" }}>{named(c.text, names)}</p>
                    <Cites cites={c.citations} />
                  </div>
                ))}
                </Disclosure>
                {!a.open && a.withdrawn_reason && <div className="notice ok">Withdrawn: {named(a.withdrawn_reason, names)}</div>}
                <div className="between" style={{ marginTop: 8 }}>
                  <span className="micro muted">Verifier: {a.verdict.status}</span>
                  {a.recommended_action && <span className="micro warn-ink">{a.recommended_action.replaceAll("_", " ")}</span>}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <Disclosure title="Source documents" hint="Read the underlying evidence" meta={`${docs.length} documents`}>
        <div className="grid g3">
          {docs.sort((a, b) => (a.dated ?? "").localeCompare(b.dated ?? "")).map((d) => (
            <div key={d.id} className={`doc ${it.trail?.winning.includes(d.id) ? "hl" : ""}`}>
              <div className="between" style={{ marginBottom: 6 }}>
                <span className="micro muted">{SOURCE[d.kind] ?? d.kind} · {day(d.dated)}</span>
                <Cites cites={[d.id]} />
              </div>
              <div style={{ display: "-webkit-box", WebkitLineClamp: 4, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{d.text}</div>
            </div>
          ))}
        </div>
      </Disclosure>
    </>
  );
}
