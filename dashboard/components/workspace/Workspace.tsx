"use client";

// Workspace state shared by every UI revamp 2 page: date range, invoice filters, search, chart
// period, ledger page, the selected property, review notes, profile and preferences, plus the
// one dialog and one toast on screen. Review notes, profile and preferences stay in this browser.

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useFeed } from "@/components/Feed";
import {
  DEFAULT_PREFS, DEFAULT_PROFILE, dateBounds, invoicesOf, needsReview, propertiesOf, readLocal, writeLocal,
  type Period, type Prefs, type Profile, type Reviews, type WsInvoice,
} from "@/lib/workspace";

export type Filters = { filterProperty: string; vendor: string; status: string; query: string };
const NO_FILTERS: Filters = { filterProperty: "All Properties", vendor: "All Vendors", status: "All Statuses", query: "" };

type Ws = {
  ready: boolean; invoices: WsInvoice[]; properties: string[]; needs: WsInvoice[]; asOf: string;
  start: string; end: string; bounds: { start: string; end: string }; setDates: (s: string, e: string) => void;
  filters: Filters; setFilter: <K extends keyof Filters>(k: K, v: Filters[K]) => void; clearFilters: () => void;
  period: Period; setPeriod: (p: Period) => void;
  page: number; setPage: (n: number) => void;
  property: string; setProperty: (p: string) => void;
  reviews: Reviews; addNote: (id: string, note: string) => void; markReviewed: (id: string, note?: string) => void;
  profile: Profile; prefs: Prefs; saveProfile: (p: Profile, prefs: Prefs) => void;
  go: (path: string, f?: Partial<Filters>) => void;
  dialog: ReactNode | null; openDialog: (d: ReactNode) => void; closeDialog: () => void;
  toast: (t: string) => void; toastText: string | null;
};
const Ctx = createContext<Ws | null>(null);
export const useWs = () => { const c = useContext(Ctx); if (!c) throw new Error("useWs outside WorkspaceProvider"); return c; };

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const { state } = useFeed();
  const router = useRouter();
  const path = usePathname();
  const invoices = useMemo(() => (state ? invoicesOf(state) : []), [state]);
  const properties = useMemo(() => propertiesOf(invoices), [invoices]);
  const bounds = useMemo(() => dateBounds(invoices), [invoices]);
  const [range, setRange] = useState<{ start: string; end: string } | null>(null);
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [period, setPeriod] = useState<Period>("Monthly");
  const [page, setPage] = useState(1);
  const [property, setPropertyState] = useState<string>("");
  const [reviews, setReviews] = useState<Reviews>({});
  const [profile, setProfile] = useState<Profile>(DEFAULT_PROFILE);
  const [prefs, setPrefs] = useState<Prefs>(DEFAULT_PREFS);
  const [dialog, setDialog] = useState<ReactNode | null>(null);
  const [toastText, setToastText] = useState<string | null>(null);

  useEffect(() => {        // browser-only state, read once after the first render
    setReviews(readLocal("accountable-reviews-v1", {}));
    setProfile(readLocal("accountable-profile", DEFAULT_PROFILE));
    setPrefs(readLocal("accountable-prefs", DEFAULT_PREFS));
  }, []);
  useEffect(() => { document.body.classList.toggle("ws-compact", prefs.compact); }, [prefs.compact]);
  const needs = useMemo(() => needsReview(invoices, reviews), [invoices, reviews]);
  useEffect(() => {        // default property: the first one with something to review
    if (!property && properties.length) setPropertyState(needs[0]?.property ?? properties[0]);
  }, [properties, needs, property]);

  const start = range?.start ?? bounds.start, end = range?.end ?? bounds.end;
  const toast = useCallback((t: string) => {
    setToastText(t);
    window.clearTimeout((window as unknown as { _wsToast?: number })._wsToast);
    (window as unknown as { _wsToast?: number })._wsToast = window.setTimeout(() => setToastText(null), 3500);
  }, []);
  const saveReviews = (r: Reviews) => { setReviews(r); writeLocal("accountable-reviews-v1", r); };

  const ws: Ws = {
    ready: !!state, invoices, properties, needs, asOf: state?.meta.as_of ?? bounds.end,
    start, end, bounds, setDates: (s, e) => { setRange({ start: s, end: e }); setPage(1); },
    filters, setFilter: (k, v) => { setFilters(f => ({ ...f, [k]: v })); setPage(1); },
    clearFilters: () => { setFilters(NO_FILTERS); setRange(null); setPage(1); },
    period, setPeriod, page, setPage,
    property, setProperty: (p) => { setPropertyState(p); if (!path.startsWith("/properties")) router.push("/properties"); },
    reviews,
    addNote: (id, note) => saveReviews({ ...reviews, [id]: { notes: [...(reviews[id]?.notes ?? []), note], reviewed: reviews[id]?.reviewed ?? false } }),
    markReviewed: (id, note) => saveReviews({ ...reviews, [id]: { notes: [...(reviews[id]?.notes ?? []), ...(note ? [note] : [])], reviewed: true } }),
    profile, prefs,
    saveProfile: (p, pr) => { setProfile(p); setPrefs(pr); writeLocal("accountable-profile", p); writeLocal("accountable-prefs", pr); },
    go: (to, f) => { setFilters({ ...NO_FILTERS, ...f }); setPage(1); setDialog(null); router.push(to); window.scrollTo(0, 0); },
    dialog, openDialog: setDialog, closeDialog: () => setDialog(null),
    toast, toastText,
  };
  return <Ctx.Provider value={ws}>{children}</Ctx.Provider>;
}

/** Rows within the date range and the invoice filters (the Invoices page and its export). */
export function filtered(ws: Ws): WsInvoice[] {
  const { filters: f, start, end, reviews } = ws;
  const q = f.query.trim().toLowerCase();
  return ws.invoices.filter(i => i.due >= start && i.due <= end
    && (f.filterProperty === "All Properties" || i.property === f.filterProperty)
    && (f.vendor === "All Vendors" || i.vendor === f.vendor)
    && (f.status === "All Statuses" || (f.status === "Needs Review" ? !!i.issue && !reviews[i.id]?.reviewed : i.status === f.status))
    && (!q || [i.id, i.cid, i.vendor, i.property, i.description].some(v => v.toLowerCase().includes(q))));
}

/** Rows within the date range, and the property when on the Properties page (the dashboards). */
export function scoped(ws: Ws, onProperty: boolean): WsInvoice[] {
  return ws.invoices.filter(i => i.due >= ws.start && i.due <= ws.end && (!onProperty || i.property === ws.property));
}
