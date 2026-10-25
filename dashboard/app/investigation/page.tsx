"use client";

import { Simulation } from "@/components/Simulation";
import { useFeed } from "@/components/Feed";
import { Loading, PageTop } from "@/components/ui";

/** Live investigation: 15 days through the real workflow, step by step (components/Simulation). */
export default function Investigation() {
  const { state } = useFeed();
  return <>
    <PageTop title="Live investigation" />
    {state ? <Simulation /> : <Loading />}
  </>;
}
