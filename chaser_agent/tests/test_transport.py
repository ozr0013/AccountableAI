from datetime import date
from unittest.mock import MagicMock

from chaser_agent import connected
from chaser_agent.transport import dispatch
from contracts.models import LedgerLine
from workflow.memrails import MemoryRails


class Mirror:
    def __init__(self):
        self.rules = []
        self.actions = []

    def query(self, sql):
        return (
            self.rules if "agreement_rule" in sql else self.actions if "chase_action" in sql else []
        )


def setup(monkeypatch):
    rails = MemoryRails()
    rails.now = lambda: date(2026, 3, 12)
    line = LedgerLine(
        invoice_id="INV-KW-0301",
        vendor_id="v-vendor",
        manager_id="keyway",
        amount=100,
        invoice_received=date(2026, 3, 1),
    )
    rails.ledger = lambda manager: [line]
    for key in (
        "WORKFLOW_EMAIL_SEND",
        "SMTP_HOST",
        "SMTP_FROM",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "WORKFLOW_MANAGEMENT_EMAIL",
        "WORKFLOW_OWNER_EMAIL",
        "SMTP_STARTTLS",
        "SMTP_PORT",
    ):
        monkeypatch.delenv(key, raising=False)
    smtp = MagicMock()
    smtp.return_value.__enter__.return_value.send_message.return_value = {}
    monkeypatch.setattr("chaser_agent.transport.smtplib.SMTP", smtp)
    return rails, Mirror(), smtp


def configured(monkeypatch):
    for key, value in {
        "WORKFLOW_EMAIL_SEND": "on",
        "SMTP_HOST": "smtp.test",
        "SMTP_FROM": "finance@test.example",
        "WORKFLOW_MANAGEMENT_EMAIL": "manager@test.example",
    }.items():
        monkeypatch.setenv(key, value)


def test_disabled_and_unconfigured_never_connect(monkeypatch):
    r, m, smtp = setup(monkeypatch)
    assert dispatch(r, m)["enabled"] is False
    monkeypatch.setenv("WORKFLOW_EMAIL_SEND", "on")
    assert dispatch(r, m)["configured"] is False
    smtp.assert_not_called()


def test_sends_once_with_durable_attempt_and_receipt(monkeypatch):
    r, m, smtp = setup(monkeypatch)
    configured(monkeypatch)
    monkeypatch.setenv("SMTP_USERNAME", "user")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    result = dispatch(r, m)
    assert len(result["delivered"]) == 1
    client = smtp.return_value.__enter__.return_value
    client.starttls.assert_called_once()
    client.login.assert_called_once_with("user", "secret")
    sent = client.send_message.call_args.args[0]
    assert sent["Message-ID"] == "<" + result["delivered"][0] + "@workflow.local>"
    assert any(e.type == "workflow_delivery_attempt" for e in r.events())
    assert connected.cases(r)[0]["outbox"][0]["status"] == "delivered"
    assert dispatch(r, m)["delivered"] == []
    assert client.send_message.call_count == 1


def test_ambiguous_failure_is_never_automatically_retried(monkeypatch):
    r, m, smtp = setup(monkeypatch)
    configured(monkeypatch)
    smtp.return_value.__enter__.return_value.send_message.side_effect = TimeoutError(
        "password must not leak"
    )
    result = dispatch(r, m)
    assert result["errors"] and "password" not in str(result["errors"])
    assert connected.cases(r)[0]["outbox"][0]["status"] == "queued"
    dispatch(r, m)
    assert smtp.return_value.__enter__.return_value.send_message.call_count == 1


def test_pending_approval_and_stale_requests_are_never_sent(monkeypatch):
    r, m, smtp = setup(monkeypatch)
    configured(monkeypatch)
    connected.reconcile(r, m)
    m.rules = [
        {
            "rule_id": "r",
            "vendor_name": "Vendor",
            "rule_type": "pay_within_days",
            "status": "active",
            "params": {"days": 10},
        }
    ]
    m.actions = [
        {
            "action_id": "firm",
            "invoice_id": "INV-KW-0301",
            "action": "reminder",
            "status": "needs_approval",
            "message": "Pay invoice",
            "as_of": "2026-03-12",
        }
    ]
    assert dispatch(r, m)["delivered"] == []
    smtp.assert_not_called()
    m.actions[0]["status"] = "approved"
    assert len(dispatch(r, m)["delivered"]) == 1


def test_escalation_requires_both_recipients(monkeypatch):
    r, m, smtp = setup(monkeypatch)
    configured(monkeypatch)
    m.rules = [
        {
            "rule_id": "r",
            "vendor_name": "Vendor",
            "rule_type": "pay_within_days",
            "status": "active",
            "params": {"days": 10},
        }
    ]
    m.actions = [
        {
            "action_id": "e",
            "invoice_id": "INV-KW-0301",
            "action": "escalate",
            "status": "approved",
            "message": "Escalate invoice",
            "as_of": "2026-03-12",
        }
    ]
    assert dispatch(r, m)["configured"] is False
    smtp.assert_not_called()
    monkeypatch.setenv("WORKFLOW_OWNER_EMAIL", "owner@test.example")
    assert len(dispatch(r, m)["delivered"]) == 1
    message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
    assert "owner@test.example" in message["To"] and "manager@test.example" in message["To"]


def test_attempt_guard_survives_restart_and_tls_off_is_explicit(monkeypatch):
    r, m, smtp = setup(monkeypatch)
    configured(monkeypatch)
    monkeypatch.setenv("SMTP_STARTTLS", "off")
    smtp.return_value.__enter__.return_value.send_message.side_effect = TimeoutError()
    dispatch(r, m)
    restarted = MemoryRails(r.events())
    restarted.now = r.now
    restarted.ledger = r.ledger
    assert dispatch(restarted, m)["delivered"] == []
    assert smtp.return_value.__enter__.return_value.send_message.call_count == 1
    smtp.return_value.__enter__.return_value.starttls.assert_not_called()


def test_incomplete_auth_configuration_has_no_network_attempt(monkeypatch):
    r, m, smtp = setup(monkeypatch)
    configured(monkeypatch)
    monkeypatch.setenv("SMTP_USERNAME", "user")
    assert dispatch(r, m)["configured"] is False
    smtp.assert_not_called()
    assert not any(e.type == "workflow_delivery_attempt" for e in r.events())
