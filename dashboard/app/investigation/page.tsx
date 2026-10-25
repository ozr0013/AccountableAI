"use client";

import { FlowLive } from "@/components/FlowLive";
import { useFeed } from "@/components/Feed";
import { Loading, PageTop } from "@/components/ui";

/** Live investigation: one file in, and the path it takes through the workflow, top to bottom. */
export default function Investigation() {
  const { state } = useFeed();
  return <>
    <PageTop title="Live investigation" />
    {state ? <FlowLive /> : <Loading />}
  </>;
}
