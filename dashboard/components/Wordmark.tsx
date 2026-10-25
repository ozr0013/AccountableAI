/** The ACCOUNTABLE logo: the mark and the wordmark, one logo for the landing page and the dashboard. */
export function Wordmark() {
  return <span className="wordmark">
    {/* eslint-disable-next-line @next/next/no-img-element -- a small static mark, no optimisation needed */}
    <img className="wordmark-mark" src="/logo.png" alt="" width={26} height={26} />
    ACCOUNTABLE
  </span>;
}
