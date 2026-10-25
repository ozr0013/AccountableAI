"use client";

import { useFeed } from "@/components/Feed";
import { NeedsAttention } from "@/components/NeedsAttention";
import { Loading, PageTop } from "@/components/ui";

/** Needs attention on its own (the same section sits at the top of the Review queue). */
export default function Items() {
  const { state } = useFeed();
  if (!state) return <><PageTop title="Needs attention" /><Loading /></>;
  return <>
    <PageTop title="Needs attention" sub="What still needs someone." />
    <NeedsAttention state={state} />
  </>;
}
