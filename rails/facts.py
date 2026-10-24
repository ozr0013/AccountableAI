"""Facts: the unit that selective recheck is built on.

A fact key names one thing an item depends on, e.g. `invoice:INV-1.received` or
`terms:keyway.vendor_payment_days`. Events set or change facts; items (invoices) depend on them.

`fact_corrected` events overlay a new value onto the original document data (history is never
edited; the latest `learned_at` wins). Pass events already filtered by the caller for
`known_at` / `current_only` and the overlay respects that automatically.

fact_corrected payload:  {"fact_key": "invoice:INV-1.received", "value": "2026-04-01"}
chase_reply payload:     contracts.models.ChaseReply.model_dump(mode="json")
"""

from __future__ import annotations

from contracts.models import Event

INVOICE_ATTRS = ("received", "amount", "inspection", "service_period", "vendor_ref", "funding",
                 "payment")
TERMS_ATTRS = ("vendor_payment_days", "holdback_pct")


def effective(events: list[Event]) -> list[Event]:
    """Events with fact corrections applied to invoice documents and confirmed terms."""
    patches: dict[str, object] = {}
    for e in sorted((e for e in events if e.type == "fact_corrected"), key=lambda e: e.learned_at):
        patches[e.payload["fact_key"]] = e.payload["value"]
    if not patches:
        return events
    out = []
    for e in events:
        if e.type == "document_ingested" and e.payload.get("kind") == "vendor_invoice":
            iid = e.payload.get("data", {}).get("invoice_id")
            attrs = {k.split(".", 1)[1]: v for k, v in patches.items()
                     if k.startswith(f"invoice:{iid}.")}
            if attrs:
                data = {**e.payload["data"], **attrs}
                e = e.model_copy(update={"payload": {**e.payload, "data": data}})
        elif e.type == "terms_confirmed":
            attrs = {k.split(".", 1)[1]: v for k, v in patches.items()
                     if k.startswith(f"terms:{e.manager_id}.")}
            if attrs:
                e = e.model_copy(update={"payload": {**e.payload, **attrs}})
        out.append(e)
    return out


def current_terms(events: list[Event]) -> dict[str, dict]:
    """manager_id -> confirmed terms payload (corrections applied)."""
    return {e.manager_id: e.payload for e in effective(events)
            if e.type == "terms_confirmed" and e.manager_id}


def fact_keys(e: Event) -> set[str]:
    """Fact keys this event sets or changes (exact invoice ids only; see DependencyIndex.rebuild
    for payments that the matcher ties to an invoice)."""
    d = e.payload.get("data", {})
    if e.type == "fact_corrected":
        return {e.payload["fact_key"]}
    if e.type == "terms_confirmed":
        return {f"terms:{e.manager_id}.{a}" for a in TERMS_ATTRS}
    if e.type == "chase_reply":
        ids = e.payload.get("invoice_ids") or ([e.invoice_id] if e.invoice_id else [])
        return {f"invoice:{i}.payment" for i in ids}
    if e.type != "document_ingested":
        return set()
    kind, iid = e.payload.get("kind"), d.get("invoice_id")
    if kind == "vendor_invoice" and iid:
        return {f"invoice:{iid}.{a}" for a in ("received", "amount", "inspection",
                                                  "service_period", "vendor_ref")}
    if kind == "funding" and iid:
        return {f"invoice:{iid}.funding"}
    if kind == "manager_bank":
        return {f"invoice:{p['invoice_id']}.payment" for p in d.get("payments", [])
                if p.get("invoice_id")}
    if kind == "manager_statement":
        return {f"invoice:{ln['invoice_id']}.payment" for ln in d.get("lines", [])
                if ln.get("invoice_id")}
    if kind in ("vendor_confirmation", "vendor_notice", "manager_reply") and iid:
        return {f"invoice:{iid}.payment"}
    return set()
