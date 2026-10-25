"use client";

// Overview's two charts, from the same invoices every tab reads:
//  - what Oakline paid Keyway over 1W, 1M, 6M, 1Y, 2Y or a custom range (one series: a line, no legend);
//  - what Keyway is late paying, by vendor type (status red; the same rules as the boxes: lib/balances.ts).

import { type RefObject, useEffect, useMemo, useRef, useState } from "react";
import { lateToPay, leftToPay } from "@/lib/balances";
import { money } from "@/lib/format";
import type { Item } from "@/lib/types";

type Grain = "day" | "week" | "month";
type Range = "1W" | "1M" | "6M" | "1Y" | "2Y" | "custom";
const RANGES: Range[] = ["1W", "1M", "6M", "1Y", "2Y", "custom"];
const DAY = 864e5;
const iso = (t: number) => new Date(t).toISOString().slice(0, 10);
const ut = (d: string) => Date.parse(d + "T00:00:00Z");
const fmtDay = (t: number) => new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });

/** The first day a preset covers, counting back from the books' date. */
function rangeStart(r: Exclude<Range, "custom">, end: number): number {
  const d = new Date(end);
  if (r === "1W") return end - 6 * DAY;
  const months = { "1M": 1, "6M": 6, "1Y": 12, "2Y": 24 }[r];
  d.setUTCMonth(d.getUTCMonth() - months);
  return d.getTime() + DAY;
}
/** Daily points for a month or less, weekly up to about six months, monthly beyond. */
const grainFor = (days: number): Grain => (days <= 31 ? "day" : days <= 190 ? "week" : "month");
const BLUE = "#2d5bff";
const RED = "#d03b3b";                   // status: critical. Always next to the words "past due".
const TRADE: Record<string, string> = {
  hvac: "HVAC", cleaner: "Cleaning", plumber: "Plumbing", roofer: "Roofing", landscaper: "Landscaping",
  electrician: "Electrical", handyman: "Handyman", painter: "Painting",
};
const tradeName = (t: string | null | undefined) => (t ? TRADE[t] ?? t[0].toUpperCase() + t.slice(1) : "Other");
const short = (n: number) => (n >= 1000 ? `$${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : `$${Math.round(n)}`);

/** A horizontal bar with its data end rounded and its base square on the axis. */
function bar(x: number, y: number, w: number, h: number, r = 4): string {
  if (w <= 0 || h <= 0) return "";
  const k = Math.min(r, w, h / 2);
  return `M${x},${y} H${x + w - k} Q${x + w},${y} ${x + w},${y + k} V${y + h - k} Q${x + w},${y + h} ${x + w - k},${y + h} H${x} Z`;
}

function periodStart(t: number, g: Grain): number {
  const d = new Date(t);
  if (g === "month") return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1);
  if (g === "week") return t - ((d.getUTCDay() + 6) % 7) * DAY;              // weeks start Monday
  return t;
}
function nextPeriod(t: number, g: Grain): number {
  if (g === "month") { const d = new Date(t); return Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1); }
  return t + (g === "week" ? 7 : 1) * DAY;
}
function periodLabel(t: number, g: Grain, years: boolean): string {
  const o: Intl.DateTimeFormatOptions = g === "month" ? { month: "short", timeZone: "UTC" } : { month: "short", day: "numeric", timeZone: "UTC" };
  if (years && g === "month") o.year = "2-digit";
  return new Date(t).toLocaleDateString("en-US", o);
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

export function FundingChart({ items, asOf }: { items: Item[]; asOf: string }) {
  const [range, setRange] = useState<Range>("6M");
  const end = ut(asOf);
  const first = useMemo(() => Math.min(...items.filter((i) => i.funded_on).map((i) => ut(i.funded_on!)), end), [items, end]);
  const [custom, setCustom] = useState<{ from: string; to: string }>({ from: iso(first), to: asOf });
  const [tip, setTip] = useState<Tip>(null);
  const box = useRef<HTMLDivElement>(null);
  const W = useWidth(box, 560);
  const [hover, setHover] = useState<number | null>(null);
  const [from, to] = range === "custom"
    ? [Math.min(ut(custom.from), ut(custom.to)), Math.max(ut(custom.from), ut(custom.to))]
    : [rangeStart(range, end), end];
  const grain = grainFor((to - from) / DAY + 1);
  const rows = useMemo(() => {
    // every period in the range, empty ones as zero, so the line shows quiet weeks too
    const out: { label: string; start: number; sum: number; n: number }[] = [];
    const years = new Date(from).getUTCFullYear() !== new Date(to).getUTCFullYear();
    for (let t = periodStart(from, grain); t <= to; t = nextPeriod(t, grain)) out.push({ label: periodLabel(t, grain, years), start: t, sum: 0, n: 0 });
    for (const i of items) {
      if (!i.funded_on || !i.funded) continue;
      const t = ut(i.funded_on);
      if (t < from || t > to) continue;
      const k = periodStart(t, grain);
      const r = out.find((o) => o.start === k);
      if (r) { r.sum += i.funded; r.n += 1; }
    }
    return out;
  }, [items, from, to, grain]);

  const H = 240, L = 52, R = 16, T = 14, B = 28;
  const max = niceMax(Math.max(...rows.map((r) => r.sum), 0));
  const px = (i: number) => (rows.length <= 1 ? (L + W - R) / 2 : L + ((W - L - R) * i) / (rows.length - 1));
  const y = (v: number) => T + (H - T - B) * (1 - v / max);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max);
  const total = rows.reduce((t, r) => t + r.sum, 0);
  const line = rows.map((r, i) => `${i ? "L" : "M"}${px(i)},${y(r.sum)}`).join(" ");
  const area = rows.length ? `${line} L${px(rows.length - 1)},${H - B} L${px(0)},${H - B} Z` : "";
  const every = Math.max(1, Math.ceil((rows.length * 64) / (W - L - R)));
  // the crosshair snaps to the nearest period; the reader aims at a date, not at a 2px line
  const near = (e: React.PointerEvent<SVGRectElement>) => {
    const b = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - b.left) / b.width) * (W - L - R) + L;
    let k = 0;
    rows.forEach((_, i) => { if (Math.abs(px(i) - x) < Math.abs(px(k) - x)) k = i; });
    setHover(k);
    setTip({ x: (px(k) / W) * 100, y: (y(rows[k].sum) / H) * 100, title: rows[k].label, lines: [money(rows[k].sum), `${rows[k].n} invoice${rows[k].n === 1 ? "" : "s"} funded`] });
  };

  return (
    <figure className="ov-chart">
      <figcaption>
        <div><b>Total invoiced</b><span>{money(total)} · {fmtDay(from)} to {fmtDay(to)} · by {grain}</span></div>
        <div className="ov-range">
          <div className="ov-seg" role="tablist" aria-label="Time range">
            {RANGES.map((r) => <button key={r} role="tab" aria-selected={range === r} className={range === r ? "on" : ""} onClick={() => { setRange(r); setHover(null); setTip(null); }}>{r === "custom" ? "Custom" : r}</button>)}
          </div>
          {range === "custom" && <div className="ov-dates">
            <input type="date" aria-label="From" value={custom.from} max={custom.to} onChange={(e) => e.target.value && setCustom({ ...custom, from: e.target.value })} />
            <span>to</span>
            <input type="date" aria-label="To" value={custom.to} min={custom.from} max={asOf} onChange={(e) => e.target.value && setCustom({ ...custom, to: e.target.value })} />
          </div>}
        </div>
      </figcaption>
      <div className="ov-plot" ref={box} onMouseLeave={() => { setTip(null); setHover(null); }}>
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Total invoiced, ${fmtDay(from)} to ${fmtDay(to)}, by ${grain}: ${rows.map((r) => `${r.label} ${money(r.sum)}`).join(", ")}`}>
          {ticks.map((t) => <g key={t}><line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className="ov-grid" /><text x={L - 8} y={y(t) + 4} textAnchor="end" className="ov-tick">{short(t)}</text></g>)}
          <line x1={L} x2={W - R} y1={H - B} y2={H - B} className="ov-axis" />
          <path d={area} fill={BLUE} opacity={0.1} />
          <path d={line} fill="none" stroke={BLUE} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          {hover != null && <line x1={px(hover)} x2={px(hover)} y1={T} y2={H - B} className="ov-cross" />}
          {rows.map((r, i) => <g key={r.start}>
            {(rows.length <= 16 || i === hover) && <circle cx={px(i)} cy={y(r.sum)} r={i === hover ? 5 : 4} fill={BLUE} stroke="#fff" strokeWidth={2} />}
            {i % every === 0 && <text x={px(i)} y={H - 8} textAnchor={i === 0 && rows.length > 1 ? "start" : "middle"} className="ov-tick">{r.label}</text>}
          </g>)}
          {rows.length > 0 && rows[rows.length - 1].sum > 0 && hover == null && (() => {
            // the end value sits on the side away from the line coming into it
            const k = rows.length - 1, falling = k > 0 && rows[k - 1].sum > rows[k].sum;
            return <text x={px(k) - 8} y={y(rows[k].sum) + (falling ? 18 : -10)} textAnchor="end" className="ov-val">{short(rows[k].sum)}</text>;
          })()}
          <rect x={L} y={T} width={Math.max(W - L - R, 1)} height={H - T - B} fill="transparent" onPointerMove={near} onPointerDown={near} />
        </svg>
        {tip && <div className="ov-tip" style={{ left: `${tip.x}%`, top: `${tip.y}%` }}><b>{tip.title}</b>{tip.lines.map((l) => <span key={l}>{l}</span>)}</div>}
      </div>
      <div className="sr-only"><table><caption>Total invoiced, {fmtDay(from)} to {fmtDay(to)}, by {grain}</caption><tbody>{rows.map((r) => <tr key={r.start}><th>{r.label}</th><td>{money(r.sum)}</td><td>{r.n}</td></tr>)}</tbody></table></div>
    </figure>
  );
}

export function OutstandingChart({ items }: { items: Item[] }) {
  const [tip, setTip] = useState<Tip>(null);
  const box = useRef<HTMLDivElement>(null);
  const W = useWidth(box, 480);
  // every vendor type with money left to pay; the bar is only the part that's past due
  const rows = useMemo(() => {
    const m = new Map<string, { name: string; late: number; n: number }>();
    for (const i of items) {
      if (leftToPay(i) <= 0) continue;
      const name = tradeName(i.trade);
      const r = m.get(name) ?? { name, late: 0, n: 0 };
      if (lateToPay(i)) { r.late += leftToPay(i); r.n += 1; }
      m.set(name, r);
    }
    return [...m.values()].sort((a, b) => b.late - a.late || a.name.localeCompare(b.name));
  }, [items]);
  const total = rows.reduce((t, r) => t + r.late, 0);

  const rowH = 36, L = 100, R = 96, T = 6, bh = 18;
  const H = T + Math.max(rows.length, 1) * rowH;
  const max = Math.max(...rows.map((r) => r.late), 1);
  const x = (v: number) => ((W - L - R) * v) / max;
  return (
    <figure className="ov-chart">
      <figcaption>
        <div><b>Past due, by vendor type</b><span>{total ? `${money(total)} Keyway is late paying` : "Nothing past due"}</span></div>
      </figcaption>
      <div className="ov-plot" ref={box} onMouseLeave={() => setTip(null)}>
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Past due by vendor type: ${rows.map((r) => `${r.name} ${money(r.late)}`).join("; ")}`}>
          <line x1={L} x2={L} y1={T} y2={H} className="ov-axis" />
          {rows.map((r, i) => {
            const y0 = T + i * rowH + (rowH - bh) / 2;
            const w = x(r.late);
            return <g key={r.name}>
              <text x={L - 10} y={y0 + bh / 2 + 4} textAnchor="end" className="ov-cat">{r.name}</text>
              {w > 0 && <path d={bar(L, y0, w, bh)} fill={RED} opacity={tip && tip.title !== r.name ? 0.55 : 1} />}
              <text x={L + w + 8} y={y0 + bh / 2 + 4} className={`ov-val${r.late ? "" : " zero"}`}>{money(r.late)}</text>
              <rect x={0} y={T + i * rowH} width={W} height={rowH} fill="transparent"
                onMouseEnter={() => setTip({ x: Math.min(((L + w) / W) * 100, 80), y: (y0 / H) * 100, title: r.name,
                  lines: [`${money(r.late)} past due`, r.n ? `${r.n} bill${r.n === 1 ? "" : "s"} late` : "nothing late"] })} />
            </g>;
          })}
        </svg>
        {tip && <div className="ov-tip" style={{ left: `${tip.x}%`, top: `${tip.y}%` }}><b>{tip.title}</b>{tip.lines.map((l) => <span key={l}>{l}</span>)}</div>}
      </div>
      <div className="sr-only"><table><caption>Past due, by vendor type</caption><tbody>{rows.map((r) => <tr key={r.name}><th>{r.name}</th><td>{money(r.late)}</td></tr>)}</tbody></table></div>
    </figure>
  );
}
