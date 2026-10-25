"use client";

import { Disclosure } from "@/components/Disclosure";
import { LiveDesk } from "@/components/LiveDesk";
import { LiveRun } from "@/components/LiveRun";
import { PageTop } from "@/components/ui";

/** Live investigation: feed the running engine and watch it work (components/LiveDesk); the
 *  recorded 20-PDF run (components/LiveRun) stays below as evidence. */
export default function Investigation() {
  return <>
    <PageTop title="Live investigation" sub="Send the engine one document, or let time pass, and watch it work on the same books every tab shows." />
    <LiveDesk />
    <Disclosure title="Recorded run: 20 outside PDFs" hint="Paperwork we didn't write, read by Sonnet over 15 simulated days (Oct 24)">
      <LiveRun />
    </Disclosure>
  </>;
}
