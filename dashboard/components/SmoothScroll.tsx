"use client";

import Lenis from "lenis";
import { useEffect } from "react";

/** Lenis smooth scrolling for the page. Off for reduced motion; panels with their own scroll
 *  (sidebar, drawers) opt out with data-lenis-prevent. */
export function SmoothScroll() {
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const lenis = new Lenis({ autoRaf: true, anchors: { offset: -88 }, lerp: 0.09 });
    return () => lenis.destroy();
  }, []);
  return null;
}
