"""Case by rules: the hold amount is arithmetic, never a model's guess."""

from __future__ import annotations

import hashlib

from contracts.models import Alert, AlertKind, Case, Citation

HOLD_KINDS = {AlertKind.unexplained_unpaid}


def money(x: float) -> str:
    return f"${x:,.2f}"


def holdable(alerts: list[Alert]) -> list[Alert]:
    """Open, unexplained, still-unpaid items: the only money a hold can cover."""
    return [a for a in alerts if a.state == "open" and a.draft.kind in HOLD_KINDS
            and a.draft.amount > 0]


def proposed_hold(alerts: list[Alert]) -> float:
    return round(sum(a.draft.amount for a in holdable(alerts)), 2)


def evidence(alerts: list[Alert]) -> list[Citation]:
    seen: dict[str, Citation] = {}
    for a in alerts:
        for cl in a.draft.claims:
            for c in cl.citations:
                seen.setdefault(c.source_id, c)
    return list(seen.values())


def case_id(manager_id: str, alerts: list[Alert]) -> str:
    key = "|".join([manager_id, *sorted(a.draft.id for a in alerts)])
    return f"case-{manager_id}-{hashlib.sha1(key.encode()).hexdigest()[:10]}"


def summary(manager_id: str, alerts: list[Alert]) -> str:
    items = holdable(alerts)
    hold = proposed_hold(alerts)
    if not items:
        late = [a for a in alerts if a.draft.kind == AlertKind.unexplained_unpaid]
        return (f"No unexplained unpaid vendor money at {manager_id}"
                + (f"; {len(late)} late payment(s) on record." if late else ".")
                + " No hold recommended.")
    refs = ", ".join(i for a in items for i in a.draft.invoice_ids)
    return (f"{manager_id} still owes {len(items)} vendor invoice(s) totalling {money(hold)} "
            f"from money Oakline already funded ({refs}), with no bank record or vendor "
            f"confirmation of payment. Recommend holding {money(hold)} of the next funding "
            "until payment is shown.")


def build_case(manager_id: str, alerts: list[Alert]) -> Case:
    items = holdable(alerts)
    return Case(id=case_id(manager_id, alerts), manager_id=manager_id,
                alerts=[a.draft.id for a in items], proposed_hold=proposed_hold(alerts),
                summary=summary(manager_id, alerts), evidence=evidence(items))
