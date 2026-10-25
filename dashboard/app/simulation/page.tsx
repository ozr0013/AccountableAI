"use client";

import { Simulation } from "@/components/Simulation";
import { useFeed } from "@/components/Feed";
import { Loading, PageTop } from "@/components/ui";

/** Simulated flow: 15 days through the real workflow, step by step (components/Simulation). */
export default function SimulatedFlow() {
  const { state } = useFeed();
  return <>
    <PageTop title="Simulated flow" />
    {state ? <Simulation /> : <Loading />}
  </>;
}
