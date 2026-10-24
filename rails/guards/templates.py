"""Controller-approved reminder templates. A reminder to a middleman can only be one of these.

Ids match the stand-in chaser in contracts/fakes (`tpl-<level>`).
"""

from __future__ import annotations

import string

from .wording import banned_terms_in

APPROVED_TEMPLATES: dict[str, dict] = {
    "tpl-polite": {
        "level": "polite",
        "body": ("Hello {manager_name}, a friendly reminder: Oakline Homes funded {amount} for "
                 "{vendor_name} (invoice {invoice_ref}) and we have not yet seen the vendor payment "
                 "confirmed. Could you share the payment details or the vendor's confirmation?"),
    },
    "tpl-firm": {
        "level": "firm",
        "body": ("Hello {manager_name}, following up on our earlier note about {amount} funded for "
                 "{vendor_name} (invoice {invoice_ref}). We still have no bank record or vendor "
                 "confirmation of payment. Please send one, or tell us what is holding it up."),
    },
    "tpl-escalation": {
        "level": "escalation",
        "body": ("Hello {manager_name}, this item is still unexplained: {amount} funded for "
                 "{vendor_name} (invoice {invoice_ref}) with no bank record or vendor confirmation. "
                 "Oakline's controller has been notified and may hold further funding until it is "
                 "resolved. Please reply with payment proof."),
    },
}
# Not a reminder: a request that goes out only after the controller approves that exact draft.
DOCUMENT_REQUEST_TEMPLATE = "doc-request-v1"

# Not a reminder either: the anomaly detector's note to the middleman when a statement line
# doesn't match what Oakline approved. Drafted by code; sent only once a controller approves it.
STATEMENT_NOTICE_TEMPLATE = "stmt-notice-v1"
STATEMENT_NOTICE_BODY = (
    "Hello {manager_name}, your statement {statement_ref} shows {stated} for {invoice_ref}, but "
    "Oakline Homes approved {approved} for that invoice. Oakline's controller has been notified. "
    "Please check the line and send a corrected statement or the vendor's confirmation of the "
    "amount paid.")


def render_statement_notice(**fields) -> str:
    return STATEMENT_NOTICE_BODY.format(**fields)

# Approved schedule: first reminder any time, then at least this many days between levels.
LEVEL_ORDER = ("polite", "firm", "escalation")
MIN_DAYS_BETWEEN = 5


def render(template_id: str, **fields) -> str:
    return APPROVED_TEMPLATES[template_id]["body"].format(**fields)


def _check_templates() -> None:
    for tid, t in APPROVED_TEMPLATES.items():
        assert not banned_terms_in(t["body"]), tid
        names = {n for _, n, _, _ in string.Formatter().parse(t["body"]) if n}
        assert names == {"manager_name", "amount", "vendor_name", "invoice_ref"}, tid


_check_templates()
assert not banned_terms_in(STATEMENT_NOTICE_BODY)
