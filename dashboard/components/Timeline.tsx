"use client";

// Payment timeline: one lane per source. The source-trust rules pick the winner; that lane is
// marked trusted, and claims it overrules are struck through.

import { useOpenDoc } from "./ui";
import { day, SOURCE } from "@/lib/format";
import type { Item, TrailEntry } from "@/lib/types";

const ORDER = ["funding", "vendor_invoice", "manager_statement", "manager_reply", "manager_bank", "vendor_confirmation", "vendor_notice", "judge"];
const RANK: Record<string, string> = {
  manager_bank: "rank 1", vendor_confirmation: "rank 2", vendor_notice: "rank 2",
  manager_statement: "rank 4", manager_reply: "rank 5",
};

const ms = (iso: string) => Date.parse(iso.slice(0, 10) + "T00:00:00Z");

function tone(e: TrailEntry): "good" | "bad" | "claim" | "" {
  const c = e.claim.toLowerCase();
  if (e.kind === "manager_statement" || e.kind === "manager_reply") return "claim";
  if (c.includes("no payment") || c.includes("not received") || c.includes("past due") || c.includes("unpaid")) return "bad";
  if (c.includes("paid") || c.includes("funded") || c.includes("confirms")) return "good";
  return "";
}

export function Timeline({ item, asOf }: { item: Item; asOf: string }) {
  const openDoc = useOpenDoc();
  const entries = item.trail?.entries ?? [];
  const trail = item.trail;
  const winningKinds = new Set(entries.filter((e) => trail?.winning.includes(e.source_id)).map((e) => e.kind));
  const lanes = ORDER.filter((k) => entries.some((e) => e.kind === k));
  if (!entries.length) return <div className="empty">No payment trail for this item (nothing to resolve).</div>;

  const dates = [...entries.map((e) => e.on).filter(Boolean) as string[], asOf, item.due ?? asOf];
  const lo = Math.min(...dates.map(ms)) - 3 * 864e5;
  const hi = Math.max(...dates.map(ms)) + 4 * 864e5;
  const x = (iso: string) => `${(((ms(iso) - lo) / (hi - lo)) * 100).toFixed(2)}%`;
  const ticks: string[] = [];
  for (let t = lo; t <= hi; t += 7 * 864e5) ticks.push(new Date(t).toISOString().slice(0, 10));
  const overrule = trail?.conflict && trail.winning_claim === "unpaid";

  return (
    <div>
      <div className="lanes">
        {lanes.map((k) => {
          const trusted = winningKinds.has(k);
          const overruled = overrule && (k === "manager_statement" || k === "manager_reply");
          const evs = entries.filter((e) => e.kind === k && e.on).sort((a, b) => ms(a.on!) - ms(b.on!));
          // Repeated identical claims (six bank records all saying "no payment") become one chip
          // spanning their dates, so the lane stays readable.
          const groups: { e: TrailEntry; first: string; n: number; ids: string[] }[] = [];
          for (const e of evs) {
            const g = groups.at(-1);
            if (g && g.e.claim === e.claim) { g.e = e; g.n += 1; g.ids.push(e.source_id); }
            else groups.push({ e, first: e.on!, n: 1, ids: [e.source_id] });
          }
          let prev = -Infinity, level = 0;
          const placed = groups.map((g) => {
            const t = ms(g.e.on!);
            level = (t - prev) / (hi - lo) < 0.11 ? (level + 1) % 3 : 0;
            prev = t;
            return { ...g, dy: [0, -15, 15][level] };
          });
          const tall = placed.some((p) => p.dy !== 0);
          return (
            <div key={k} className={`lane ${trusted ? "trusted" : ""} ${overruled ? "overruled" : ""}`} style={{ display: "contents" }}>
              <div className="lane-name" style={{ minHeight: tall ? 76 : 52 }}>
                <span>{SOURCE[k] ?? k}</span>
                {trusted && <span className="tag ok">Trusted</span>}
                {overruled && <span className="tag neutral">Overruled</span>}
                {!trusted && !overruled && RANK[k] && <span className="micro faint">{RANK[k]}</span>}
              </div>
              <div className="lane-track" style={{ minHeight: tall ? 76 : 52 }}>
                {placed.map(({ e, dy, first, n, ids }) => (
                  <span key={e.source_id + e.on + e.claim}>
                    {n > 1 && <i className="span-bar" style={{ left: x(first), width: `calc(${x(e.on!)} - ${x(first)})` }} />}
                    <button
                      className={`ev ${tone(e)} ${overruled ? "overruled" : ""}`}
                      style={{ left: x(e.on!), transform: `translate(-50%, calc(-50% + ${dy}px))` }}
                      onClick={() => openDoc(e.source_id)}
                      title={ids.join(", ")}
                    >
                      <i className="dot" />
                      <span>{e.claim}{n > 1 ? ` · ${n} records, ${day(first)} to ${day(e.on)}` : ""}</span>
                    </button>
                  </span>
                ))}
                {item.due && <div className="dueline" style={{ left: x(item.due) }} />}
                <div className="today" style={{ left: x(asOf) }} />
              </div>
            </div>
          );
        })}
      </div>
      <div className="axis">
        <div />
        <div className="axis-track">
          {ticks.map((t) => <span key={t} className="tick micro faint" style={{ left: x(t) }}>{day(t)}</span>)}
          {item.due && <span className="tick micro" style={{ left: x(item.due), top: -2, color: "var(--n11)", background: "var(--white)", padding: "0 3px" }}>Due {day(item.due)}</span>}
          <span className="tick micro warn" style={{ left: x(asOf), top: -2, background: "var(--white)", padding: "0 3px" }}>Today</span>
        </div>
      </div>
    </div>
  );
}
