"use client";

import { Disclosure } from "@/components/Disclosure";
import { JudgeConsole } from "@/components/JudgeConsole";
import { LiveRun } from "@/components/LiveRun";
import { PageTop } from "@/components/ui";

/** Judge Console: give the running engine paperwork, an email, a fact or time, and watch it work
 *  (components/JudgeConsole). The recorded 20-PDF run (components/LiveRun) stays below as evidence. */
export default function Console() {
  return <>
    <PageTop title="Judge Console" sub="Give the engine something real, your own if you like, and watch each agent it wakes up. It works on the same books every tab shows." />
    <JudgeConsole />
    <Disclosure title="Recorded run: 20 outside PDFs" hint="Paperwork we didn't write, read by Sonnet over 15 simulated days (Oct 24)">
      <LiveRun />
    </Disclosure>
  </>;
}
