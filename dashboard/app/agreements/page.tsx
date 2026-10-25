"use client";

import { useCallback, useEffect, useState } from "react";
import { PageTop } from "@/components/ui";

const AGENT = process.env.NEXT_PUBLIC_AGREEMENT_URL || "http://127.0.0.1:8771";
type Account = { signed_in: boolean; email?: string; name?: string; given_name?: string; picture?: string; error?: string };

export default function AccountPage() {
  const [account, setAccount] = useState<Account | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      const response = await fetch(`${AGENT}/api/status`, { cache: "no-store" });
      if (!response.ok) throw new Error("The profile connector is unavailable.");
      setAccount(await response.json());
    } catch {
      setError("The profile connector is unavailable. Start it with python -m agreement_agent.server.");
    }
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("login_error")) setError(`Google sign-in failed: ${params.get("login_error")}`);
    if (params.has("login") || params.has("login_error")) window.history.replaceState(null, "", "/agreements");
    load();
  }, [load]);

  const signIn = () => {
    window.location.href = `${AGENT}/auth/google?next=${encodeURIComponent(`${window.location.origin}/agreements`)}`;
  };
  const signOut = async () => {
    await fetch(`${AGENT}/auth/logout`, { method: "POST" });
    setAccount({ signed_in: false });
    window.location.reload();
  };

  return <>
    <PageTop title="Your account" sub="Connect Google to show your name and photo." />
    {error && <div className="notice err">{error}</div>}
    <section className="section">
      <div className="card block-shadow dots-panel" style={{ padding: 28 }}>
        {account?.signed_in ? <div className="between" style={{ alignItems: "center", gap: 20 }}>
          <div className="row" style={{ gap: 14 }}>
            {account.picture && <img src={account.picture} alt="" referrerPolicy="no-referrer" style={{ width: 52, height: 52, borderRadius: "50%", objectFit: "cover" }} />}
            <div className="stack" style={{ gap: 5 }}>
              <span className="micro muted">Connected Google account</span>
              <span className="display h3">{account.name || account.given_name || "Your account"}</span>
              <span className="body muted">{account.email}</span>
            </div>
          </div>
          <button className="pill" onClick={signOut}>Sign out</button>
        </div> : <div className="between" style={{ alignItems: "center", gap: 20 }}>
          <div className="stack" style={{ gap: 7 }}>
            <span className="display h4">Make this your workspace.</span>
            <span className="body muted">Sign in with your Google profile to personalize the dashboard.</span>
            {account?.error && <span className="micro warn-ink">{account.error}</span>}
          </div>
          <button className="google-btn" onClick={signIn}>Continue with Google</button>
        </div>}
      </div>
    </section>
    <section className="section">
      <div className="card" style={{ padding: 24 }}>
        <span className="display h4">Your inbox stays private.</span>
        <p className="body muted" style={{ maxWidth: 650, margin: "10px 0 0" }}>This connection uses your name, email address, and profile photo. It does not request permission to read or send Gmail messages.</p>
      </div>
    </section>
  </>;
}
