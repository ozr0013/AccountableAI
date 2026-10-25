"use client";

import { Disclosure } from "@/components/Disclosure";
import { useFeed } from "@/components/Feed";
import { ResponseList } from "@/components/Responses";
import { Badge, PageTop, SectionTitle } from "@/components/ui";
import { day, named } from "@/lib/format";
import ROSTER from "@/lib/agent-prompts.json";

export default function Agents() {
  const { state } = useFeed();
  const live = state?.meta.agents === "live";
  const history = state?.history ?? [];
  const hasResponses = history.some((h) => h.responses?.length);

  return (
    <>
      <PageTop title="Agents" sub="Four agents. Read their roles, prompts, and recent activity." />

      {state && hasResponses && (
        <Disclosure title="Session history" hint="Actions and their agent responses" meta={`${history.length} actions`}>
          <div className="stack" style={{ gap: 16 }}>
            {history.slice().reverse().map((h, i) => (
              <div key={i} className="card dotted-card">
                <div className="between" style={{ marginBottom: 10 }}>
                  <span className="body" style={{ color: "var(--ink)" }}>{named(h.summary, state.meta.names)}</span>
                  <span className="micro muted">#{history.length - i}{h.cost ? ` · $${h.cost.toFixed(4)}` : ""}</span>
                </div>
                <ResponseList responses={h.responses ?? []} dense />
              </div>
            ))}
          </div>
        </Disclosure>
      )}

      <SectionTitle right="4 agents">The team</SectionTitle>
      <p className="body muted">Open an agent’s prompt to inspect its instructions. The controller approves holds and protected actions; reconciliation and rechecks run in code.</p>
      <div className="grid g2">
        {ROSTER.map((r) => {
          const done = (state?.audit ?? []).filter((a) => r.actors.includes(a.actor)).slice().reverse();
          return (
            <article key={r.id} className="card stack agent-card" style={{ gap: 12 }} aria-labelledby={`agent-${r.id}`}>
              <div className="between">
                <div className="row"><Badge kind={done.length ? "done" : "idle"} /><h2 id={`agent-${r.id}`} className="h4 display">{r.name}</h2></div>
                <span className="tag neutral">{state ? (live ? "Claude" : "Rules") : "Agent"}</span>
              </div>
              <p className="body muted" style={{ margin: 0 }}>{r.role}</p>
              <Disclosure title="View prompt" hint="System instructions and guidance">
                {r.sections.map((section) => <section className="agent-prompt-section" key={section.title}>
                  <h3 className="h4 display">{section.title}</h3>
                  {section.note && <p className="body muted">{section.note}</p>}
                  <pre className="agent-prompt">{section.text}</pre>
                  <p className="micro muted agent-prompt-source">Source: {section.source}</p>
                </section>)}
              </Disclosure>
              {done.length > 0 && (
                <Disclosure title="Recent work" meta={done.length}>
                  {done.slice(0, 4).map((a) => (
                    <div key={a.id} className="between" style={{ gap: 12, padding: "6px 0", alignItems: "flex-start" }}>
                      <span className="body" style={{ fontSize: "calc(12.5px * var(--dashboard-type-scale, 1))", color: "var(--ink)" }}>{named(a.what, state?.meta.names ?? {})}</span>
                      <span className="micro faint" style={{ flex: "none" }}>{day(a.at)}</span>
                    </div>
                  ))}
                  {done.length > 4 && <Disclosure title="Earlier work" meta={done.length - 4}>{done.slice(4).map(a => <p key={a.id} className="body">{day(a.at)} · {named(a.what, state?.meta.names ?? {})}</p>)}</Disclosure>}
                </Disclosure>
              )}
            </article>
          );
        })}
      </div>
    </>
  );
}
