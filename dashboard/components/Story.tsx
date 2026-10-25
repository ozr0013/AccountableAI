"use client";

import { useEffect, useRef, useState } from "react";

/** The landing page's scroll story: what happens to the owner's money, told in seven beats.
 *  The text carries the story; the sticky diagram beside it is decorative. */
const CHAPTERS = [
  { tag: "FUND", title: "You fund the work.",
    body: "Every month you send money to the company that manages your homes, so it can pay the plumbers, cleaners and electricians who do the work." },
  { tag: "TRUST", title: "They’re meant to pay your vendors.",
    body: "That’s the deal: the money reaches the people who did the job, in full and on time. You rarely get to see whether it did." },
  { tag: "STATEMENT", title: "The statement says the payment is overdue.",
    body: "This creates confusion among owners." },
  { tag: "GAP", title: "Some vendors are still waiting.",
    body: "A plumber’s past-due notice. No payment in the bank. Three invoices, $3,140, with no proof they were ever paid." },
  { tag: "FOLLOW UP", title: "We ask. Politely, then firmly.",
    body: "Reminders go out on a schedule you approved. A reply that only says “we paid” doesn’t close the question. A receipt does." },
  { tag: "UPDATE", title: "One fact changes. So does the answer.",
    body: "One invoice turns out to have arrived later than recorded, so it isn’t late after all. It clears. The other two stay open." },
  { tag: "DECIDE", title: "You decide before you send more.",
    body: "Before the next $18,400 goes out, you see what’s still unproven and the hold we recommend. Nothing moves without you." },
];

const VENDORS = [
  { name: "Rio Grande", trade: "Plumbing", amount: "$1,240" },
  { name: "Brightside", trade: "Cleaning", amount: "$1,000" },
  { name: "Sparkline", trade: "Electric", amount: "$900" },
];

export function Story() {
  const [step, setStep] = useState(0);
  const refs = useRef<(HTMLElement | null)[]>([]);
  useEffect(() => {
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting) setStep(Number((e.target as HTMLElement).dataset.step));
    }, { rootMargin: "-45% 0px -45% 0px" });
    refs.current.forEach((el) => el && io.observe(el));
    return () => io.disconnect();
  }, []);
  const at = (n: number) => (step >= n ? " in" : "");
  const vendorState = (i: number) => (step >= 5 && i === 2 ? "clear" : step >= 3 ? "late" : step >= 1 ? "owed" : "");
  const vendorNote = (i: number) => {
    const s = vendorState(i);
    return s === "clear" ? "Not due yet" : s === "late" ? "Not received" : s === "owed" ? `Owed ${VENDORS[i].amount}` : VENDORS[i].trade;
  };

  return (
    <section className="story ruled" id="story" aria-labelledby="story-title">
      <div className="story-head">
        <span className="home-eyebrow">/ THE STORY</span>
        <h2 id="story-title">Where did the money<em>actually go?</em></h2>
      </div>
      <div className="story-grid">
        <div className="story-steps">
          {CHAPTERS.map((c, i) => (
            <article key={c.tag} ref={(el) => { refs.current[i] = el; }} data-step={i} className={i === step ? "on" : ""}>
              <span className="feature-number">{String(i + 1).padStart(2, "0")} / {c.tag}</span>
              <h3>{c.title}</h3>
              <p>{c.body}</p>
            </article>
          ))}
        </div>
        <div className="story-stage" aria-hidden="true">
          <div className="stage-canvas" data-step={step}>
            <svg className="stage-lines" viewBox="0 0 560 500">
              <path className={`ln fund${at(0)}`} d="M 120 250 H 210" />
              <path className={`ln pay${at(1)}${step >= 3 ? " broken" : ""}`} d="M 350 250 H 390 V 126 H 430" />
              <path className={`ln pay${at(1)}${step >= 3 ? " broken" : ""}`} d="M 350 250 H 430" />
              <path className={`ln pay${at(1)}${step >= 5 ? " clear" : step >= 3 ? " broken" : ""}`} d="M 350 250 H 390 V 374 H 430" />
            </svg>
            <span className={`pkt${step === 0 || step === 6 ? " in" : ""}`} />
            <div className="stage-node you in"><b>You</b><small>Oakline Homes</small></div>
            <div className={`stage-node mid in${step === 4 ? " ping" : ""}`}><b>Property manager</b><small>Keyway Management</small></div>
            {VENDORS.map((v, i) => (
              <div key={v.name} className={`stage-node vendor v${i} ${vendorState(i)}`}><b>{v.name}</b><small>{vendorNote(i)}</small></div>
            ))}
            <div className={`stage-statement${at(2)}${step >= 3 ? " doubted" : ""}`}>
              <span className="stage-label">Monthly statement</span>
              {VENDORS.map((v) => <span key={v.name} className="row"><span>{v.name}</span><i>Overdue</i></span>)}
            </div>
            <div className={`stage-bubble${step === 4 ? " in" : ""}`}>“We paid it.”<small>No receipt yet</small></div>
            <div className={`stage-hold${at(3)}${step >= 6 ? " decide" : ""}`}>
              <span><span className="stage-label">Recommended hold</span><b key={step >= 5 ? "after" : "before"}>{step >= 5 ? "$2,240" : "$3,140"}</b></span>
              <small>of the next $18,400</small>
              <span className="stage-choice"><i>Approve</i><i>Decline</i></span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
