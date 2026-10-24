"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { useFeed } from "./Feed";
import { Building, Gavel, Home, Inbox, ListIcon, Mark, Shield, Spark } from "./Icons";
import { DemoBar } from "./DemoBar";
import { Disclosure } from "./Disclosure";
import { ResponsesPanel } from "./Responses";
import { DocProvider } from "./ui";

export function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const { state } = useFeed();
  const pending = state?.cases.filter((c) => c.status === "pending").length ?? 0;
  const open = state?.items.filter((i) => i.attention).length ?? 0;
  const nav = [
    { href: "/overview", label: "Overview", icon: Home },
    { href: "/cases", label: "Review queue", icon: Inbox, count: pending },
    { href: "/items", label: "Needs attention", icon: ListIcon, count: open },
    { href: "/keyway", label: "Keyway money trail", icon: Building },
  ];
  const tools = [
    { href: "/console", label: "Judge Console", icon: Gavel },
    { href: "/ask", label: "Ask about Keyway", icon: Spark },
  ];
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));

  return (
    <DocProvider>
      <div className="shell">
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
            <div className="who">
              <span className="who-av">D</span>
              <span>Dana · Controller</span>
            </div>
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
