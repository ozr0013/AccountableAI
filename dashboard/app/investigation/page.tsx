"use client";

import Link from "next/link";
import { PageTop } from "@/components/ui";

/** Live investigation: real paperwork run through the system. The 15-day simulation is on /simulation. */
export default function Investigation() {
  return <>
    <PageTop title="Live investigation" />
    <section className="sim-empty">
      <p>Real paperwork runs land here.</p>
      <Link className="pill" href="/simulation">Open the simulated flow ↗</Link>
    </section>
  </>;
}
