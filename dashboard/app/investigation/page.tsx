"use client";

import { LiveRun } from "@/components/LiveRun";
import { PageTop } from "@/components/ui";

/** Live investigation: outside paperwork run through the engine (components/LiveRun). */
export default function Investigation() {
  return <>
    <PageTop title="Live investigation" />
    <LiveRun />
  </>;
}
