// Stroke icons, 16px grid, currentColor.
type P = { className?: string; style?: React.CSSProperties };
const S = { fill: "none", stroke: "currentColor", strokeWidth: 1.2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };

export const Home = (p: P) => (
  <svg viewBox="0 0 16 16" {...p}><path {...S} d="M2.5 7 8 2.7 13.5 7v6.3h-11Z M6.3 13.3V9.5h3.4v3.8" /></svg>
);
export const ListIcon = (p: P) => (
  <svg viewBox="0 0 16 16" {...p}><path {...S} d="M5.5 4h8M5.5 8h8M5.5 12h8M2.5 4h.01M2.5 8h.01M2.5 12h.01" /></svg>
);
export const Inbox = (p: P) => (
  <svg viewBox="0 0 16 16" {...p}><path {...S} d="M2.5 9.5 4 3.5h8l1.5 6v3h-11Zm0 0h3.2l.8 1.6h3l.8-1.6h3.2" /></svg>
);
export const Building = (p: P) => (
  <svg viewBox="0 0 16 16" {...p}><path {...S} d="M3 13.5V3.5h6.5v10M9.5 6.5h3.5v7M1.5 13.5h13M5 6h1.5M5 8.5h1.5M5 11h1.5" /></svg>
);
export const Gavel = (p: P) => (
  <svg viewBox="0 0 16 16" {...p}><path {...S} d="m6.5 3.5 4 4M8.5 1.5l4 4-2 2-4-4ZM8.5 7.5 3 13M2 14.5h6" /></svg>
);
export const Spark = (p: P) => (
  <svg viewBox="0 0 16 16" {...p}><path {...S} d="M8 1.8 9.4 6.6 14.2 8 9.4 9.4 8 14.2 6.6 9.4 1.8 8l4.8-1.4Z" /></svg>
);
export const Shield = (p: P) => (
  <svg viewBox="0 0 16 16" {...p}><path {...S} d="M8 1.8 13 3.6v4c0 3.1-2.1 5.4-5 6.6-2.9-1.2-5-3.5-5-6.6v-4Z" /></svg>
);
export const Check = (p: P) => (
  <svg viewBox="0 0 10 10" {...p}><path fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" d="M2.2 5.2 4.2 7.1 7.9 3" /></svg>
);
export const Flag = (p: P) => (
  <svg viewBox="0 0 10 10" {...p}><path fill="none" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" strokeLinejoin="round" d="M2.6 8.6V1.8m0 .5s.4-.4 1.6-.4 2 .8 3.2.8 1.6-.4 1.6-.4v4.4s-.4.4-1.6.4-2-.8-3.2-.8-1.6.4-1.6.4" /></svg>
);
export const Cross = (p: P) => (
  <svg viewBox="0 0 10 10" {...p}><path fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" d="m3 3 4 4M7 3 3 7" /></svg>
);
export const Arrow = (p: P) => (
  <svg viewBox="0 0 16 16" {...p}><path {...S} d="M3 8h10M9 4l4 4-4 4" /></svg>
);

/** AccountableAI mark: a ledger square with the reconciling tick. */
export const Mark = (p: P) => (
  <svg viewBox="0 0 24 24" {...p}>
    <rect x="1.5" y="1.5" width="21" height="21" rx="3" fill="currentColor" />
    <path d="M6 8h12M6 12h6M6 16h4" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" />
    <path d="m13.5 16 2 2 3.5-4" fill="none" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);
