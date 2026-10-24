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
    <PageTop title="Settings" sub="Connect Google to show your name and photo in AccountableAI." />
    {error && <div className="notice err">{error}</div>}
    <section className="section">
      <div className="ledger-panel"><h2>Profile</h2>
        {account?.signed_in ? <div className="account-profile">
          <div className="account-avatar">{account.picture ? <img src={account.picture} alt="" referrerPolicy="no-referrer" /> : (account.name || "A").charAt(0)}</div>
          <div><div className="account-fields"><label>Full name<input className="input" readOnly value={account.name || account.given_name || "Not provided"}/></label><label>Email address<input className="input" readOnly value={account.email || "Not provided"}/></label><label>Sign-in provider<input className="input" readOnly value="Google"/></label><label>Profile management<span className="body">Your profile is managed through your Google account.</span></label></div><button className="pill" style={{marginTop:24}} onClick={signOut}>Sign out</button></div>
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
