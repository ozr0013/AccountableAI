"use client";

import { useState } from "react";
import { useFeed } from "./Feed";
import { ResponseList, ResultHead } from "./Responses";
import { money } from "@/lib/format";
import type { ActionResult } from "@/lib/types";

const PROOF = [
  { id: "none", label: "No attachment" },
  { id: "bank", label: "Bank record" },
  { id: "vendor", label: "Vendor confirmation" },
] as const;

/** The judge plays Keyway: answer a reminder, optionally with proof, and watch the agents react. */
export function ReplyBox() {
  const { state, reply, busy, mode, setPanel } = useFeed();
  const open = state?.items.filter((i) => i.owed > 0 && i.alerts.some((a) => a.startsWith("al-unexplained"))) ?? [];
  const [inv, setInv] = useState(open[0]?.id ?? "");
  const [text, setText] = useState("We paid this vendor last week.");
  const [proof, setProof] = useState<"none" | "bank" | "vendor">("none");
  const [res, setRes] = useState<ActionResult | null>(null);
  if (!state) return null;
  const target = open.find((i) => i.id === inv) ?? open[0];

  const send = async () => {
    if (!target) return;
    const r = await reply(target.id, text, proof);
    setPanel(false);          // shown inline here instead of the floating panel
    setRes(r);
  };

  return (
    <div className="card block-shadow" style={{ padding: 0 }}>
      <div className="dots-panel" style={{ borderRadius: "8px 8px 0 0", padding: 20, borderBottom: "1px solid var(--n11)" }}>
        <div className="between" style={{ marginBottom: 12 }}>
          <span className="label">/ Reply as Keyway Management</span>
          <span className="micro muted">the judge plays the middleman</span>
        </div>
        {open.length === 0 ? (
          <div className="notice ok">Nothing unexplained left to answer for.</div>
        ) : (
          <div className="stack" style={{ gap: 10 }}>
            <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
              {open.map((i) => (
                <button key={i.id} className={`chipbtn ${target?.id === i.id ? "on" : ""}`} onClick={() => setInv(i.id)}>
                  {i.vendor} · {money(i.owed, false)}
                </button>
              ))}
            </div>
            <textarea className="input" rows={2} value={text} onChange={(e) => setText(e.target.value)} aria-label="Keyway Management's reply" />
            <div className="between" style={{ flexWrap: "wrap", gap: 10 }}>
              <div className="seg" role="group" aria-label="Attachment">
                {PROOF.map((p) => <button key={p.id} aria-pressed={proof === p.id} onClick={() => setProof(p.id)}>{p.label}</button>)}
              </div>
              <button className="btn" disabled={busy || !text.trim() || mode !== "live"} onClick={send}>
                {busy ? "Agents working…" : `Send to Oakline`}
              </button>
            </div>
            {mode !== "live" && <span className="micro muted" style={{ textTransform: "none", letterSpacing: 0 }}>Needs the live feed: the reply goes through the real chaser, recheck and case agent.</span>}
          </div>
        )}
      </div>
      {res && (
        <div style={{ padding: 20 }}>
          {res.ok ? (
            <div className="stack" style={{ gap: 14 }}>
              <ResultHead res={res} />
              <ResponseList responses={res.responses} />
            </div>
          ) : <div className="notice err">{res.error}</div>}
        </div>
      )}
    </div>
  );
}
