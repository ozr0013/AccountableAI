import type { ReactNode } from "react";

/** Native disclosure: keyboard operable, closed until the reader asks for detail. */
export function Disclosure({ title, hint, meta, children, className = "" }: {
  title: string; hint?: string; meta?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <details className={`disclosure ${className}`}>
      <summary><span className="disclosure-copy"><span className="disclosure-title">{title}</span>{hint && <span className="disclosure-hint">{hint}</span>}</span>{meta != null && <span className="disclosure-meta">{meta}</span>}<span className="disclosure-plus" aria-hidden="true" /></summary>
      <div className="disclosure-body">{children}</div>
    </details>
  );
}
