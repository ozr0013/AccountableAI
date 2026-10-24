"""Code checks the workflow runs between agents: trail, cause, hold, wording and briefing."""
import pytest

from agents import RealAgents
from agents.testing import invoice, world_rails
from contracts.fakes import FakeAgents, FakeApprovals, FakeRails, FakeVerifier
from contracts.models import (
    Alert,
    AlertDraft,
    AlertKind,
    Cause,
    Citation,
    Claim,
    SourceKind,
    Verdict,
    VerdictStatus,
)
from workflow import Deps, Workflow
from workflow.checks.briefing import CodeBriefer, Record, code_briefing
from workflow.checks.cause import classify
from workflow.checks.hold import build_case
from workflow.checks.trail import build_trail
from workflow.checks.wording import RulesVerifier, find_banned
from workflow.memrails import MemoryRails
from workflow.run import scan_events
from workflow.terms import terms_for
from world import generate
from world.generator import ALL_SCENARIOS

S3 = ("INV-KW-0301", "keyway")


def _ids(trail):
    return {e.citation.source_id for e in trail.entries}


# --- payment trail ---------------------------------------------------------------------

def test_trail_scenario_3_bank_and_vendor_beat_statement():
    t = build_trail(*invoice(*S3))
    assert t.conflict and t.winning_claim == "unpaid"
    assert {c.source_id for c in t.winning_sources} >= {"doc-bank-kw-2026-03",
                                                        "doc-notice-riogrande-0420"}
    assert {"doc-stmt-kw-2026-03", "doc-bank-kw-2026-03", "doc-notice-riogrande-0420"} <= _ids(t)


def test_trail_scenario_9_paid_reply_is_not_proof():
    t = build_trail(*invoice("INV-KW-0901", "keyway"))
    assert t.winning_claim == "unpaid" and t.conflict and "doc-reply-kw-0901" in _ids(t)


def test_trail_paid_invoice_wins_paid():
    t = build_trail(*invoice("INV-KW-0501", "keyway"))
    assert t.winning_claim == "paid" and not t.conflict


# --- delay cause -----------------------------------------------------------------------

def _cause(invoice_id):
    line, docs = invoice(invoice_id, "keyway")
    return classify(line, build_trail(line, docs), terms_for(world_rails(), FakeAgents(), "keyway"))


@pytest.mark.parametrize("inv,cause", [
    ("INV-KW-0101", Cause.authorized_hold),
    ("INV-KW-0201", Cause.paperwork_pending),
    ("INV-KW-0301", Cause.unexplained),
    ("INV-KW-0601", Cause.owner_side),
    ("INV-KW-0901", Cause.unexplained),
    ("INV-KW-0402", Cause.unexplained),
])
def test_cause_per_scenario(inv, cause):
    f = _cause(inv)
    assert f.cause == cause and f.citations and 0 < f.confidence <= 1


def test_cause_paid_within_the_window_is_vendor_side():
    f = _cause("INV-KW-0501")
    assert f.cause == Cause.vendor_side and "2026-04-11" in f.explanation


def test_cause_cites_the_approval_limit_clause():
    [limit] = [c for c in _cause("INV-KW-0601").citations if c.kind == "agreement"]
    assert limit.locator == "8.1"


# --- hold arithmetic -------------------------------------------------------------------

def _alert(inv, amount, kind=AlertKind.unexplained_unpaid, state="open"):
    cite = Citation(source_id=f"doc-{inv}", kind=SourceKind.manager_bank)
    d = AlertDraft(id=f"al-{inv}", kind=kind, manager_id="keyway", invoice_ids=[inv],
                   amount=amount, headline=f"{inv} unpaid",
                   claims=[Claim(text="unpaid", citations=[cite])])
    return Alert(draft=d, verdict=Verdict(alert_id=d.id, status=VerdictStatus.approved,
                                          reasons=[]), state=state)


ALERTS = [_alert("INV-1", 1240.0), _alert("INV-2", 1450.0), _alert("INV-3", 450.0),
          _alert("INV-4", 0.0), _alert("INV-5", 650.0, AlertKind.paperwork_request),
          _alert("INV-6", 999.0, state="withdrawn")]


def test_hold_is_the_sum_still_owed_on_open_unexplained_items():
    c = build_case("keyway", ALERTS)
    assert c.proposed_hold == 3140.00 and c.alerts == ["al-INV-1", "al-INV-2", "al-INV-3"]
    assert "$3,140.00" in c.summary and c.approved_by is None


def test_no_unexplained_money_no_hold():
    assert build_case("keyway", ALERTS[3:]).proposed_hold == 0


# --- wording check ---------------------------------------------------------------------

DOCS = FakeRails().documents("keyway")


def _draft(**kw):
    base = {"id": "a3", "kind": AlertKind.unexplained_unpaid, "manager_id": "keyway",
                "invoice_ids": ["INV-KW-0301"], "amount": 1240.0, "headline": "Rio Grande still unpaid",
                "claims": [Claim(text="Rio Grande reports the invoice unpaid as of 04/20",
                              citations=[Citation(source_id="doc-notice-riogrande-0420",
                                                  kind=SourceKind.vendor_notice,
                                                  quote="remains unpaid as of 04/20")])]}
    return AlertDraft(**{**base, **kw})


def test_wording_check_passes_a_cited_neutral_alert():
    assert RulesVerifier().verify(_draft(), DOCS).status == VerdictStatus.approved


@pytest.mark.parametrize("change, reason", [
    ({"claims": [Claim(text="Keyway kept the money", citations=[])]}, None),
    ({"headline": "Possible fraud at Keyway"}, None),
    ({"claims": [Claim(text="x", citations=[Citation(source_id="doc-missing",
                                                     kind=SourceKind.vendor_notice)])]},
     "unknown source"),
    ({"claims": [Claim(text="x", citations=[Citation(source_id="doc-notice-riogrande-0420",
                                                     kind=SourceKind.vendor_notice,
                                                     quote="Keyway refused to pay")])]},
     "quote not found"),
])
def test_wording_check_blocks_bad_alerts(change, reason):
    v = RulesVerifier().verify(_draft(**change), DOCS)
    assert v.status == VerdictStatus.blocked
    assert reason is None or any(reason in r for r in v.reasons)


@pytest.mark.parametrize("text,hit", [
    ("Possible fraud at Keyway", ["fraud"]), ("funds were misused", ["misuse"]),
    ("Stealth Roofing invoice", []), ("the dog scampered off", []),
])
def test_banned_words_match_whole_words(text, hit):
    assert find_banned(text) == hit


# --- code briefing ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def replayed():
    w = generate(13, ALL_SCENARIOS, 30)
    r = MemoryRails()
    Workflow(Deps(r, RealAgents(None), FakeVerifier(), FakeApprovals())).replay(
        w.events() + scan_events([m.id for m in w.managers], w.as_of))
    return r


def test_code_briefing_states_the_moment_total_and_cites_everything(replayed):
    b = code_briefing(Record(replayed, "keyway"), "What's open with Keyway?")
    assert "3 unexplained" in b.answer and "$3,140.00" in b.answer
    assert b.claims and all(c.citations for c in b.claims)
    assert any("withdrawn" in r for r in b.prior_rulings)


def test_code_briefer_reports_its_mode(replayed):
    briefer = CodeBriefer(replayed)
    briefer.brief("keyway", "What's open?")
    assert briefer.last == {"mode": "code"}
