"use client";

// Portfolio (UI revamp 2): the invoices that need review, the status of every dollar, and the
// trends, for all of Oakline's properties.

import { Loading, PageTop } from "@/components/ui";
import { Charts, ReviewCard, StatusCard, WsHead } from "@/components/workspace/parts";
import { scoped, useWs } from "@/components/workspace/Workspace";

export default function Portfolio() {
  const ws = useWs();
  if (!ws.ready) return <><PageTop title="Vendor Payments" /><Loading /></>;
  const rows = scoped(ws, false);
  const review = ws.needs.filter(i => i.due >= ws.start && i.due <= ws.end);
  return <>
    <WsHead crumb={<>Portfolio &nbsp;›<b>{ws.profile.company}</b></>} title="Vendor Payments" exportRows={rows} />
    <ReviewCard rows={review} />
    <StatusCard rows={rows} onProperty={false} />
    <Charts rows={rows} />
  </>;
}
