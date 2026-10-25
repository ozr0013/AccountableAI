"use client";

import Link from "next/link";
import { Disclosure } from "./Disclosure";
import { useFeed } from "./Feed";
import { Badge } from "./ui";
import { money } from "@/lib/format";
import type { ActionResult, Response } from "@/lib/types";

const TONE: Record<string, "done" | "flag" | "idle" | "no"> = {
  "Keyway Management": "idle", Judge: "idle", Controller: "done",
};

function kindOf(r: Response): "done" | "flag" | "idle" | "no" {
  if (TONE[r.agent]) return TONE[r.agent];
  const w = r.what.toLowerCase();
  if (w.startsWith("blocked") || w.includes("still held") || w.includes("refused")) return "no";
  if (w.includes("withdrew") || w.includes("backed by") || w.includes("still hold") || w.includes("approved")) return "done";
  if (w.includes("holding position") || w.includes("reminder") || w.includes("unexplained") || w.includes("recommend holding")) return "flag";
  return "done";
}

export function ResponseList({ responses, dense }: { responses: Response[]; dense?: boolean }) {
  if (!responses.length) return <div className="body muted">No agent had anything to do.</div>;
  return (
    <ol className="resp">
      {responses.map((r, i) => (
        <li key={i} className="resp-row" style={{ animationDelay: `${Math.min(i, 12) * 70}ms` }}>
          <Badge kind={kindOf(r)} />
          <div className="arow-main">
            <span className="micro warn-ink">{r.agent}</span>
            <span className="body" style={{ fontSize: dense ? 12.5 : 13, color: "var(--ink)", whiteSpace: "normal" }}>
              {r.what}
              {r.invoice_id && !r.what.includes(r.invoice_id) && <Link className="cite" style={{ marginLeft: 6 }} href={`/items/${r.invoice_id}`}>{r.invoice_id}</Link>}
            </span>
          </div>
        </li>
      ))}
    </ol>
  );
}

export function ResultHead({ res }: { res: Extract<ActionResult, { ok: true }> }) {
  const [a, b] = res.diff.unexplained;
  return (
    <div className="stack" style={{ gap: 8 }}>
      <span className="label muted">/ {({ fact: "Judge fact", reply: "Keyway Management replied", decision: "Controller decision", clock: "Clock" } as Record<string, string>)[res.kind] ?? res.kind}</span>
      <p className="body" style={{ margin: 0, color: "var(--ink)", fontSize: 15, lineHeight: "22px" }}>{res.summary}</p>
      <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
        <span className="tag neutral">{res.diff.rechecked.length} rechecked</span>
        <span className="tag">{res.diff.changed.length} changed</span>
        {a !== b && <span className="tag solid">Unexplained {money(a, false)} → {money(b, false)}</span>}
        <span className="tag neutral">{res.live ? `Claude · $${res.cost.toFixed(4)}` : "rules · $0"}</span>
      </div>
    </div>
  );
}

/** Slides in after any action, on any page: what each agent did in response. */
export function ResponsesPanel() {
  const { last, panel, setPanel, busy } = useFeed();
  if (busy) return <div className="toast"><span className="spinner" /> Agents working…</div>;
  if (!panel || !last) return null;
  return (
    <aside className="dock" role="status" aria-live="polite">
      <div className="between" style={{ marginBottom: 12 }}>
        <span className="label">/ Agent responses</span>
        <button className="pill" style={{ height: 26, padding: "0 10px" }} onClick={() => setPanel(false)}>Close</button>
      </div>
      {last.ok ? (
        <div className="stack" style={{ gap: 14 }}>
          <ResultHead res={last} />
          <Disclosure title="What the agents did" meta={last.responses?.length ?? 0}><div className="dock-scroll" data-lenis-prevent><ResponseList responses={last.responses ?? []} dense /></div></Disclosure>
        </div>
      ) : (
        <div className="notice err">{last.error}</div>
      )}
    </aside>
  );
}
