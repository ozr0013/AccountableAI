"""Optional SMTP transport with durable at-most-one automatic delivery attempt.

The coordinator must hold its mutation lock when calling dispatch. An interrupted or failed
attempt remains queued for human investigation; a deterministic Message-ID is not a promise
that an SMTP server deduplicates mail. No attempted ID is automatically retried.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage

from . import connected


def _recipients(row):
    audience = row.get("audience", "")
    if row["kind"] == "escalation":
        keys = ["WORKFLOW_MANAGEMENT_EMAIL", "WORKFLOW_OWNER_EMAIL"]
    elif audience.startswith("management:"):
        keys = ["WORKFLOW_MANAGEMENT_EMAIL"]
    elif audience.startswith("owner:"):
        keys = ["WORKFLOW_OWNER_EMAIL"]
    else:
        return None
    addresses = [os.getenv(key, "").strip() for key in keys]
    if not all(addresses) or any("\n" in a or "\r" in a for a in addresses):
        return None
    return list(dict.fromkeys(addresses))


def dispatch(rails, mirror, manager="keyway"):
    """Deliver current approved outbox actions only when explicitly configured.

    Returns enabled/configured booleans, delivered outbox IDs, and sanitized errors.
    SMTP credentials, message bodies and recipient addresses are never placed in errors.
    """
    enabled = os.getenv("WORKFLOW_EMAIL_SEND", "off").lower() == "on"
    result = {"enabled": enabled, "configured": False, "delivered": [], "errors": []}
    if not enabled:
        return result
    host = os.getenv("SMTP_HOST", "").strip()
    sender = os.getenv("SMTP_FROM", "").strip()
    username = os.getenv("SMTP_USERNAME", "")
    password = os.getenv("SMTP_PASSWORD", "")
    try:
        port = int(os.getenv("SMTP_PORT", "587"))
        if not 1 <= port <= 65535:
            raise ValueError("invalid port")
    except ValueError:
        result["errors"].append({"outbox_id": None, "error": "smtp_configuration_invalid"})
        return result
    if (
        not host
        or not sender
        or "\n" in sender
        or "\r" in sender
        or bool(username) != bool(password)
    ):
        result["errors"].append({"outbox_id": None, "error": "smtp_not_configured"})
        return result
    current = connected.reconcile(rails, mirror, manager)
    result["configured"] = True
    attempted = {
        e.payload.get("outbox_id")
        for e in rails.events(manager)
        if e.type == "workflow_delivery_attempt"
    }
    for case in current:
        for row in case["outbox"]:
            ident = row["id"]
            if row["status"] != "queued":
                continue
            if ident in attempted:
                result["errors"].append(
                    {"outbox_id": ident, "error": "prior_attempt_requires_human_receipt"}
                )
                continue
            recipients = _recipients(row)
            if recipients is None:
                result["configured"] = False
                result["errors"].append({"outbox_id": ident, "error": "recipient_not_configured"})
                continue
            message_id = "<" + ident + "@workflow.local>"
            connected.record(
                rails,
                row["invoice_id"],
                "workflow_delivery_attempt",
                {"outbox_id": ident, "message_id": message_id, "status": "attempting"},
                manager=manager,
            )
            attempted.add(ident)
            try:
                message = EmailMessage()
                message["From"] = sender
                message["To"] = ", ".join(recipients)
                message["Subject"] = f"Finance workflow: {row['invoice_id']} — {row['kind']}"
                message["Message-ID"] = message_id
                message.set_content(row["body"])
                with smtplib.SMTP(host, port, timeout=30) as client:
                    if os.getenv("SMTP_STARTTLS", "on").lower() != "off":
                        client.starttls(context=ssl.create_default_context())
                    if username:
                        client.login(username, password)
                    refused = client.send_message(message)
                    if refused:
                        raise smtplib.SMTPException("one or more recipients refused")
                connected.acknowledge(rails, mirror, ident, message_id, "smtp", manager)
                connected.record(
                    rails,
                    row["invoice_id"],
                    "workflow_delivery_result",
                    {"outbox_id": ident, "message_id": message_id, "status": "delivered"},
                    manager=manager,
                )
                result["delivered"].append(ident)
            except Exception as exc:  # noqa: BLE001 - ambiguous transport outcomes require human review
                error = type(exc).__name__
                connected.record(
                    rails,
                    row["invoice_id"],
                    "workflow_delivery_result",
                    {
                        "outbox_id": ident,
                        "message_id": message_id,
                        "status": "uncertain",
                        "error": error,
                    },
                    manager=manager,
                )
                result["errors"].append({"outbox_id": ident, "error": error})
    return result
