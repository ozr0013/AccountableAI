"""Verifier tests. Offline by default (stub client); the live test runs only with an API key."""

import os
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from agents.verifier.verifier import LLMVerifier, _ClaimCheck, _CounterEvidence, _Refutation
from contracts.fakes import FakeRails
from contracts.models import AlertDraft, AlertKind, Citation, Claim, SourceKind, VerdictStatus

DOCS = FakeRails().documents("keyway")


def _cite(doc_id, kind, quote=None):
    return Citation(source_id=doc_id, kind=kind, quote=quote)


def scenario_3_draft(**kw) -> AlertDraft:
    base = dict(
        id="a3", kind=AlertKind.unexplained_unpaid, manager_id="keyway",
        invoice_ids=["INV-KW-0301"], amount=1240.0,
        headline="Rio Grande Plumbing still unpaid for $1,240 (INV-KW-0301)",
        claims=[
            Claim(text="Keyway's March statement lists INV-KW-0301 as paid on 03/10",
                  citations=[_cite("doc-stmt-kw-2026-03", SourceKind.manager_statement)]),
            Claim(text="Keyway's bank records show no payment to Rio Grande Plumbing",
                  citations=[_cite("doc-bank-kw-2026-03", SourceKind.manager_bank)]),
            Claim(text="Rio Grande Plumbing reports the invoice unpaid as of 04/20",
                  citations=[_cite("doc-notice-riogrande-0420", SourceKind.vendor_notice,
                                   quote="remains unpaid as of 04/20")]),
        ],
        recommended_action="Chase Keyway for proof of payment; consider holding $1,240.",
    )
    base.update(kw)
    return AlertDraft(**base)


class StubClient:
    """Mimics client.beta.messages.parse and records the call."""

    def __init__(self, output=None, stop_reason="end_turn", error=None):
        self.output, self.stop_reason, self.error, self.calls = output, stop_reason, error, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(
            model=kwargs["model"], stop_reason=self.stop_reason, parsed_output=self.output,
            usage=SimpleNamespace(input_tokens=1200, output_tokens=300),
        )


def _all_supported(n=3):
    return [_ClaimCheck(index=i, supported=True, why="matches source") for i in range(n)]


# --- rules: blocked before any model call --------------------------------------------------

def test_uncited_claim_blocked_without_model_call():
    client = StubClient()
    draft = scenario_3_draft(claims=[Claim(text="Keyway kept the money", citations=[])])
    v = LLMVerifier(client=client, model="claude-haiku-5-5").verify(draft, DOCS)
    assert v.status == VerdictStatus.blocked
    assert v.uncited_claims == ["Keyway kept the money"]
    assert client.calls == []


def test_accusatory_wording_blocked():
    v = LLMVerifier(client=StubClient(), model="x").verify(
        scenario_3_draft(headline="Possible fraud at Keyway"), DOCS)
    assert v.status == VerdictStatus.blocked and v.banned_terms == ["fraud"]


def test_unknown_source_blocked():
    draft = scenario_3_draft(claims=[Claim(text="Vendor confirmed non-payment",
                                           citations=[_cite("doc-does-not-exist", SourceKind.vendor_confirmation)])])
    v = LLMVerifier(client=StubClient(), model="x").verify(draft, DOCS)
    assert v.status == VerdictStatus.blocked
    assert any("unknown source" in r for r in v.reasons)


def test_fabricated_quote_blocked():
    draft = scenario_3_draft(claims=[Claim(text="Vendor says unpaid", citations=[
        _cite("doc-notice-riogrande-0420", SourceKind.vendor_notice, quote="Keyway refused to pay")])])
    v = LLMVerifier(client=StubClient(), model="x").verify(draft, DOCS)
    assert v.status == VerdictStatus.blocked
    assert any("quote not found" in r for r in v.reasons)


# --- model stage ---------------------------------------------------------------------------

def test_scenario_3_survives_refutation():
    out = _Refutation(claim_checks=_all_supported(), refuted=False,
                      refutation="Bank and vendor both show no payment; statement is outranked.",
                      counter_evidence=[])
    client = StubClient(out)
    v = LLMVerifier(client=client, model="claude-haiku-5-5").verify(scenario_3_draft(), DOCS)
    assert v.status == VerdictStatus.approved
    assert v.meta["input_tokens"] == 1200 and "latency_ms" in v.meta
    assert "fallbacks" not in client.calls[0]  # Haiku: no server-side fallback


def test_refuted_with_valid_counter_evidence():
    out = _Refutation(claim_checks=_all_supported(), refuted=True,
                      refutation="Agreement 7.2 gives 10 days from receipt; not yet due.",
                      counter_evidence=[_CounterEvidence(source_id="doc-agreement-keyway",
                                                         locator="7.2", quote="within ten (10) days")])
    v = LLMVerifier(client=StubClient(out), model="x").verify(scenario_3_draft(), DOCS)
    assert v.status == VerdictStatus.refuted
    assert v.counter_evidence[0].kind == SourceKind.agreement


def test_refutation_without_known_source_is_ignored():
    out = _Refutation(claim_checks=_all_supported(), refuted=True, refutation="Probably paid.",
                      counter_evidence=[_CounterEvidence(source_id="doc-made-up", locator="", quote="")])
    v = LLMVerifier(client=StubClient(out), model="x").verify(scenario_3_draft(), DOCS)
    assert v.status == VerdictStatus.approved
    assert any("refutation ignored" in r for r in v.reasons)


def test_unsupported_claim_blocked():
    checks = _all_supported()
    checks[1] = _ClaimCheck(index=1, supported=False, why="bank doc does not cover this period")
    out = _Refutation(claim_checks=checks, refuted=False, refutation="n/a", counter_evidence=[])
    v = LLMVerifier(client=StubClient(out), model="x").verify(scenario_3_draft(), DOCS)
    assert v.status == VerdictStatus.blocked
    assert v.uncited_claims == [scenario_3_draft().claims[1].text]


def test_api_error_fails_closed():
    err = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))
    v = LLMVerifier(client=StubClient(error=err), model="x").verify(scenario_3_draft(), DOCS)
    assert v.status == VerdictStatus.blocked
    assert any("failing closed" in r for r in v.reasons)


def test_refusal_fails_closed():
    v = LLMVerifier(client=StubClient(None, stop_reason="refusal"), model="x").verify(
        scenario_3_draft(), DOCS)
    assert v.status == VerdictStatus.blocked


def test_opus_gets_server_side_fallback():
    out = _Refutation(claim_checks=_all_supported(), refuted=False, refutation="ok", counter_evidence=[])
    client = StubClient(out)
    LLMVerifier(client=client, model="claude-opus-5-5").verify(scenario_3_draft(), DOCS)
    assert client.calls[0]["fallbacks"] == "default"


def test_registry_loads_real_verifier(monkeypatch):
    from contracts import registry
    monkeypatch.setenv("VERIFIER", "real")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    assert isinstance(registry.verifier(), LLMVerifier)


# --- live (costs money; only with a key) ----------------------------------------------------

@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY") or os.getenv("LIVE") != "1",
                    reason="set LIVE=1 and ANTHROPIC_API_KEY to run against the API")
def test_live_scenario_3():
    v = LLMVerifier().verify(scenario_3_draft(), DOCS)
    print(v.model_dump_json(indent=2))
    assert v.status == VerdictStatus.approved
