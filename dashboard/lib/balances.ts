import type { State } from "./types";

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
