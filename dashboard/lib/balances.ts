import type { Item, State } from "./types";

/** The money figures every page shows, from one place, so they always agree.
 *
 *  open    = every vendor balance not yet paid, whatever the reason
 *          = keyway (funded by Oakline, not yet paid by Keyway)
 *          + unfunded (Oakline hasn't funded it yet)
 *          + retained (held back under the agreement)
 *          + duplicate (funded twice; must not be paid)
 *  The feed's test (dashboard/feed/tests/test_session.py) checks this adds up after every action. */
export function balances(state: State) {
  const r = state.recon;
  const open = state.items.filter((i) => i.owed > 0).reduce((s, i) => s + i.owed, 0);
  return {
    open,
    keyway: r.owed,
    unexplained: state.headline.unexplained,
    unfunded: r.unfunded ?? 0,
    retained: r.held ?? 0,
    duplicate: r.duplicate_funded ?? 0,
  };
}

/** Oakline's money still with Keyway on one bill: funded, not yet paid to the vendor. */
export const leftToPay = (i: Item) => Math.max(i.funded - i.paid, 0);

/** Keyway is late with it: money left to pay on a bill past its due date. Not the share the agreement
 *  lets Keyway retain until inspection, and not a bill sent twice (that one must never be paid). */
export const lateToPay = (i: Item) =>
  leftToPay(i) > 0 && i.days_late > 0 && !i.duplicate_of && i.cause !== "authorized_hold";

/** Overview's four boxes: paid by Oakline = paid to vendors + left to pay; past due is part of left. */
export function flow(state: State) {
  const r = state.recon;
  const late = state.items.filter(lateToPay);
  return {
    funded: r.funded, paid: r.paid,
    left: Math.round((r.funded - r.paid) * 100) / 100,
    owed: r.owed, retained: r.held ?? 0, duplicate: r.duplicate_funded ?? 0,
    late, lateSum: late.reduce((t, i) => t + leftToPay(i), 0),
    lateUnexplained: late.filter((i) => i.cause === "unexplained").reduce((t, i) => t + leftToPay(i), 0),
  };
}
