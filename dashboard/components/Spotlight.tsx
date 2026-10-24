"use client";

import { useEffect, useRef, type ReactNode } from "react";

/** A decorative dot field. Pointer updates stay out of React's render loop. */
export function Spotlight({ children, className = "" }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let frame = 0;
    let previous = 0;
    let x = el.clientWidth * .7;
    let y = el.clientHeight * .5;
    let targetX = x, targetY = y;
    const paint = () => {
      el.style.setProperty("--light-x", `${x}px`);
      el.style.setProperty("--light-y", `${y}px`);
    };
    const tick = (now: number) => {
      const dt = previous ? Math.min(now - previous, 50) : 16;
      previous = now;
      const ease = 1 - Math.exp(-dt / 85);
      x += (targetX - x) * ease;
      y += (targetY - y) * ease;
      paint();
      if (Math.abs(targetX - x) + Math.abs(targetY - y) > .2) {
        frame = requestAnimationFrame(tick);
      } else {
        frame = 0;
        previous = 0;
      }
    };
    const move = (event: PointerEvent) => {
      if (motion.matches || event.pointerType === "touch") return;
      const bounds = el.getBoundingClientRect();
      targetX = event.clientX - bounds.left;
      targetY = event.clientY - bounds.top;
      el.dataset.tracking = "true";
      if (!frame) frame = requestAnimationFrame(tick);
    };
    const leave = () => { delete el.dataset.tracking; };
    const stop = () => {
      cancelAnimationFrame(frame);
      frame = 0;
      previous = 0;
      leave();
    };
    paint();
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerleave", leave);
    motion.addEventListener("change", stop);
    return () => {
      stop();
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerleave", leave);
      motion.removeEventListener("change", stop);
    };
  }, []);
  return (
    <section ref={ref} className={`moment spotlight ${className}`}>
      <div className="spotlight-field" aria-hidden="true" />
      {children}
    </section>
  );
}
