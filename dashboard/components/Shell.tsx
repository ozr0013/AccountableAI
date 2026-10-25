"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { useFeed } from "./Feed";
import { Wordmark } from "./Wordmark";
import { Building, Gavel, Home, Inbox, ListIcon, Shield, Spark } from "./Icons";
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
  const open = state?.items.filter((i) => i.attention).length ?? 0;
  const controllerName = account?.name || account?.given_name;
  const controllerInitial = controllerName?.trim().charAt(0).toUpperCase() || "G";
  const nav = [
    { href: "/overview", label: "Overview", icon: Home },
    { href: "/console", label: "Judge Console", icon: Gavel },
    // the number is what still needs someone; it goes when everything is cleared
    { href: "/cases", label: "Review queue", icon: Inbox, count: open, hot: true },
    { href: "/invoices", label: "Invoices", icon: ListIcon },
    { href: "/properties", label: "Properties", icon: Building },
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
            {nav.map(({ href, label, icon: I, count, hot }) => (
              <Link key={href} href={href} className={hot && count ? "attention" : undefined}
                aria-current={active(href) || (href === "/cases" && active("/items")) ? "page" : undefined}
                aria-label={count ? `${label}, ${count} need attention` : undefined}>
                <I /> {label}
                {count ? <span className="count">{count}</span> : null}
              </Link>
            ))}
            <Disclosure className="nav-disclosure" title="Behind the scenes" hint="Agents and safeguards">
              <Link href="/agents"><Spark /> Agents</Link>
              <Link href="/proof"><Spark /> Proof & evals</Link>
              <Link href="/guardrails"><Shield /> Guardrails</Link>
            </Disclosure>
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
            <Link href="/ask" className="ask-pill"><Spark style={{ width: 13, height: 13 }} /> Ask about Keyway Management</Link>
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
