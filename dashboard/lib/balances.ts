import type { Item } from "./types";

/** The money rules every page uses, so a change anywhere moves every figure together.
 *
 *  Each vendor bill splits three ways, which always add up to what the vendor billed:
 *     billed = paid to vendors + left to pay (by Keyway) + not yet funded (by Oakline)
 *  and Oakline's money to Keyway:
 *     paid by Oakline = paid to vendors + left to pay
 *  Past due is the part of "left to pay" whose due date has passed.
 *  The feed's test (dashboard/feed/tests/test_simulation.py) checks these after every action. */

/** Oakline's money still with Keyway on this bill. */
export const leftToPay = (i: Item) => Math.max(i.funded - i.paid, 0);

/** What the vendor billed that Oakline hasn't sent Keyway yet. */
export const notFunded = (i: Item) => Math.max(i.amount - Math.max(i.funded, i.paid), 0);

/** Keyway is late with it: left to pay, past the due date. Not the share the agreement lets Keyway
 *  retain until inspection, and not a bill sent twice (that one must never be paid). */
export const lateToPay = (i: Item) =>
  leftToPay(i) > 0 && i.days_late > 0 && !i.duplicate_of && i.cause !== "authorized_hold";

const cents = (n: number) => Math.round(n * 100) / 100;

/** Every figure, summed over whichever bills a page shows. */
export function totals(items: Item[]) {
  const sum = (f: (i: Item) => number) => cents(items.reduce((t, i) => t + f(i), 0));
  const late = items.filter(lateToPay);
  const left = items.filter((i) => leftToPay(i) > 0);
  return {
    bills: items.length,
    billed: sum((i) => i.amount),
    funded: sum((i) => i.funded),
    paid: sum((i) => i.paid),
    left: sum(leftToPay),
    notFunded: sum(notFunded),
    late: cents(late.reduce((t, i) => t + leftToPay(i), 0)),
    // what "left to pay" is made of
    retained: sum((i) => (i.cause === "authorized_hold" ? leftToPay(i) : 0)),
    twice: sum((i) => (i.duplicate_of ? leftToPay(i) : 0)),
    unexplained: cents(late.filter((i) => i.cause === "unexplained").reduce((t, i) => t + leftToPay(i), 0)),
    lateBills: late, leftBills: left,
  };
}
