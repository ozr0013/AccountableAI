"use client";

// Settings (UI revamp 2): the profile, notification preferences and security. Saved on this
// device; nothing here changes the backend.

import { useEffect, useState, type ChangeEvent } from "react";
import { Loading, PageTop } from "@/components/ui";
import { initials, SecurityDialog, WsHead } from "@/components/workspace/parts";
import { useWs } from "@/components/workspace/Workspace";
import type { Prefs, Profile } from "@/lib/workspace";

const FIELDS: [keyof Profile, string, string][] = [
  ["name", "Full Name", "text"], ["email", "Email Address", "email"], ["company", "Company Name", "text"],
  ["management", "Management Company", "text"], ["phone", "Phone Number", "tel"],
];
const TOGGLES: [keyof Prefs, string, string][] = [
  ["overdue", "Overdue Payments", "Alert me when an invoice is past due"],
  ["review", "Invoices to Review", "Alert me when an invoice is flagged for review"],
  ["payment", "Payment Confirmations", "Tell me when a vendor payment is confirmed"],
  ["weekly", "Weekly Summary", "A weekly digest of payments and open balances"],
  ["compact", "Compact Invoice Rows", "Show more invoices on screen at once"],
];

export default function Settings() {
  const ws = useWs();
  const [profile, setProfile] = useState<Profile>(ws.profile);
  const [prefs, setPrefs] = useState<Prefs>(ws.prefs);
  const [editing, setEditing] = useState(false);
  useEffect(() => { setProfile(ws.profile); setPrefs(ws.prefs); }, [ws.profile, ws.prefs]);
  if (!ws.ready) return <><PageTop title="Settings" /><Loading /></>;

  const photo = (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]; e.target.value = "";
    if (!f) return;
    if (f.size > 1.5 * 1024 * 1024) { ws.toast("Photo must be 1.5 MB or smaller"); return; }
    const r = new FileReader();
    r.onload = () => { setProfile(p => ({ ...p, photo: String(r.result) })); setEditing(true); };
    r.readAsDataURL(f);
  };
  const cancel = () => { setProfile(ws.profile); setPrefs(ws.prefs); setEditing(false); ws.toast("Unsaved changes discarded"); };
  const save = () => { ws.saveProfile(profile, prefs); setEditing(false); ws.toast("Profile and preferences saved on this device"); };

  return <>
    <WsHead crumb={<>Settings &nbsp;›<b>{ws.profile.company}</b></>} title="Settings" exportRows={ws.invoices} />
    <section className="lr-box ws-card">
      <div className="ws-sectionhead"><h3 className="ws-h3">Profile</h3>
        <button type="button" className="ws-btn" onClick={() => setEditing(e => !e)}>✎ Edit</button></div>
      <div className="ws-profile">
        <div style={{ textAlign: "center" }}>
          <div className="ws-bigavatar">{profile.photo ? <img src={profile.photo} alt="" /> : initials(profile.name)}</div>
          <label className="ws-btn" style={{ cursor: "pointer" }}>Change Photo<input type="file" accept="image/*" hidden onChange={photo} /></label>
          <div className="ws-muted" style={{ marginTop: 6 }}>JPG or PNG, max 1.5 MB</div>
        </div>
        <div className="ws-form ws-formgrid">
          {FIELDS.map(([k, label, type]) => <label key={k}>{label}
            <input type={type} value={profile[k]} disabled={!editing} onChange={(e) => setProfile(p => ({ ...p, [k]: e.target.value }))} /></label>)}
        </div>
      </div>
    </section>
    <section className="lr-box ws-card">
      <h3 className="ws-h3">Notifications</h3>
      {TOGGLES.map(([k, label, desc]) => <div key={k} className="ws-settingrow">
        <div><b>{label}</b><div className="ws-muted">{desc}</div></div>
        <input type="checkbox" className="ws-switch" aria-label={label} checked={prefs[k]} onChange={(e) => setPrefs(p => ({ ...p, [k]: e.target.checked }))} />
      </div>)}
      <p className="ws-muted">Preferences are saved on this device. Email alerts come from the agents themselves, on the controller-approved schedule.</p>
    </section>
    <section className="lr-box ws-card">
      <h3 className="ws-h3">Security</h3>
      <div className="ws-settingrow"><div><b>Password</b><div className="ws-muted">Managed by your Google sign-in</div></div>
        <button type="button" className="ws-btn" onClick={() => ws.openDialog(<SecurityDialog />)}>Manage account</button></div>
      <div className="ws-settingrow"><div><b>Two-Factor Authentication</b><div className="ws-muted">Set up in your Google account</div></div>
        <button type="button" className="ws-btn" onClick={() => ws.openDialog(<SecurityDialog />)}>Manage 2FA</button></div>
      <div className="ws-settingrow"><div><b>Active Session</b><div className="ws-muted">This browser, signed in now</div></div>
        <span className="ws-pill">This Device</span></div>
    </section>
    <div className="ws-savebar">
      <button type="button" className="ws-btn" onClick={cancel}>Cancel</button>
      <button type="button" className="btn" onClick={save}>✓ Save Changes</button>
    </div>
  </>;
}
