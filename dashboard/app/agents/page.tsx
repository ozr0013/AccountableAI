"use client";

import { Disclosure } from "@/components/Disclosure";
import { useFeed } from "@/components/Feed";
import { ResponseList } from "@/components/Responses";
import { Badge, Loading, PageTop, SectionTitle } from "@/components/ui";
import { CAUSE, day, named } from "@/lib/format";

const ROSTER = [
  { id: "agreement_agent", name: "Agreement agent", role: "Reads the management agreement into terms with clause citations; a controller confirms them.", model: true },
  { id: "trail", name: "Payment-trail agent", role: "Builds one timeline per invoice from funding, statements, bank, vendor and replies; source-trust rules pick the winner.", model: true },
  { id: "cause", name: "Delay-cause agent", role: "Gives every unpaid invoice one cause. Only “unexplained” lands on Keyway Management.", model: true },
  { id: "verifier", name: "Verifier", role: "Tries to refute every alert from the evidence alone; blocks uncited claims and accusatory wording. Fails closed.", model: true },
  { id: "chaser", name: "Chaser agent", role: "Reminds Keyway Management from approved templates on the approved schedule, and reads replies: “paid” counts only with bank or vendor proof.", model: true },
  { id: "case_agent", name: "Case agent", role: "Builds the evidence pack and proposes a hold before Oakline funds Keyway Management again. Proposes only.", model: true },
  { id: "recheck", name: "Recheck", role: "When a fact changes, reruns only the items that depend on it and withdraws alerts that no longer hold, with the reason.", model: false },
  { id: "controller", name: "Controller", role: "The only one who approves holds and bank-detail changes. Every decision is in the log.", model: false },
];

export default function Agents() {
  const { state } = useFeed();
  if (!state) return <><PageTop title="Agents" /><Loading /></>;
  const live = state.meta.agents === "live";
  const byActor: Record<string, typeof state.audit> = {};
  for (const a of state.audit) (byActor[a.actor] ??= []).push(a);
  const session = state.history.flatMap((h, i) => (h.responses ?? []).map((r) => ({ ...r, step: i + 1, summary: h.summary })));

  return (
    <>
      <PageTop title="Agents" sub={`${live ? "Claude agents, metered" : "Rules mode, no model cost"}. Code decides what runs; agents only judge.`} />

      {session.length > 0 && (
        <Disclosure title="Session history" hint="Actions and their agent responses" meta={`${state.history.length} actions`}>
          <div className="stack" style={{ gap: 16 }}>
            {state.history.slice().reverse().map((h, i) => (
              <div key={i} className="card dotted-card">
                <div className="between" style={{ marginBottom: 10 }}>
                  <span className="body" style={{ color: "var(--ink)" }}>{named(h.summary, state.meta.names)}</span>
                  <span className="micro muted">#{state.history.length - i}{h.cost ? ` · $${h.cost.toFixed(4)}` : ""}</span>
                </div>
                <ResponseList responses={h.responses ?? []} dense />
              </div>
            ))}
          </div>
        </Disclosure>
      )}

      <SectionTitle right={live ? "Claude" : "rules"}>The team</SectionTitle>
      <div className="grid g2">
        {ROSTER.map((r) => {
          // trail and cause write no events of their own: their output lives on each item
          const derived = r.id === "trail"
            ? state.items.filter((i) => i.attention && i.trail).map((i) => ({ id: i.id, at: state.meta.as_of, what: `${i.id} ${i.vendor}: ${i.trail!.reasoning}` }))
            : r.id === "cause"
              ? state.items.filter((i) => i.attention && i.cause).map((i) => ({ id: i.id, at: state.meta.as_of, what: `${i.id} ${i.vendor}: ${CAUSE[i.cause!]}` }))
              : null;
          const done = derived ?? byActor[r.id] ?? [];
          return (
            <div key={r.id} className="card stack" style={{ gap: 12 }}>
              <div className="between">
                <span className="row"><Badge kind={done.length ? "done" : "idle"} /><span className="h4 display">{r.name}</span></span>
                <span className={`tag ${r.model ? (live ? "" : "neutral") : "ok"}`}>{r.model ? (live ? "Claude" : "Rules") : r.id === "controller" ? "Person" : "Code"}</span>
              </div>
              <p className="body muted" style={{ margin: 0 }}>{r.role}</p>
              {done.length > 0 && (
                <Disclosure title="Recent work" meta={done.length}>
                  {done.slice(0, 4).map((a) => (
                    <div key={a.id} className="between" style={{ gap: 12, padding: "6px 0", alignItems: "flex-start" }}>
                      <span className="body" style={{ fontSize: "calc(12.5px * var(--dashboard-type-scale, 1))", color: "var(--ink)" }}>{named(a.what, state.meta.names)}</span>
                      <span className="micro faint" style={{ flex: "none" }}>{day(a.at)}</span>
                    </div>
                  ))}
                  {done.length > 4 && <Disclosure title="Earlier work" meta={done.length - 4}>{done.slice(4).map(a => <p key={a.id} className="body">{day(a.at)} · {named(a.what, state.meta.names)}</p>)}</Disclosure>}
                </Disclosure>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}
