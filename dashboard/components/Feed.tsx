"use client";

// One source of truth for every screen. NEXT_PUBLIC_FEED=live talks to dashboard/feed/serve.py
// through the /api rewrite; replay (or a live feed that isn't running) reads the baked JSON in
// public/feed, where the Judge Console can play the moment and nothing else.
//
// Every action (judge fact, Keyway reply, controller decision, clock) runs the real workflow on
// the server and comes back with what each agent did; the responses panel shows it.

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import type { ActionResult, Briefing, Preset, State } from "@/lib/types";

type Mode = "live" | "replay";
type Ctx = {
  state: State | null;
  mode: Mode;
  error: string | null;
  presets: Preset[];
  last: ActionResult | null;
  busy: boolean;
  panel: boolean;
  setPanel: (open: boolean) => void;
  runConsole: (q: { preset?: string; params?: Record<string, string>; text?: string }) => Promise<ActionResult>;
  reply: (invoiceId: string, text: string, proof: "none" | "bank" | "vendor") => Promise<ActionResult>;
  decide: (approvalId: string, approve: boolean, identityVerified?: boolean) => Promise<ActionResult>;
  advance: (days: number) => Promise<ActionResult>;
  addFile: (name: string) => Promise<ActionResult>;
  setLive: (on: boolean) => Promise<{ ok: boolean; error?: string }>;
  brief: (question: string) => Promise<Briefing>;
  reset: () => Promise<void>;
};

const FeedCtx = createContext<Ctx | null>(null);
const WANT: Mode = process.env.NEXT_PUBLIC_FEED === "replay" ? "replay" : "live";
const PLAYED = "accountableai.replay.moment";
const MOMENT_TEXT = /(electric|sparkline|0402).*(15|fifteen)|(15|fifteen).*(electric|sparkline|0402)/i;
const NEEDS_LIVE = "This needs the live feed (python dashboard/feed/serve.py): it runs the real workflow and agents.";

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, { cache: "no-store", ...init, headers: { "content-type": "application/json", ...init?.headers } });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}
const post = <T,>(url: string, body: unknown) => json<T>(url, { method: "POST", body: JSON.stringify(body) });

export function FeedProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State | null>(null);
  const [mode, setMode] = useState<Mode>(WANT);
  const [error, setError] = useState<string | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [last, setLast] = useState<ActionResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [panel, setPanel] = useState(false);
  const [briefs, setBriefs] = useState<{ before: Record<string, Briefing>; after: Record<string, Briefing> } | null>(null);

  const loadReplay = useCallback(async () => {
    setMode("replay");
    const [s, p] = await Promise.all([json<State>("/feed/state.json"), json<Preset[]>("/feed/presets.json")]);
    setPresets(p);
    setLast(null);
    if (sessionStorage.getItem(PLAYED) === "1") {
      const m = await json<ActionResult>("/feed/moment.json");
      if (m.ok) { setState(m.state); setLast(m); return; }
    }
    setState(s);
  }, []);

  useEffect(() => {
    (async () => {
      try {
        if (WANT === "live") {
          try {
            const [s, p] = await Promise.all([json<State>("/api/state"), json<Preset[]>("/api/presets")]);
            setState(s);
            setPresets(p);
            return;
          } catch {
            /* live feed not running: fall back to the replay snapshot */
          }
        }
        await loadReplay();
      } catch (e) {
        setError(String(e));
      }
    })();
  }, [loadReplay]);

  /** Run one action: update state, keep the result, open the responses panel. */
  const act = async (run: () => Promise<ActionResult>): Promise<ActionResult> => {
    setBusy(true);
    let res: ActionResult;
    try {
      res = await run();
    } catch (e) {
      res = { ok: false, error: String(e) };
    }
    if (res.state) setState(res.state);
    setLast(res);
    setPanel(true);
    setBusy(false);
    return res;
  };
  const replayOnly = async (): Promise<ActionResult> => ({ ok: false, error: NEEDS_LIVE });

  const runConsole: Ctx["runConsole"] = (q) => act(async () => {
    if (mode === "live") return post<ActionResult>("/api/console", q);
    if (q.preset === "invoice_received_correction" || (q.text && MOMENT_TEXT.test(q.text))) {
      if (state?.history.length) return { ok: false, error: "The replay feed has already played the moment. Reset to play it again." };
      sessionStorage.setItem(PLAYED, "1");
      return json<ActionResult>("/feed/moment.json");
    }
    return { ok: false, error: "The replay feed plays the moment only. " + NEEDS_LIVE };
  });

  const reply: Ctx["reply"] = (invoiceId, text, proof) =>
    act(() => (mode === "live" ? post<ActionResult>("/api/reply", { invoice_id: invoiceId, text, proof }) : replayOnly()));

  const decide: Ctx["decide"] = (approvalId, approve, identityVerified = false) =>
    act(() => (mode === "live"
      ? post<ActionResult>(`/api/approvals/${approvalId}`, { approve, by: state?.meta.controller ?? "controller.dana", identity_verified: identityVerified })
      : replayOnly()));

  const advance: Ctx["advance"] = (days) =>
    act(() => (mode === "live" ? post<ActionResult>("/api/clock", { days }) : replayOnly()));

  const addFile: Ctx["addFile"] = (name) =>
    act(() => (mode === "live" ? post<ActionResult>(`/api/samples/${encodeURIComponent(name)}/add`, {}) : replayOnly()));

  const setLive: Ctx["setLive"] = async (on) => {
    if (mode !== "live") return { ok: false, error: NEEDS_LIVE };
    const r = await post<{ ok: boolean; error?: string; state: State }>("/api/live", { on });
    if (r.state) setState(r.state);
    return r;
  };

  const brief: Ctx["brief"] = async (question) => {
    if (mode === "live") return post<Briefing>("/api/brief", { question });
    const b = briefs ?? {
      before: await json<Record<string, Briefing>>("/feed/briefing.json"),
      after: await json<Record<string, Briefing>>("/feed/briefing_after.json"),
    };
    if (!briefs) setBriefs(b);
    const set = state?.history.length ? b.after : b.before;
    const hit = set[question] ?? set[Object.keys(set)[0]];
    return { ...hit, question };
  };

  const reset: Ctx["reset"] = async () => {
    if (mode === "live") setState(await json<State>("/api/reset", { method: "POST" }));
    else { sessionStorage.removeItem(PLAYED); await loadReplay(); }
    setLast(null);
    setPanel(false);
  };

  return (
    <FeedCtx.Provider value={{ state, mode, error, presets, last, busy, panel, setPanel, runConsole, reply, decide, advance, addFile, setLive, brief, reset }}>
      {children}
    </FeedCtx.Provider>
  );
}

export function useFeed(): Ctx {
  const c = useContext(FeedCtx);
  if (!c) throw new Error("useFeed outside FeedProvider");
  return c;
}
