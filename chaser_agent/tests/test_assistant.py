"""The chaser assistant: Claude calls read-only tools, the answer carries its tool trace."""
from datetime import date
from types import SimpleNamespace as NS

from chaser_agent import assistant
from chaser_agent.assistant import DataTools, ask
from chaser_agent.patterns import vendor_patterns
from contracts.models import LedgerLine

TODAY = date(2026, 4, 22)


def _line(inv, vendor, received, paid_on=None, amount=100.0):
    return LedgerLine(invoice_id=inv, manager_id="keyway", vendor_id=vendor, amount=amount,
                      invoice_received=received, paid_amount=amount if paid_on else 0.0,
                      paid_on=paid_on, due_date=None)


class Rails:
    def __init__(self, lines):
        self.lines = lines

    def documents(self, manager_id=None):
        return []

    def ledger(self, manager_id):
        return self.lines

    def events(self, manager_id=None):
        return [NS(type="entity_registered", payload={"id": "v-late", "name": "Late Co"}),
                NS(type="entity_registered", payload={"id": "v-good", "name": "Good Co"})]


RULE = lambda vendor: {"rule_id": f"{vendor}:pay_within_days", "rule_type": "pay_within_days",
                       "status": "active", "vendor_name": vendor, "params": '{"days": 10}',
                       "check": "Pay within 10 days", "source_quote": "within 10 days"}


class Mirror:
    def query(self, sql):
        return [RULE("Late Co LLC"), RULE("Good Co")] if "agreement_rule" in sql else []


LINES = [_line(f"INV-KW-00{i}", "v-late", date(2026, 1, i), date(2026, 1, i + 20)) for i in (1, 2, 3)]
LINES += [_line("INV-KW-0099", "v-good", date(2026, 1, 5), date(2026, 1, 9)),
          _line("INV-KW-0100", "v-late", date(2026, 4, 1))]


def _tools():
    return DataTools(Rails(LINES), Mirror(), list, TODAY)


def test_lateness_is_measured_against_the_agreement_deadline():
    rows = {r["vendor"]: r for r in vendor_patterns(Rails(LINES), Mirror().query("agreement_rule"), TODAY)}
    late = rows["Late Co"]
    assert late["paid"] == 3 and late["late"] == 3 and late["avg_days_late"] == 10.0
    assert late["chronic"] and late["deadline_source"] == "agreement"
    assert late["overdue_unpaid"][0]["invoice_id"] == "INV-KW-0100"
    assert rows["Good Co"]["late"] == 0 and not rows["Good Co"]["chronic"]


def test_tools_read_and_reject_unknown_calls():
    t = _tools()
    assert t.call("list_invoices", {"status": "overdue"})["invoices"][0]["invoice_id"] == "INV-KW-0100"
    assert t.call("vendor_lateness", {"vendor": "late"})[0]["chronic"]
    assert "error" in t.call("send_reminder", {})
    assert "error" in t.call("get_invoice", {"invoice_id": "INV-XX-1"})
    assert "error" in t.call("list_invoices", {"bogus": 1})


class ScriptedClaude:
    """Calls vendor_lateness, then answers."""

    def __init__(self):
        self.beta = NS(messages=self)
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        usage = NS(input_tokens=10, output_tokens=5)
        if len(self.calls) == 1:
            return NS(stop_reason="tool_use", usage=usage, model="scripted", content=[
                NS(type="thinking", thinking="Need lateness per vendor."),
                NS(type="tool_use", id="t1", name="vendor_lateness", input={})])
        return NS(stop_reason="end_turn", usage=usage, model="scripted",
                  content=[NS(type="text", text="Late Co: 3 of 3 payments late (avg 10 days).")])


def test_ask_runs_the_tool_loop_and_returns_the_trace():
    claude = ScriptedClaude()
    out = ask("Which vendor is paid late most often?", _tools(), client=claude)
    assert out["answer"].startswith("Late Co") and out["thinking"] == ["Need lateness per vendor."]
    assert [t["tool"] for t in out["tools"]] == ["vendor_lateness"]
    second = claude.calls[1]["messages"]
    results = next(m for m in second if m["role"] == "user" and isinstance(m["content"], list))
    assert results["content"][0]["tool_use_id"] == "t1"
    assert claude.calls[0]["model"] == assistant.MODEL and claude.calls[0]["fallbacks"] == "default"
    assert all("send" not in t["name"] and "approve" not in t["name"] for t in assistant.TOOLS)
