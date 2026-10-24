"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { Aura } from "./Aura";
import { useFeed } from "./Feed";
import { Building, Gavel, Home, Inbox, ListIcon, Mark, Shield, Spark } from "./Icons";
import { DemoBar } from "./DemoBar";
import { Disclosure } from "./Disclosure";
import { ResponsesPanel } from "./Responses";
import { DocProvider } from "./ui";

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
  const pending = state?.cases.filter((c) => c.status === "pending").length ?? 0;
  const open = state?.items.filter((i) => i.attention).length ?? 0;
  const controllerName = account?.name || account?.given_name;
  const controllerInitial = controllerName?.trim().charAt(0).toUpperCase() || "G";
  const nav = [
    { href: "/overview", label: "Overview", icon: Home },
    { href: "/investigation", label: "Live investigation", icon: Spark },
    { href: "/cases", label: "Review queue", icon: Inbox, count: pending },
    { href: "/items", label: "Needs attention", icon: ListIcon, count: open },
    { href: "/keyway", label: "Keyway money trail", icon: Building },
    { href: "/agreements", label: "Your account", icon: Shield },
  ];
  const tools = [
    { href: "/console", label: "Judge Console", icon: Gavel },
    { href: "/ask", label: "Ask about Keyway", icon: Spark },
  ];
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));
  // The aura theme is in review on the overview only; the other pages keep the current look.
  const aura = path === "/overview";

  return (
    <DocProvider>
      <div className={aura ? "shell theme-aura" : "shell"}>
        <aside className="side">
          <Link href="/" className="brand" aria-label="AccountableAI home">
            <Mark className="brand-mark" />
            <span className="brand-word">AccountableAI</span>
          </Link>
          <div className="org">
            <span className="org-badge">O</span>
            <span>{state?.meta.parent ?? "Oakline Homes"}</span>
          </div>
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
          {aura && <Aura />}
          <div className="wrap">{children}</div>
        </main>
        <ResponsesPanel />
      </div>
    </DocProvider>
  );
}
