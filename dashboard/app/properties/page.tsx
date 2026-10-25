"use client";

// Properties (UI revamp 2): the Portfolio dashboard for one property. Pick the property in the
// header or in the sidebar list.

import { Loading, PageTop } from "@/components/ui";
import { Charts, ReviewCard, StatusCard, WsHead } from "@/components/workspace/parts";
import { scoped, useWs } from "@/components/workspace/Workspace";

export default function Properties() {
  const ws = useWs();
  if (!ws.ready || !ws.property) return <><PageTop title="Vendor Payments" /><Loading /></>;
  const rows = scoped(ws, true);
  const review = ws.needs.filter(i => i.property === ws.property && i.due >= ws.start && i.due <= ws.end);
  const picker = <select aria-label="Property" value={ws.property} onChange={(e) => ws.setProperty(e.target.value)}>
    {ws.properties.map(p => <option key={p}>{p}</option>)}
  </select>;
  return <>
    <WsHead crumb={<>Properties &nbsp;›<b>{ws.property}</b></>} title="Vendor Payments" extra={picker} exportRows={rows} />
    <ReviewCard rows={review} />
    <StatusCard rows={rows} onProperty />
    <Charts rows={rows} />
  </>;
}
