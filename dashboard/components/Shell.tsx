"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { useFeed } from "./Feed";
import { Building, Gavel, Home, Inbox, ListIcon, Mark, Shield, Spark } from "./Icons";
import { DemoBar } from "./DemoBar";
import { Disclosure } from "./Disclosure";
import { ResponsesPanel } from "./Responses";
import { DocProvider } from "./ui";
import "./workspace.css";

export function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const { state } = useFeed();
  const [account, setAccount] = useState<{ signed_in: boolean; name?: string; given_name?: string; picture?: string } | null>(null);
  useEffect(() => {
    let current = true;
    fetch(`${process.env.NEXT_PUBLIC_AGREEMENT_URL || "http://127.0.0.1:8771"}/api/status`, { cache: "no-store" })
      .then((response) => response.ok ? response.json() : null)
      .then((profile) => { if (current && profile?.signed_in) setAccount(profile); })
      .catch(() => undefined);
    return () => { current = false; };
  }, []);
  const [menuOpen, setMenuOpen] = useState(false);
  useEffect(() => { setMenuOpen(false); }, [path]);
  const [query, setQuery] = useState("");
  const matches = query.trim() ? (state?.items ?? []).filter(i => `${i.id} ${i.vendor} ${i.trade ?? ""}`.toLowerCase().includes(query.trim().toLowerCase())).slice(0, 7) : [];
  const pending = state?.cases.filter((c) => c.status === "pending").length ?? 0;
  const open = state?.items.filter((i) => i.attention).length ?? 0;
  const controllerName = account?.name || account?.given_name;
  const controllerInitial = controllerName?.trim().charAt(0).toUpperCase() || "G";
  const nav = [
    { href: "/overview", label: "Portfolio", icon: Home },
    { href: "/investigation", label: "Agent workflow", icon: Spark },
    { href: "/cases", label: "Review queue", icon: Inbox, count: pending },
    { href: "/items", label: "Invoices", icon: ListIcon, count: open },
    { href: "/keyway", label: "Manager payments", icon: Building },
    { href: "/agreements", label: "Settings", icon: Shield },
  ];
  const tools = [
    { href: "/console", label: "Judge Console", icon: Gavel },
    { href: "/ask", label: "Ask about Keyway", icon: Spark },
  ];
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));

  return (
    <DocProvider>
      <div className="workspace shell">
        <header className="workspace-header">
          <Link href="/" className="workspace-brand"><Mark /><span>Accountable<span>AI</span></span></Link>
          <div className="workspace-search">
            <span aria-hidden="true">⌕</span><input aria-label="Search invoices or vendors" placeholder="Search invoice, vendor…" value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => { if(e.key === "Escape") setQuery(""); }} />
            <Link href="/ask" className="assist-link">✧ AI Assist</Link>
            {query.trim() && <div className="search-results">{matches.length ? matches.map(i => <Link key={i.id} href={`/items/${i.id}`} onClick={() => setQuery("")}><strong>{i.vendor}</strong><small>{i.id}</small></Link>) : <p>No matching invoices.</p>}</div>}
          </div>
          <Link className="workspace-notification" href="/cases" aria-label={`${pending} cases awaiting review`}><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" /></svg>{pending > 0 && <span>{pending}</span>}</Link>
          <Link href="/agreements" className="workspace-account"><span>{controllerInitial}</span>Account</Link>
        </header>
        <button className="workspace-menu" aria-expanded={menuOpen} aria-controls="workspace-navigation" onClick={() => setMenuOpen(!menuOpen)}>{menuOpen ? "Close navigation −" : "Menu · " + (nav.find(n => active(n.href))?.label ?? "Workspace") + " +"}</button>
        <aside id="workspace-navigation" className={`side${menuOpen ? " menu-open" : ""}`}>
          <div className="org">
            <span className="org-badge">O</span>
            <span>{state?.meta.parent ?? "Oakline Homes"}</span>
          </div>
          <div className="workspace-nav-label">Navigation</div>
          <nav className="nav" aria-label="Main">
            {nav.map(({ href, label, icon: I, count }) => (
              <Link key={href} href={href} aria-current={active(href) ? "page" : undefined}>
                <I /> {label}
                {count ? <span className="count">{count}</span> : null}
              </Link>
            ))}
            <Disclosure className="nav-disclosure" title="Behind the scenes" hint="Agents and safeguards">
              <Link href="/agents"><Spark /> Agents</Link>
              <Link href="/proof"><Spark /> Proof & evals</Link>
              <Link href="/guardrails"><Shield /> Guardrails</Link>
            </Disclosure>
            <div className="nav-group micro faint" style={{ marginTop: 14, marginBottom: 4 }}>Demo tools</div>
            {tools.map(({ href, label, icon: I }) => (
              <Link key={href} href={href} aria-current={active(href) ? "page" : undefined}>
                <I /> {label}
              </Link>
            ))}
          </nav>
          <DemoBar />
          <div className="side-foot">
            <Link href="/agreements" className="who" title={account?.signed_in ? "Manage your Google profile" : "Connect your Google account"}>
              <span className="who-av">{account?.picture ? <img src={account.picture} alt="" referrerPolicy="no-referrer" /> : controllerInitial}</span>
              <span>{controllerName ? `${controllerName} · Controller` : "Connect Google account"}</span>
            </Link>
            <Link href="/ask" className="ask-pill"><Spark style={{ width: 13, height: 13 }} /> Ask about Keyway</Link>
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
