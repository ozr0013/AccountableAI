"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { Aura } from "./Aura";
import { useFeed } from "./Feed";
import { Wordmark } from "./Wordmark";
import { Arrow, Building, Gavel, Home, Inbox, ListIcon, Shield, Spark } from "./Icons";
import { DemoBar } from "./DemoBar";
import { Disclosure } from "./Disclosure";
import { ResponsesPanel } from "./Responses";
import { DocProvider } from "./ui";
import { LogoutDialog, TopBar, WorkspaceLayer } from "./workspace/parts";
import { useWs } from "./workspace/Workspace";

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
  const open = state?.items.filter((i) => i.attention).length ?? 0;
  const ws = useWs();
  const controllerName = account?.name || account?.given_name;
  const controllerInitial = controllerName?.trim().charAt(0).toUpperCase() || "G";
  // the workspace (UI revamp 2's pages) first; the rest of the dashboard under More
  const nav = [
    { href: "/portfolio", label: "Portfolio", icon: Home },
    { href: "/properties", label: "Properties", icon: Building, count: ws.needs.length, hot: true },
    { href: "/invoices", label: "Invoices", icon: ListIcon, count: ws.invoices.length },
    { href: "/judgment", label: "Judgment", icon: Gavel },
  ];
  const more = [
    { href: "/overview", label: "Overview", icon: Home },
    { href: "/investigation", label: "Live investigation", icon: Spark },
    { href: "/simulation", label: "Simulated flow", icon: Spark },
    // the number is what still needs someone; it goes when everything is cleared
    { href: "/cases", label: "Review queue", icon: Inbox, count: open },
    { href: "/agents", label: "Agents", icon: Spark },
    { href: "/proof", label: "Proof & evals", icon: Spark },
    { href: "/guardrails", label: "Guardrails", icon: Shield },
    { href: "/console", label: "Judge Console", icon: Gavel },
  ];
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));

  return (
    <DocProvider>
      <div className="shell theme-aura">
        <aside className="side" data-lenis-prevent>
          <Link href="/" className="brand" aria-label="Accountable.AI home">
            <Wordmark />
          </Link>
          <nav className="nav" aria-label="Main">
            <div className="ws-navlabel">Navigation</div>
            {nav.map(({ href, label, icon: I, count, hot }) => (
              <Link key={href} href={href} className={hot && count ? "attention" : undefined}
                aria-current={active(href) ? "page" : undefined}
                aria-label={count ? `${label}, ${count}` : undefined}>
                <I /> {label}
                {count ? <span className="count">{count}</span> : null}
              </Link>
            ))}
            {active("/properties") && ws.properties.length > 0 && <>
              <div className="ws-navlabel">Select property</div>
              <div className="ws-propnav">{ws.properties.map((p) => (
                <button key={p} type="button" className={ws.property === p ? "on" : ""} onClick={() => ws.setProperty(p)}>
                  {p}{ws.needs.some((i) => i.property === p) && <i className="ws-dot" aria-label="needs review" />}
                </button>))}</div>
            </>}
            <Disclosure className="nav-disclosure" title="More from Keyway" hint="Live investigation, review queue, agents">
              {more.map(({ href, label, icon: I, count }) => (
                <Link key={href} href={href} aria-current={active(href) || (href === "/cases" && active("/items")) ? "page" : undefined}>
                  <I /> {label}{count ? <span className="count">{count}</span> : null}
                </Link>))}
            </Disclosure>
            <Link href="/settings" aria-current={active("/settings") ? "page" : undefined}><Shield /> Settings</Link>
            <Link href="/help" aria-current={active("/help") ? "page" : undefined}><Spark /> Help &amp; Support</Link>
            <button type="button" className="ws-navbtn" onClick={() => ws.openDialog(<LogoutDialog />)}><Arrow /> Log Out</button>
          </nav>
          <DemoBar />
          <div className="side-foot">
            <Link href="/agreements" className="who" title={account?.signed_in ? "Manage your Google profile" : "Connect your Google account"}>
              <span className="who-av">{account?.picture ? <img src={account.picture} alt="" referrerPolicy="no-referrer" /> : controllerInitial}</span>
              <span className="who-text">
                <span>{controllerName ? `${controllerName} · Your account` : "Your account"}</span>
                <small>{state?.meta.parent ?? "Oakline Homes"}</small>
              </span>
            </Link>
            <Link href="/ask" className="ask-pill"><Spark style={{ width: 13, height: 13 }} /> Ask about Keyway</Link>
          </div>
        </aside>
        <main className="main">
          <Aura />
          <div className="wrap"><TopBar />{children}</div>
        </main>
        <ResponsesPanel />
        <WorkspaceLayer />
      </div>
    </DocProvider>
  );
}
