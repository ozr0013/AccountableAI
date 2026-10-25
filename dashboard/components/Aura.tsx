"use client";

import { useEffect, useRef } from "react";

/** A soft blue aura that drifts behind the content and leans toward the pointer, with thin
 *  geometric guide lines. Decorative only. */
export function Aura({ variant = "page" }: { variant?: "hero" | "page" }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let frame = 0, x = 0, y = 0, tx = 0, ty = 0;
    const tick = () => {
      x += (tx - x) * 0.06;
      y += (ty - y) * 0.06;
      el.style.setProperty("--px", x.toFixed(3));
      el.style.setProperty("--py", y.toFixed(3));
      frame = Math.abs(tx - x) + Math.abs(ty - y) > 0.002 ? requestAnimationFrame(tick) : 0;
    };
    const move = (e: PointerEvent) => {
      if (e.pointerType === "touch") return;
      tx = e.clientX / window.innerWidth - 0.5;
      ty = e.clientY / window.innerHeight - 0.5;
      if (!frame) frame = requestAnimationFrame(tick);
    };
    window.addEventListener("pointermove", move, { passive: true });
    return () => { window.removeEventListener("pointermove", move); cancelAnimationFrame(frame); };
  }, []);
  return (
    <div ref={ref} className={`aura aura-${variant}`} aria-hidden="true">
      <div className="aura-p aura-p1"><div className="aura-blob aura-b1" /></div>
      <div className="aura-p aura-p2"><div className="aura-blob aura-b2" /></div>
      <div className="aura-p aura-p3"><div className="aura-blob aura-b3" /></div>
      <div className="aura-p aura-p4"><div className="aura-blob aura-b4" /></div>
      <svg className="aura-lines" viewBox="0 0 1200 800" preserveAspectRatio="xMidYMid slice">
        <line x1="0" y1="320" x2="1200" y2="320" />
        <line x1="860" y1="0" x2="860" y2="800" />
        <line x1="380" y1="800" x2="1180" y2="0" />
        <line x1="540" y1="0" x2="1180" y2="640" className="aura-faint" />
        <circle cx="860" cy="320" r="150" />
        <circle cx="860" cy="320" r="270" />
        <circle className="aura-dash" cx="860" cy="320" r="390" />
        <circle cx="860" cy="320" r="520" className="aura-faint" />
        <rect x="854" y="314" width="12" height="12" className="aura-tick" />
        <path className="aura-tick" d="M 710 314 v 12 M 1010 314 v 12 M 854 170 h 12 M 854 470 h 12" />
        <path className="aura-tick" d="M 590 314 v 12 M 1130 314 v 12 M 854 50 h 12 M 854 590 h 12" />
        <g className="aura-orbit"><circle cx="860" cy="50" r="4" className="aura-dot" /></g>
        <g className="aura-orbit aura-orbit-slow"><circle cx="860" cy="-70" r="3" className="aura-dot" /></g>
      </svg>
    </div>
  );
}
