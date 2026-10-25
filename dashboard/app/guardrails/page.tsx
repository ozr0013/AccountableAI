"use client";

import { Disclosure } from "@/components/Disclosure";
import { useFeed } from "@/components/Feed";
import { Badge, Loading, PageTop, SectionTitle } from "@/components/ui";

const RULES = [
  ["Never move money", "There is no payment function anywhere. pay, wire, transfer and release exist only to be refused."],
  ["Agents recommend, people hold", "The case agent proposes a hold; only the controller places it, and never the proposer."],
  ["Approved templates only", "Reminders to Keyway Management use controller-approved templates on an approved schedule. Anything else needs approval of that exact draft."],
  ["Never alert without a cited record", "Every claim cites the funding record, the vendor's evidence and the agreement clause. The verifier blocks uncited claims."],
  ["Never accuse", "Wording tops out at “unexplained”. Accusatory terms are blocked in alerts and outbound mail."],
  ["Bank-detail changes are held", "Vendor bank changes wait for identity verification and a controller who didn't request them."],
  ["Append-only history", "Corrections supersede facts; nothing is overwritten. Books must balance after every replay."],
];

export default function Guardrails() {
  const { state } = useFeed();
  if (!state) return <><PageTop title="Guardrails" /><Loading /></>;
  return (
    <>
      <PageTop title="Guardrails" sub="Rules the system can't break, enforced in code." />
      <Disclosure title="Policy check results" hint="Inspect actions, actors, and reasons" meta={`${state.guardrails.filter((g) => !g.allowed).length} refused`}>
      <div className="card" style={{ padding: "16px 8px 4px" }}>
        <table className="table">
          <thead><tr><th>Attempt</th><th>Action</th><th>Actor</th><th>Gate</th><th>Reason</th></tr></thead>
          <tbody>
            {state.guardrails.map((g) => (
              <tr key={g.label}>
                <td className="vendor"><span className="row"><Badge kind={g.allowed ? "done" : "no"} />{g.label}</span></td>
                <td className="mono muted" style={{ fontSize: "calc(12px * var(--dashboard-type-scale, 1))" }}>{g.action}</td>
                <td className="mono muted" style={{ fontSize: "calc(12px * var(--dashboard-type-scale, 1))" }}>{g.actor}</td>
                <td>{g.allowed ? <span className="tag ok">Allowed</span> : <span className="tag wine">Refused</span>}</td>
                <td className="body" style={{ fontSize: "calc(13px * var(--dashboard-type-scale, 1))" }}>{g.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      </Disclosure>
      <section className="section">
        <SectionTitle>The rules</SectionTitle>
        <div className="grid g3">
          {RULES.map(([t, d], i) => (
            <Disclosure key={t} title={t} meta={String(i + 1).padStart(2, "0")}><p className="body" style={{ margin: 0 }}>{d}</p></Disclosure>
          ))}
        </div>
      </section>
    </>
  );
}
