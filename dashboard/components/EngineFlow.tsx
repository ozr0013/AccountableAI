"use client";

// The engine as one map, each box once, and the cards Live investigation plays: both built from
// one action's real run (dashboard/feed/session.py `_act` timeline: what the reader read, each
// routing step, each agent's decision with its reasoning, each policy-gate check). Nothing here is
// scripted: a box lights only if that part of the engine ran.

import type { ActionResult, Signal } from "@/lib/types";

type Ok = Extract<ActionResult, { ok: true }>;
type Decision = Extract<Signal, { type: "decision" }>;
type Gate = Extract<Signal, { type: "policy" }>;

export type BoxId = "inbox" | "clock" | "you" | "reader" | "log" | "router" | "agreement" | "chaser" | "anomaly"
  | "verifier" | "gate" | "owner" | "email" | "controller";
export type Stage = "source" | "owner" | "email" | "controller";
export type Row = { id: string; text: string; open: boolean; lines?: Decision["steps"] };
export type Card = {
  key: string; who: string; tone: "src" | "agent" | "check" | "gate" | "out";
  status: "done" | "held" | "blocked" | "allowed" | "raised" | "withdrawn" | "quiet";
  title: string; meta?: string; question?: string; lines?: Decision["steps"]; rows?: Row[];
  light: string[]; stage?: Stage; ms: number;
};

const W = 120, H = 40;
export const BOXES: { id: BoxId; x: number; y: number; title: string; tone: string }[] = [
  { id: "inbox", x: 20, y: 30, title: "Inbox", tone: "src" },
  { id: "clock", x: 20, y: 120, title: "Clock", tone: "src" },
  { id: "you", x: 20, y: 210, title: "Your fact", tone: "src" },
  { id: "reader", x: 170, y: 30, title: "Reader", tone: "agent" },
  { id: "log", x: 320, y: 120, title: "EVENT LOG", tone: "log" },
  { id: "router", x: 470, y: 120, title: "Router", tone: "check" },
  { id: "agreement", x: 620, y: 30, title: "Agreement", tone: "agent" },
  { id: "chaser", x: 620, y: 120, title: "Chaser", tone: "agent" },
  { id: "anomaly", x: 620, y: 210, title: "Anomaly", tone: "agent" },
  { id: "verifier", x: 770, y: 75, title: "Verifier", tone: "check" },
  { id: "gate", x: 770, y: 165, title: "Policy gate", tone: "check" },
  { id: "owner", x: 920, y: 30, title: "Owner alert", tone: "out" },
  { id: "email", x: 920, y: 120, title: "Keyway Management\nemail", tone: "out" },
  { id: "controller", x: 920, y: 210, title: "Controller queue", tone: "out" },
];
// Straight segments, right angles.
export const EDGES: Record<string, string> = {
  "inbox>reader": "M 140 50 H 170", "reader>log": "M 290 50 H 380 V 120",
  "clock>log": "M 140 140 H 320", "you>log": "M 140 230 H 380 V 160",
  "log>router": "M 440 140 H 470",
  "router>agreement": "M 590 140 H 605 V 50 H 620", "router>chaser": "M 590 140 H 620",
  "router>anomaly": "M 590 140 H 605 V 230 H 620",
  "agreement>verifier": "M 740 50 H 755 V 90 H 770", "chaser>verifier": "M 740 135 H 755 V 100 H 770",
  "anomaly>verifier": "M 740 225 H 762 V 108 H 770", "chaser>gate": "M 740 145 H 755 V 185 H 770",
  "verifier>owner": "M 890 95 H 905 V 50 H 920", "gate>email": "M 890 180 H 905 V 140 H 920",
  "gate>controller": "M 890 190 H 905 V 230 H 920",
};

export function EngineFlow({ lit, active, label }: { lit: Set<string>; active: Set<string>; label: string }) {
  const cls = (id: string) => (active.has(id) ? " active" : lit.has(id) ? " lit" : "");
  return (
    <svg className="ef-map" viewBox="0 0 1060 270" role="img" aria-label={label}>
      {Object.entries(EDGES).map(([k, d]) => <path key={k} d={d} className={`sm-edge${lit.has(k) ? " lit" : ""}`} />)}
      {Object.entries(EDGES).filter(([k]) => active.has(k)).map(([k, d]) => <path key={`c-${k}`} d={d} pathLength={100} className="sm-comet" />)}
      {BOXES.map((b) => (
        <g key={b.id} className={`sm-box t-${b.tone}${cls(b.id)}`} transform={`translate(${b.x} ${b.y})`}>
          <rect width={W} height={H} />
          {b.title.includes("\n")
            ? <text x={W / 2} y={H / 2 - 2} textAnchor="middle" className="sm-two">{b.title.split("\n").map((t, k) => <tspan key={k} x={W / 2} dy={k ? 13 : 0}>{t}</tspan>)}</text>
            : <text x={W / 2} y={H / 2 + 4.5} textAnchor="middle">{b.title}</text>}
        </g>
      ))}
    </svg>
  );
}

// -- a run, as cards ----------------------------------------------------------------------

const AGENT: Record<string, { who: string; box: BoxId }> = {
  chaser: { who: "Chaser agent", box: "chaser" },
  case: { who: "Case agent · funding hold", box: "chaser" },
  anomaly: { who: "Anomaly detector", box: "anomaly" },
  agreement: { who: "Agreement agent", box: "agreement" },
};
const result = (d: Decision) => d.steps.findLast((s) => s.kind === "result")?.text ?? "";
const reasons = (d: Decision) => d.steps.filter((s) => s.kind !== "result" && s.kind !== "thinking").slice(0, 4);
const clean = (t: string) => t.replace(/\s+/g, " ").trim();
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;

/** What the route did, named from the step that followed it. */
function routeName(next: string | undefined, nth: number): string | null {
  if (!next || next === "finish") return null;
  if (next === "read_reply") return "Keyway Management's reply → the chaser reads it";
  if (next === "detect_anomalies") return "A Keyway Management statement → the anomaly detector";
  if (next === "extract_terms") return "An agreement → the agreement agent";
  return nth === 0 ? "New evidence → recheck the invoices it touches" : "Daily scan → the chaser rechecks every late invoice";
}

export function plan(r: Ok): Card[] {
  const t = r.timeline ?? [];
  const changed = new Set([...r.diff.changed.map((c) => c.id), ...r.affected]);
  const cards: Card[] = [];
  let n = 0;
  const key = () => `c${n++}`;
  let lastAgentBox: BoxId = "chaser";

  // where it came in
  const reader = t.find((x): x is Extract<Signal, { type: "reader" }> => x.type === "reader");
  if (reader) {
    cards.push({ key: key(), who: "Reader", tone: "src", status: reader.flags.length ? "held" : "done",
      title: `Read ${reader.file} as ${reader.kind.map((k) => k.replace(/_/g, " ")).join(", ")}`,
      meta: reader.invoices.length ? `mentions ${reader.invoices.join(", ")}` : "no invoice number found",
      lines: reader.flags.map((f) => ({ kind: "guard", text: f, cites: [] })),
      light: ["inbox", "inbox>reader", "reader", "reader>log", "log"], stage: "source", ms: 1100 });
  } else {
    const src = r.kind === "clock" ? "clock" : "you";
    cards.push({ key: key(), who: r.kind === "clock" ? "Clock" : "Your fact", tone: "src", status: "done",
      title: clean(r.summary), light: [src, `${src}>log`, "log"], stage: "source", ms: 1000 });
  }

  let routes = 0;
  for (let i = 0; i < t.length; i++) {
    const x = t[i];
    if (x.type === "step" && x.step === "route") {
      const next = t.slice(i + 1).find((y): y is Extract<Signal, { type: "step" }> => y.type === "step")?.step;
      const firstAgent = t.slice(i + 1).find((y) => y.type === "decision") as Decision | undefined;
      const name = routeName(next ?? (firstAgent ? "assess" : undefined), routes++);
      if (name) cards.push({ key: key(), who: "Router", tone: "check", status: "done", title: name,
        meta: "picked from the event type alone, no model", light: ["log>router", "router"], ms: 700 });
      continue;
    }
    if (x.type === "decision") {
      if (x.agent === "workflow") continue;                         // the run's own summary
      if (x.agent === "trail" || x.agent === "cause") {
        // the chaser's own steps: what happened to each payment, and why it's late
        const rows: Row[] = [];
        let j = i;
        for (; j < t.length; j++) {
          const y = t[j];
          if (y.type === "agent_started") continue;
          if (y.type !== "decision" || (y.agent !== "trail" && y.agent !== "cause")) break;
          const id = y.invoice_id ?? "?";
          let row = rows.find((rw) => rw.id === id);
          if (!row) rows.push(row = { id, text: "", open: changed.has(id), lines: [] });
          const res = result(y).replace(/^Winner: /, "").replace(/^Cause: /, "cause: ");
          row.text = row.text ? `${row.text} · ${clean(res.split(". ")[0])}` : clean(res.split(". ")[0]);
          row.lines = [...(row.lines ?? []), ...reasons(y).slice(0, 3)];
        }
        i = j - 1;
        cards.push({ key: key(), who: "Chaser · payment trail + cause", tone: "agent", status: "done",
          title: rows.length === 1 ? `${rows[0].id}: ${rows[0].text}` : `Rechecked ${plural(rows.length, "invoice")} against the bank, the vendor and the agreement`,
          meta: "source-trust rules: bank > vendor > Keyway Management's word", rows: rows.length > 1 ? rows : undefined,
          lines: rows.length === 1 ? rows[0].lines : undefined,
          light: ["router>chaser", "chaser"], ms: rows.length > 1 ? 900 + 140 * Math.min(rows.length, 8) : 1300 });
        lastAgentBox = "chaser";
        continue;
      }
      if (x.agent === "verifier") {
        const group: Decision[] = [];
        let j = i;
        for (; j < t.length; j++) {
          const y = t[j];
          if (y.type === "agent_started") continue;
          if (y.type !== "decision" || y.agent !== "verifier") break;
          group.push(y);
        }
        i = j - 1;
        const blocked = group.filter((d) => String(d.answer.status) !== "approved").length;
        cards.push({ key: key(), who: "Verifier", tone: "check", status: blocked ? "blocked" : "done",
          title: group.length === 1 ? `Tried to prove it wrong: ${clean(result(group[0])).replace(/^Verdict: /, "")}` : `Tried to prove ${plural(group.length, "alert")} wrong before the owner sees them: ${group.length - blocked} held up${blocked ? `, ${blocked} blocked` : ""}`,
          question: group.length === 1 ? group[0].question : undefined, lines: group.length === 1 ? reasons(group[0]) : undefined,
          rows: group.length > 1 ? group.map((d) => ({ id: d.invoice_id ?? "pattern", text: clean(d.question.replace(/^Does the alert “|” survive scrutiny\?$/g, "")) + ` → ${clean(result(d)).replace(/^Verdict: /, "")}`, open: false })) : undefined,
          light: [`${lastAgentBox}>verifier`, "verifier"], ms: group.length > 1 ? 900 + 120 * Math.min(group.length, 6) : 1300 });
        continue;
      }
      const a = AGENT[x.agent] ?? { who: x.agent, box: "chaser" as BoxId };
      const res = clean(result(x)).replace(/^Next: (\w+) reminder \((tpl-[\w-]+)\)$/, "Drafted the next reminder: $1, from approved template $2");
      cards.push({ key: key(), who: a.who, tone: "agent",
        status: /holding position|no vendor or bank proof/i.test(res) ? "held" : "done",
        title: res || clean(x.question), question: x.question, lines: reasons(x),
        meta: x.invoice_id ?? undefined, light: [`router>${a.box}`, a.box], ms: 1500 });
      lastAgentBox = a.box;
      continue;
    }
    if (x.type === "policy") {
      const group: Gate[] = [];
      let j = i;
      for (; j < t.length && t[j].type === "policy"; j++) group.push(t[j] as Gate);
      i = j - 1;
      const said = (g: Gate) => ({ send_chase: "Send Keyway Management a reminder", propose_hold: "Propose a funding hold" }[g.action] ?? g.action.replace(/_/g, " "));
      const refused = group.filter((g) => !g.ok);
      cards.push({ key: key(), who: "Policy gate", tone: "gate", status: refused.length === group.length ? "blocked" : "allowed",
        title: group.length === 1 ? `${said(group[0])}: ${group[0].ok ? "allowed" : `refused, ${group[0].why}`}` : `${plural(group.length, "action")} checked: ${group.length - refused.length} allowed, ${refused.length} refused`,
        rows: group.length > 1 ? group.map((g, k) => ({ id: `${k + 1}`, text: `${said(g)}: ${g.ok ? "allowed" : `refused, ${g.why}`}`, open: false })) : undefined,
        meta: "no agent can move money; reminders only from approved templates, on schedule",
        light: ["chaser>gate", "gate"], ms: 1100 });
    }
  }

  // what came out
  const notes = r.writes?.notes ?? [];
  if (r.diff.raised.length || r.diff.withdrawn.length) {
    cards.push({ key: key(), who: "Owner alert", tone: "out", status: r.diff.raised.length ? "raised" : "withdrawn",
      title: [r.diff.raised.length && `${plural(r.diff.raised.length, "alert")} raised`, r.diff.withdrawn.length && `${plural(r.diff.withdrawn.length, "alert")} withdrawn`].filter(Boolean).join(", "),
      rows: [...r.diff.raised.map((a) => ({ id: "raised", text: clean(a.headline), open: false })), ...r.diff.withdrawn.map((a) => ({ id: "withdrawn", text: clean(a.headline), open: false }))],
      light: ["verifier>owner", "owner"], stage: "owner", ms: 1100 });
  }
  const sent = notes.filter((w) => w.type === "chase_sent");
  if (sent.length) cards.push({ key: key(), who: "Keyway Management email", tone: "out", status: "done",
    title: `${plural(sent.length, "reminder")} to Keyway Property Management · recorded, not emailed`,
    rows: sent.map((w, k) => ({ id: `${k + 1}`, text: clean(w.text.replace(/ · recorded, not emailed$/, "")), open: false })),
    light: ["gate>email", "email"], stage: "email", ms: 1100 });
  const hold = notes.find((w) => w.type === "case_proposed");
  if (hold) cards.push({ key: key(), who: "Controller queue", tone: "out", status: "done",
    title: `${clean(hold.text)}, waiting for the controller. Agents only recommend.`,
    light: ["gate>controller", "controller"], stage: "controller", ms: 1000 });
  if (!r.diff.raised.length && !r.diff.withdrawn.length && !sent.length && !hold)
    cards.push({ key: key(), who: "Outcome", tone: "out", status: "quiet",
      title: "No alert, no reminder, no new hold: nothing here needed anyone.", light: [], ms: 800 });
  return merge(cards);
}

/** The scan repeats work (a second sweep, the same reminder refused twice): say it once, ×n. */
function merge(cards: Card[]): Card[] {
  const out: Card[] = [];
  const times = new Map<Card, number>();
  for (const c of cards) {
    const twin = out.slice(-3).find((o) => o.who === c.who && o.title === c.title && o.who !== "Router");
    if (twin) { times.set(twin, (times.get(twin) ?? 1) + 1); continue; }
    out.push(c);
  }
  return out.map((c) => (times.has(c) ? { ...c, meta: [c.meta, `×${times.get(c)} in this run`].filter(Boolean).join(" · ") } : c));
}

/** Which writes to the event log each output stands for (the rest show at the start or the end). */
export function stageOf(type: string): Stage | "end" {
  if (["document_ingested", "chase_reply", "fact_corrected", "world_meta", "reply_read"].includes(type)) return "source";
  if (type.startsWith("alert_")) return "owner";
  if (type === "chase_sent") return "email";
  if (type === "case_proposed" || type === "approval_requested") return "controller";
  return "end";
}
