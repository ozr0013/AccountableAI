"use client";

// One source of truth for every screen. NEXT_PUBLIC_FEED=live talks to dashboard/feed/serve.py
// through the /api rewrite; replay (or a live feed that isn't running) reads the baked JSON in
// public/feed, where the Judge Console can play the moment and nothing else.

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import type { Briefing, ConsoleResult, Preset, State } from "@/lib/types";

type Mode = "live" | "replay";
type Ctx = {
  state: State | null;
  mode: Mode;
  error: string | null;
  presets: Preset[];
  last: ConsoleResult | null;
  runConsole: (q: { preset?: string; params?: Record<string, string>; text?: string }) => Promise<ConsoleResult>;
  decide: (approvalId: string, approve: boolean) => Promise<{ ok: boolean; error?: string }>;
  brief: (question: string) => Promise<Briefing>;
  reset: () => Promise<void>;
};

const FeedCtx = createContext<Ctx | null>(null);
const WANT: Mode = process.env.NEXT_PUBLIC_FEED === "replay" ? "replay" : "live";
const PLAYED = "accountableai.replay.moment";
const MOMENT_TEXT = /(electric|sparkline|0402).*(15|fifteen)|(15|fifteen).*(electric|sparkline|0402)/i;

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, { cache: "no-store", ...init, headers: { "content-type": "application/json", ...init?.headers } });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

export function FeedProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State | null>(null);
  const [mode, setMode] = useState<Mode>(WANT);
  const [error, setError] = useState<string | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [last, setLast] = useState<ConsoleResult | null>(null);
  const [briefs, setBriefs] = useState<{ before: Record<string, Briefing>; after: Record<string, Briefing> } | null>(null);

  const loadReplay = useCallback(async () => {
    setMode("replay");
    const [s, p] = await Promise.all([json<State>("/feed/state.json"), json<Preset[]>("/feed/presets.json")]);
    setPresets(p);
    setLast(null);
    if (sessionStorage.getItem(PLAYED) === "1") {
      const m = await json<ConsoleResult>("/feed/moment.json");
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

  const runConsole: Ctx["runConsole"] = async (q) => {
    let res: ConsoleResult;
    if (mode === "live") {
      res = await json<ConsoleResult>("/api/console", { method: "POST", body: JSON.stringify(q) });
    } else if (q.preset === "invoice_received_correction" || (q.text && MOMENT_TEXT.test(q.text))) {
      if (state?.history.length) {
        res = { ok: false, error: "The replay feed has already played the moment. Reset to play it again." };
      } else {
        res = await json<ConsoleResult>("/feed/moment.json");
        sessionStorage.setItem(PLAYED, "1");
      }
    } else {
      res = { ok: false, error: "The replay feed plays the moment only. Start the live feed (python dashboard/feed/serve.py) for every preset and free text." };
    }
    if (res.ok) setState(res.state);
    setLast(res);
    return res;
  };

  const decide: Ctx["decide"] = async (approvalId, approve) => {
    if (mode !== "live") return { ok: false, error: "Decisions need the live feed: they go through the rails policy gate." };
    const r = await json<{ ok: boolean; error?: string; state: State }>(`/api/approvals/${approvalId}`, {
      method: "POST", body: JSON.stringify({ approve, by: state?.meta.controller ?? "controller.dana" }),
    });
    setState(r.state);
    return r;
  };

  const brief: Ctx["brief"] = async (question) => {
    if (mode === "live") return json<Briefing>("/api/brief", { method: "POST", body: JSON.stringify({ question }) });
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
  };

  return (
    <FeedCtx.Provider value={{ state, mode, error, presets, last, runConsole, decide, brief, reset }}>
      {children}
    </FeedCtx.Provider>
  );
}

export function useFeed(): Ctx {
  const c = useContext(FeedCtx);
  if (!c) throw new Error("useFeed outside FeedProvider");
  return c;
}
