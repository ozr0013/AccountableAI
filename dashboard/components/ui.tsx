"use client";

import { createContext, useContext, useState, type ReactNode } from "react";
import { useFeed } from "./Feed";
import { Check, Cross, Flag } from "./Icons";
import { day, SOURCE } from "@/lib/format";
import type { Citation } from "@/lib/types";

export function SectionTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="section-title">
      <span className="label">/ {children}</span>
      <span className="label">{right ?? "■"}</span>
    </div>
  );
}

export function Badge({ kind }: { kind: "done" | "flag" | "idle" | "no" }) {
  if (kind === "done") return <span className="badge done"><Check /></span>;
  if (kind === "flag") return <span className="badge flag"><Flag /></span>;
  if (kind === "no") return <span className="badge wine"><Cross /></span>;
  return <span className="badge idle" />;
}

export function PageTop({ title, sub, children, meta = true }: { title: string; sub?: ReactNode; children?: ReactNode; meta?: boolean }) {
  const { state, mode } = useFeed();
  return (
    <div className="top">
      <div className="stack" style={{ gap: 6 }}>
        <h1 className="display h2">{title}</h1>
        {sub && <div className="body muted">{sub}</div>}
      </div>
      <div className="top-meta">
        {children}
        {meta && state && <span className="chip">As of {day(state.meta.as_of, true)}</span>}
        {meta && <span className={`chip ${mode}`} title={mode === "live" ? "dashboard/feed/serve.py" : "public/feed/*.json"}>
          {mode === "live" ? "Live feed" : "Replay feed"}
        </span>}
      </div>
    </div>
  );
}

export function Loading() {
  const { error } = useFeed();
  if (error) return <div className="notice err">Couldn&apos;t load the feed: {error}</div>;
  return <div className="grid" style={{ gap: 16 }}><div className="skeleton" /><div className="skeleton" /></div>;
}

// ---- evidence drawer: any citation opens the source document ----
const DocCtx = createContext<(id: string) => void>(() => {});
export const useOpenDoc = () => useContext(DocCtx);

export function DocProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState<string | null>(null);
  const { state } = useFeed();
  const doc = open ? state?.documents[open] : null;
  return (
    <DocCtx.Provider value={setOpen}>
      {children}
      {open && (
        <>
          <div className="scrim" onClick={() => setOpen(null)} />
          <aside className="drawer" data-lenis-prevent role="dialog" aria-label="Source document">
            <div className="between" style={{ marginBottom: 20 }}>
              <span className="label muted">/ Source document</span>
              <button className="pill" onClick={() => setOpen(null)}>Close</button>
            </div>
            {doc ? (
              <div className="stack" style={{ gap: 14 }}>
                <div className="row"><span className="tag neutral">{SOURCE[doc.kind] ?? doc.kind}</span><span className="micro muted">{day(doc.dated, true)}</span></div>
                <h2 className="display h4 mono" style={{ fontFamily: "var(--mono)", letterSpacing: 0 }}>{doc.id}</h2>
                <div className="doc" style={{ whiteSpace: "pre-wrap" }}>{doc.text}</div>
                {Object.keys(doc.data).length > 0 && (
                  <pre className="doc mono" style={{ fontSize: 11, lineHeight: "16px", overflow: "auto", margin: 0 }}>
                    {JSON.stringify(doc.data, null, 2)}
                  </pre>
                )}
              </div>
            ) : (
              <div className="notice">{open} is an event in the log, not a document (for example a judge fact or a workflow record).</div>
            )}
          </aside>
        </>
      )}
    </DocCtx.Provider>
  );
}

export function Cites({ cites }: { cites: (Citation | string)[] }) {
  const openDoc = useOpenDoc();
  const seen = new Set<string>();
  return (
    <span>
      {cites.map((c) => {
        const id = typeof c === "string" ? c : c.source_id;
        if (seen.has(id)) return null;
        seen.add(id);
        const loc = typeof c === "string" ? null : c.locator;
        return (
          <button key={id} className="cite" onClick={() => openDoc(id)} title="Open source">
            {id}{loc ? ` · ${loc}` : ""}
          </button>
        );
      })}
    </span>
  );
}

/** Small area chart in the accent, like a stat-card trend. */
export function Spark({ values, height = 56, tone = "o" }: { values: number[]; height?: number; tone?: "o" | "g" }) {
  if (values.length < 2) return null;
  const w = 260, h = height, max = Math.max(...values) || 1, min = Math.min(0, ...values);
  const pts = values.map((v, i) => [(i / (values.length - 1)) * w, h - 4 - ((v - min) / (max - min || 1)) * (h - 10)]);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
  const stroke = tone === "o" ? "var(--o)" : "var(--g-ink)";
  const fill = tone === "o" ? "var(--o-wash)" : "var(--g-wash)";
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} preserveAspectRatio="none" aria-hidden>
      <path d={`${line} L${w} ${h} L0 ${h} Z`} fill={fill} />
      <path d={line} fill="none" stroke={stroke} strokeWidth="1.2" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
