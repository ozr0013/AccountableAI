"use client";

// Judgment (UI revamp 2): the Agent Workflow. The agreement agent, chaser and anomaly detector run
// one after another on the live feed, each agent's reasoning streaming into its card.

import { AgentWorkflowView } from "@/components/AgentWorkflowView";
import { WsHead } from "@/components/workspace/parts";
import { scoped, useWs } from "@/components/workspace/Workspace";

export default function Judgment() {
  const ws = useWs();
  return <AgentWorkflowView head={(run, mode) => <>
    <WsHead crumb={<>Judgment &nbsp;›<b>Oakline Homes</b></>} title="Agent Workflow" exportRows={scoped(ws, false)}
      extra={<><span className="ws-pill">{mode}</span>{run}</>} />
    <p className="ws-muted" style={{ margin: "-12px 0 20px" }}>Owner pays the middleman, the middleman pays the vendor. Three agents check every step.</p>
  </>} />;
}
