"use client";

import { Disclosure } from "@/components/Disclosure";
import { LivePipeline } from "@/components/LivePipeline";
import { LiveRun } from "@/components/LiveRun";
import { SystemMap } from "@/components/SystemMap";
import { PageTop } from "@/components/ui";

/** Live investigation: the three agents working in real time (components/LivePipeline), with the
 *  outside-paperwork run through the engine (components/LiveRun) below. */
export default function Investigation() {
  return <>
    <PageTop title="Live investigation" meta={false} />
    <SystemMap />
    <LivePipeline />
    <Disclosure title="Run outside paperwork through the engine"><LiveRun /></Disclosure>
  </>;
}
