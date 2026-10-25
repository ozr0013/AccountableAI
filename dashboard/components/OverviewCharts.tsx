"use client";

// Overview's two charts, from the same invoices every tab reads:
//  - what Oakline sent Keyway, by week, month or quarter (one series: columns, no legend);
//  - what's still unpaid to vendors, by vendor type, split past due (status red, labelled) and not.

import { type RefObject, useEffect, useMemo, useRef, useState } from "react";
import { money } from "@/lib/format";
import type { Item } from "@/lib/types";

type Grain = "week" | "month" | "quarter";
const BLUE = "#2d5bff";
const RED = "#d03b3b";                   // status: critical. Always next to the words "past due".
const TRADE: Record<string, string> = {
  hvac: "HVAC", cleaner: "Cleaning", plumber: "Plumbing", roofer: "Roofing", landscaper: "Landscaping",
  electrician: "Electrical", handyman: "Handyman", painter: "Painting",
};
/** Owed and past its due date. Not money the agreement lets Keyway retain, and not a bill sent twice. */
export const pastDue = (i: Item) => i.owed > 0 && i.days_late > 0 && !i.duplicate_of && i.cause !== "authorized_hold";
const tradeName = (t: string | null | undefined) => (t ? TRADE[t] ?? t[0].toUpperCase() + t.slice(1) : "Other");
const short = (n: number) => (n >= 1000 ? `$${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : `$${Math.round(n)}`);

/** A bar with its data end rounded and its base square on the axis. */
function bar(x: number, y: number, w: number, h: number, r = 4, horizontal = false): string {
  if (w <= 0 || h <= 0) return "";
  if (horizontal) {
    const k = Math.min(r, w, h / 2);
    return `M${x},${y} H${x + w - k} Q${x + w},${y} ${x + w},${y + k} V${y + h - k} Q${x + w},${y + h} ${x + w - k},${y + h} H${x} Z`;
  }
  const k = Math.min(r, h, w / 2);
  return `M${x},${y + h} V${y + k} Q${x},${y} ${x + k},${y} H${x + w - k} Q${x + w},${y} ${x + w},${y + k} V${y + h} Z`;
}

function periodOf(d: string, g: Grain): { key: string; label: string; start: number } {
  const t = new Date(d + "T00:00:00Z");
  if (g === "month") return { key: d.slice(0, 7), label: t.toLocaleDateString("en-US", { month: "short", timeZone: "UTC" }), start: Date.UTC(t.getUTCFullYear(), t.getUTCMonth(), 1) };
  if (g === "quarter") {
    const q = Math.floor(t.getUTCMonth() / 3);
    return { key: `${t.getUTCFullYear()}-Q${q + 1}`, label: `Q${q + 1} ${t.getUTCFullYear()}`, start: Date.UTC(t.getUTCFullYear(), q * 3, 1) };
  }
  const mon = new Date(t); mon.setUTCDate(t.getUTCDate() - ((t.getUTCDay() + 6) % 7));     // weeks start Monday
  return { key: mon.toISOString().slice(0, 10), label: mon.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" }), start: mon.getTime() };
}

/** A top for the axis that splits into four round steps (10k, 20k, 2.5k…). */
function niceMax(v: number): number {
  if (v <= 0) return 4;
  const raw = v / 4, p = 10 ** Math.floor(Math.log10(raw));
  return 4 * ([1, 2, 2.5, 5, 10].map((m) => m * p).find((m) => m >= raw) ?? raw);
}

type Tip = { x: number; y: number; title: string; lines: string[] } | null;

/** Draw at the width the chart really has, so text stays at its real size. */
function useWidth(ref: RefObject<HTMLDivElement | null>, fallback: number): number {
  const [w, setW] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

export function FundingChart({ items }: { items: Item[] }) {
  const [grain, setGrain] = useState<Grain>("month");
  const [tip, setTip] = useState<Tip>(null);
  const box = useRef<HTMLDivElement>(null);
  const W = useWidth(box, 560);
  const rows = useMemo(() => {
    const m = new Map<string, { label: string; start: number; sum: number; n: number }>();
    for (const i of items) {
      if (!i.funded_on || !i.funded) continue;
      const p = periodOf(i.funded_on, grain);
      const r = m.get(p.key) ?? { label: p.label, start: p.start, sum: 0, n: 0 };
      r.sum += i.funded; r.n += 1; m.set(p.key, r);
    }
    const out = [...m.values()].sort((a, b) => a.start - b.start);
    if (grain === "week" && out.length) {                     // empty weeks show as empty, not skipped
      const filled: typeof out = [];
      for (let t = out[0].start; t <= out[out.length - 1].start; t += 7 * 864e5) {
        const hit = out.find((r) => r.start === t);
        filled.push(hit ?? { label: new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" }), start: t, sum: 0, n: 0 });
      }
      return filled;
    }
    return out;
  }, [items, grain]);

  const H = 240, L = 52, R = 8, T = 12, B = 28;
  const max = niceMax(Math.max(...rows.map((r) => r.sum), 0));
  const step = (W - L - R) / Math.max(rows.length, 1);
  const bw = Math.min(56, step - 2);
  const y = (v: number) => T + (H - T - B) * (1 - v / max);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max);
  const total = rows.reduce((t, r) => t + r.sum, 0);

  return (
    <figure className="ov-chart">
      <figcaption>
        <div><b>Sent to Keyway by Oakline</b><span>{money(total)} across {rows.length} {grain === "week" ? "weeks" : grain === "month" ? "months" : "quarters"}</span></div>
        <div className="ov-seg" role="tablist" aria-label="Group by">
          {(["week", "month", "quarter"] as Grain[]).map((g) => <button key={g} role="tab" aria-selected={grain === g} className={grain === g ? "on" : ""} onClick={() => setGrain(g)}>{g === "week" ? "Weekly" : g === "month" ? "Monthly" : "Quarterly"}</button>)}
        </div>
      </figcaption>
      <div className="ov-plot" ref={box} onMouseLeave={() => setTip(null)}>
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Funding to Keyway by ${grain}: ${rows.map((r) => `${r.label} ${money(r.sum)}`).join(", ")}`}>
          {ticks.map((t) => <g key={t}><line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className="ov-grid" /><text x={L - 8} y={y(t) + 4} textAnchor="end" className="ov-tick">{short(t)}</text></g>)}
          {rows.map((r, i) => {
            const cx = L + step * i + step / 2;
            const show = rows.length * 64 <= W - L || i % Math.ceil((rows.length * 64) / (W - L)) === 0;
            return <g key={r.start}>
              <path d={bar(cx - bw / 2, y(r.sum), bw, H - B - y(r.sum))} fill={BLUE} opacity={tip && tip.title !== r.label ? 0.55 : 1} />
              {show && <text x={cx} y={H - 8} textAnchor="middle" className="ov-tick">{r.label}</text>}
              <rect x={L + step * i} y={T} width={step} height={H - T - B} fill="transparent"
                onMouseEnter={() => setTip({ x: (cx / W) * 100, y: (y(r.sum) / H) * 100, title: r.label, lines: [money(r.sum), `${r.n} invoice${r.n === 1 ? "" : "s"} funded`] })} />
            </g>;
          })}
          <line x1={L} x2={W - R} y1={H - B} y2={H - B} className="ov-axis" />
        </svg>
        {tip && <div className="ov-tip" style={{ left: `${tip.x}%`, top: `${tip.y}%` }}><b>{tip.title}</b>{tip.lines.map((l) => <span key={l}>{l}</span>)}</div>}
      </div>
      <div className="sr-only"><table><caption>Sent to Keyway by {grain}</caption><tbody>{rows.map((r) => <tr key={r.start}><th>{r.label}</th><td>{money(r.sum)}</td><td>{r.n}</td></tr>)}</tbody></table></div>
    </figure>
  );
}

export function OutstandingChart({ items }: { items: Item[] }) {
  const [tip, setTip] = useState<Tip>(null);
  const box = useRef<HTMLDivElement>(null);
  const W = useWidth(box, 480);
  const rows = useMemo(() => {
    const m = new Map<string, { name: string; late: number; due: number; n: number }>();
    for (const i of items) {
      if (i.owed <= 0) continue;
      const name = tradeName(i.trade);
      const r = m.get(name) ?? { name, late: 0, due: 0, n: 0 };
      if (pastDue(i)) r.late += i.owed; else r.due += i.owed;
      r.n += 1; m.set(name, r);
    }
    return [...m.values()].sort((a, b) => b.late + b.due - (a.late + a.due));
  }, [items]);
  if (!rows.length) return <figure className="ov-chart"><figcaption><div><b>Outstanding by vendor type</b><span>Every vendor bill is paid.</span></div></figcaption></figure>;

  const rowH = 36, L = 100, R = 96, T = 6;
  const H = T + rows.length * rowH;
  const max = Math.max(...rows.map((r) => r.late + r.due));
  const x = (v: number) => ((W - L - R) * v) / max;
  const bh = 18;
  return (
    <figure className="ov-chart">
      <figcaption>
        <div><b>Outstanding by vendor type</b><span>Not yet paid to vendors, {money(rows.reduce((t, r) => t + r.late + r.due, 0))}</span></div>
        <div className="ov-legend"><span><i style={{ background: RED }} />Past due</span><span><i style={{ background: BLUE }} />Not past due</span></div>
      </figcaption>
      <div className="ov-plot" ref={box} onMouseLeave={() => setTip(null)}>
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Outstanding by vendor type: ${rows.map((r) => `${r.name} ${money(r.late + r.due)}, ${money(r.late)} past due`).join("; ")}`}>
          {rows.map((r, i) => {
            const y0 = T + i * rowH + (rowH - bh) / 2;
            const wl = x(r.late), wd = x(r.due), gap = wl > 0 && wd > 0 ? 2 : 0;
            const enter = (title: string, lines: string[]) => () => setTip({ x: ((L + wl + wd) / W) * 100, y: ((y0) / H) * 100, title, lines });
            return <g key={r.name}>
              <text x={L - 10} y={y0 + bh / 2 + 4} textAnchor="end" className="ov-cat">{r.name}</text>
              {wl > 0 && <path d={wd > 0 ? `M${L},${y0} h${wl} v${bh} h${-wl} Z` : bar(L, y0, wl, bh, 4, true)} fill={RED} />}
              {wd > 0 && <path d={bar(L + wl + gap, y0, Math.max(wd - gap, 1), bh, 4, true)} fill={BLUE} />}
              <text x={L + wl + wd + 8} y={y0 + bh / 2 + 4} className="ov-val">{money(r.late + r.due)}</text>
              <rect x={0} y={T + i * rowH} width={W} height={rowH} fill="transparent"
                onMouseEnter={enter(r.name, [`${money(r.late + r.due)} outstanding · ${r.n} bill${r.n === 1 ? "" : "s"}`, `Past due ${money(r.late)}`, `Not past due ${money(r.due)}`])} />
            </g>;
          })}
        </svg>
        {tip && <div className="ov-tip" style={{ left: `${Math.min(tip.x, 80)}%`, top: `${tip.y}%` }}><b>{tip.title}</b>{tip.lines.map((l) => <span key={l}>{l}</span>)}</div>}
      </div>
      <div className="sr-only"><table><caption>Outstanding by vendor type</caption><tbody>{rows.map((r) => <tr key={r.name}><th>{r.name}</th><td>{money(r.late)} past due</td><td>{money(r.due)} not past due</td></tr>)}</tbody></table></div>
    </figure>
  );
}
