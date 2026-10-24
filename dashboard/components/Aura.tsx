/** A soft blue aura that drifts behind the content, with thin geometric guide lines. Decorative only. */
export function Aura({ variant = "page" }: { variant?: "hero" | "page" }) {
  return (
    <div className={`aura aura-${variant}`} aria-hidden="true">
      <div className="aura-blob aura-b1" />
      <div className="aura-blob aura-b2" />
      <div className="aura-blob aura-b3" />
      <svg className="aura-lines" viewBox="0 0 1200 800" preserveAspectRatio="xMidYMid slice">
        <line x1="0" y1="320" x2="1200" y2="320" />
        <line x1="860" y1="0" x2="860" y2="800" />
        <line x1="380" y1="800" x2="1180" y2="0" />
        <circle cx="860" cy="320" r="150" />
        <circle cx="860" cy="320" r="270" />
        <circle className="aura-dash" cx="860" cy="320" r="390" />
        <path className="aura-tick" d="M 710 314 v 12 M 1010 314 v 12 M 854 170 h 12 M 854 470 h 12" />
        <path className="aura-tick" d="M 590 314 v 12 M 1130 314 v 12 M 854 50 h 12 M 854 590 h 12" />
      </svg>
    </div>
  );
}
