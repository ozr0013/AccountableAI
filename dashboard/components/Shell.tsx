"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { useFeed } from "./Feed";
import { Wordmark } from "./Wordmark";
import { Building, Gavel, Home, Inbox, ListIcon, Mark, Spark } from "./Icons";
import { ResponsesPanel } from "./Responses";
import { DocProvider } from "./ui";

export function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const { state } = useFeed();
  const [collapsed, setCollapsed] = useState(false);
  const [account, setAccount] = useState<{ signed_in: boolean; name?: string; given_name?: string; picture?: string } | null>(null);
  useEffect(() => {
    try { setCollapsed(localStorage.getItem("accountableai:sidebar-collapsed") === "true"); } catch { /* Storage may be unavailable. */ }
  }, []);
  const toggleSidebar = () => {
    const next = !collapsed;
    setCollapsed(next);
    try { localStorage.setItem("accountableai:sidebar-collapsed", String(next)); } catch { /* The toggle still works without storage. */ }
  };
  useEffect(() => {
    let current = true;
    fetch(`${process.env.NEXT_PUBLIC_AGREEMENT_URL || "http://127.0.0.1:8771"}/api/status`, { cache: "no-store" })
      .then((response) => response.ok ? response.json() : null)
      .then((profile) => { if (current && profile?.signed_in) setAccount(profile); })
      .catch(() => undefined);
    return () => { current = false; };
  }, []);
  const open = state?.items.filter((i) => i.attention).length ?? 0;
  const controllerName = account?.name || account?.given_name;
  const controllerInitial = controllerName?.trim().charAt(0).toUpperCase() || "G";
  const nav = [
    { href: "/overview", label: "Overview", icon: Home },
    { href: "/investigation", label: "Live investigation", icon: Spark },
    { href: "/console", label: "Judge Console", icon: Gavel },
    // the number is what still needs someone; it goes when everything is cleared
    { href: "/cases", label: "Review queue", icon: Inbox, count: open, hot: true },
    { href: "/invoices", label: "Invoices", icon: ListIcon },
    { href: "/properties", label: "Properties", icon: Building },
    { href: "/agents", label: "Agents", icon: Spark },
    { href: "/proof", label: "Proof & evals", icon: ListIcon },
  ];
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));

  return (
    <DocProvider>
      <div className={`shell theme-aura${collapsed ? " sidebar-collapsed" : ""}`}>
        <aside className="side" data-lenis-prevent>
          <Link href="/" className="brand" aria-label="Accountable.AI home">
            <span className="brand-full"><Wordmark /></span>
            <Mark className="brand-compact" />
          </Link>
          <button className="sidebar-toggle" type="button" onClick={toggleSidebar}
            aria-expanded={!collapsed} aria-controls="sidebar-navigation sidebar-account"
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10 4 6 8l4 4" fill="none" stroke="currentColor" strokeWidth="1.5" /></svg>
            <span className="sidebar-label">{collapsed ? "Expand sidebar" : "Collapse sidebar"}</span>
          </button>
          <nav className="nav" id="sidebar-navigation" aria-label="Main">
            {nav.map(({ href, label, icon: I, count, hot }) => (
              <Link key={href} href={href} className={hot && count ? "attention" : undefined}
                aria-current={active(href) || (href === "/cases" && active("/items")) ? "page" : undefined}
                aria-label={count ? `${label}, ${count} need attention` : label}
                title={collapsed ? (count ? `${label}, ${count} need attention` : label) : undefined}>
                <I /> <span className="sidebar-label">{label}</span>
                {count ? <span className="count">{count}</span> : null}
              </Link>
            ))}
          </nav>
          <div className="side-foot" id="sidebar-account">
            <Link href="/agreements" className="who" aria-label="Your account" title={account?.signed_in ? "Manage your Google profile" : "Connect your Google account"}>
              <span className="who-av">{account?.picture ? <img src={account.picture} alt="" referrerPolicy="no-referrer" /> : controllerInitial}</span>
              <span className="who-text">
                <span>{controllerName ? `${controllerName} · Your account` : "Your account"}</span>
                <small>{state?.meta.parent ?? "Oakline Homes"}</small>
              </span>
            </Link>
            <Link href="/ask" className="ask-pill" aria-label="Ask about Keyway Management" title={collapsed ? "Ask about Keyway Management" : undefined}>
              <Spark style={{ width: 13, height: 13 }} /><span className="sidebar-label">Ask about Keyway Management</span>
            </Link>
          </div>
        </aside>
        <main className="main">
          <div className="wrap">{children}</div>
        </main>
        <ResponsesPanel />
      </div>
    </DocProvider>
  );
}
