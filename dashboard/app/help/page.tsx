"use client";

// Help & Support (UI revamp 2): answers about the workspace, the invoice data as CSV, and a
// support request to download.

import { Loading, PageTop } from "@/components/ui";
import { csv, download } from "@/lib/workspace";
import { SupportDialog, WsHead } from "@/components/workspace/parts";
import { useWs } from "@/components/workspace/Workspace";

const FAQ: [string, string][] = [
  ["How do I review a flagged invoice?", "Open Portfolio or Properties and press Review on any invoice in the review list, or pick “Needs Review” in the Invoices status filter. The review shows the amount, due date, status, the issue the agents found and why. Add a note, then Mark reviewed. Reviewing never issues or authorizes a payment."],
  ["How do status updates work?", "Every figure comes from the live ledger. Paid means the vendor’s payment is proven by a statement, bank record or vendor confirmation, never by the manager’s word alone. Outstanding is owed and not yet due; Past Due is owed after the due date. The chaser agent reminds Keyway about past-due items on the approved schedule."],
  ["How do search and filters work?", "Search from the top bar (⌘K) by vendor, invoice ID, CID or property; it opens Invoices with your search applied. Use ⚑ or the Invoices filter bar for property, vendor and status, and the date button to set the due-date range. Clear filters resets them."],
  ["Where is my data saved?", "Invoices, payments and alerts come from the AccountableAI backend’s append-only event log. Your review notes, profile and preferences are saved in this browser only."],
  ["What do the graphs show?", "Payment Trends is the invoiced amount by due date for the chosen period. Outstanding is the unpaid balance by trade. Aging of the Payables groups the unpaid balance by days past due."],
];

export default function Help() {
  const ws = useWs();
  if (!ws.ready) return <><PageTop title="Help & Support" /><Loading /></>;
  const exportAll = () => { download("accountable-invoices.csv", csv(ws.invoices, ws.reviews), "text/csv"); ws.toast(`Exported ${ws.invoices.length} invoices`); };
  return <>
    <WsHead crumb={<>Help &amp; Support &nbsp;›<b>{ws.profile.company}</b></>} title="Help & Support" exportRows={ws.invoices} />
    <section className="lr-box ws-card">
      <h3 className="ws-h3">How can we help?</h3>
      {FAQ.map(([q, a]) => <details key={q} className="ws-helpitem"><summary>{q}</summary><p>{a}</p></details>)}
    </section>
    <section className="lr-box ws-card">
      <div className="ws-sectionhead"><div><h3 className="ws-h3">Still need help?</h3>
        <div className="ws-muted">Download your invoice data or write up a request to share with support.</div></div>
        <div style={{ display: "flex", gap: 8 }}>
          <button type="button" className="ws-btn" onClick={exportAll}>Download invoice data</button>
          <button type="button" className="btn" onClick={() => ws.openDialog(<SupportDialog />)}>Create support request</button>
        </div></div>
    </section>
  </>;
}
