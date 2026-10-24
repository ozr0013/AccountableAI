"""The demo script, end to end. Each step must change something a judge can see.

Rules mode (no model calls) on a throwaway Postgres schema: exact and free. The live run uses
the same script with the models on.
"""

from datetime import date

import pytest

from agents import RealAgents
from contracts.fakes import FakeVerifier
from contracts.models import AlertKind
from rails import RealRails
from rails.live.system import LiveSystem
from world import generate
from world.generator import ALL_SCENARIOS


def unexplained(live) -> dict[str, float]:
    book = live._Book.load(live.rails)
    return {a.draft.invoice_ids[0]: a.draft.amount for a in book.open_alerts("keyway")
            if a.draft.kind == AlertKind.unexplained_unpaid and a.draft.amount > 0}


def chases(live) -> list[tuple[str, str]]:
    return [(e.payload["chase"]["level"], e.learned_at.date().isoformat())
            for e in live.rails.events() if e.type == "chase_sent"]


@pytest.fixture
def live(schema):
    rails = RealRails(schema=schema)
    rails.load(generate(13, ALL_SCENARIOS, 30).events())
    system = LiveSystem(rails, agents=RealAgents(None), verifier=FakeVerifier())
    system.warm()
    return system


def test_warm_start_is_autonomous_and_needs_no_human(live):
    s = live.stats()
    assert s["today"] == "2026-04-22"
    assert s["open_alerts"] == 8
    assert unexplained(live) == {"INV-KW-0301": 1240.0, "INV-KW-0901": 1000.0, "INV-KW-0402": 900.0}
    assert s["automatic_actions"] > 0 and s["human_decisions"] == 0
    # The replay already ran the real timeline: reminders went out as each item aged.
    assert chases(live)[0][0] == "polite" and {lv for lv, _ in chases(live)} <= {"polite", "firm"}
    # The scenario 7 bank-detail hold is waiting for the controller; nothing else is.
    assert [p["proposed_hold"] for p in live.pending_approvals()] == [4800.0]
    assert "nothing is emailed" in s["mode"]["reminders"]


def test_warm_start_twice_changes_nothing(live):
    before = len(live.rails.events())
    assert live.warm()["processed"] == 0
    assert len(live.rails.events()) == before


def test_the_demo_script(live):
    # 1. Oakline is about to fund Keyway again: the case agent proposes a hold of exactly $3,140.
    ev, res = live.request_funding("keyway", 18400.0)
    [pending] = [p for p in live.pending_approvals() if p["proposed_hold"] == 3140.0]
    assert pending["proposed_hold"] == 3140.0 and pending["funding_amount"] == 18400.0
    assert pending["recommendation"].startswith("Recommend holding $3,140.00 of the $18,400.00")
    assert {"doc-stmt-kw-2026-03", "doc-bank-kw-2026-03"} <= set(pending["evidence"])

    # 2. Only a controller who is not the proposer can approve. One click, one human decision.
    with pytest.raises(PermissionError):
        live.decide(pending["approval_id"], "case_agent", True)
    with pytest.raises(PermissionError):
        live.decide(pending["approval_id"], "dana", True)             # not a controller
    done = live.decide(pending["approval_id"], "controller.dana", True)
    assert done["proposed_hold"] == 3140.0 and done["approved_by"] == "controller.dana"
    assert [p["proposed_hold"] for p in live.pending_approvals()] == [4800.0]   # bank change waits
    with pytest.raises(ValueError):
        live.decide(pending["approval_id"], "controller.dana", True)   # already decided
    assert live.stats()["human_decisions"] == 1

    # 3. "We paid the electrician": no proof, so the system holds its position.
    live.inject_text("Keyway says they paid the electrician", manager_id="keyway")
    assert set(unexplained(live)) == {"INV-KW-0301", "INV-KW-0901", "INV-KW-0402"}
    reply = [e for e in live.rails.events() if e.type == "reply_read"][-1]
    assert "holding position" in reply.payload["verdict"]

    # 4. The real fact: it arrived Apr 15. Only that item is rechecked and withdrawn.
    rec, results = live.inject_preset("invoice_received_correction")
    assert rec.affected == ["INV-KW-0402"]
    assert len(results[0]["withdrawn"]) == 1
    assert unexplained(live) == {"INV-KW-0301": 1240.0, "INV-KW-0901": 1000.0}      # $3,140 -> $2,240

    # 5. The plumber's bank line shows up: that case closes by itself.
    live.inject_preset("bank_shows_payment", {"invoice_id": "INV-KW-0301", "on": "2026-04-22"})
    assert unexplained(live) == {"INV-KW-0901": 1000.0}

    # 6. Time moves deadline by deadline; nobody clicks anything.
    _, _, target, reason = live.advance_clock()
    assert (target, reason) == (date(2026, 4, 25), "INV-KW-0701 becomes overdue")
    _, _, target, reason = live.advance_clock()      # the corrected electrician deadline passes
    assert (target, reason) == (date(2026, 4, 26), "INV-KW-0402 becomes overdue")
    assert unexplained(live)["INV-KW-0402"] == 900.0           # still unpaid, so it is back
    _, _, target, reason = live.advance_clock()
    assert target == date(2026, 4, 27) and "escalation reminder" in reason
    assert ("escalation", "2026-04-27") in chases(live)
    assert set(unexplained(live)) == {"INV-KW-0901", "INV-KW-0402"}

    # Everything above needed exactly one human decision, and nothing is dated after "today".
    s = live.stats()
    assert s["human_decisions"] == 1 and s["pending_approvals"] == 1     # the bank change waits
    assert s["today"] == "2026-04-27"
    assert max(e.learned_at.date() for e in live.rails.events()) <= date(2026, 4, 27)
    texts = [i["text"] for i in live.feed(200)]
    assert any("approved the hold of $3,140.00" in t for t in texts)
    assert any("Alert withdrawn" in t for t in texts)
    assert any("Escalation reminder issued" in t and "not emailed" in t for t in texts)
    assert any("Time moved to 2026-04-27" in t for t in texts)


def test_a_deadline_that_passes_brings_the_alert_back(live):
    """Advance past Apr 25 without the Apr 15 fact: the electrician is overdue again."""
    live.inject_preset("invoice_received_correction")
    assert "INV-KW-0402" not in unexplained(live)
    live.advance_clock(to=date(2026, 4, 26))                       # due Apr 25, still unpaid
    assert unexplained(live)["INV-KW-0402"] == 900.0


def test_time_only_moves_forward(live):
    with pytest.raises(ValueError):
        live.advance_clock(to=date(2026, 4, 20))


def test_the_same_clock_move_twice_is_refused_not_repeated(live):
    live.advance_clock(to=date(2026, 4, 24))
    n = len(live.rails.events())
    with pytest.raises(ValueError):
        live.advance_clock(to=date(2026, 4, 24))
    assert len(live.rails.events()) == n


def test_with_nothing_due_the_default_is_the_next_day(live):
    for _ in range(4):
        live.advance_clock()
    _, _, target, reason = live.advance_clock()
    assert reason == "next day" and target == live.rails.now()


def test_feed_and_counters_do_not_count_refreshes_as_new_actions(live):
    """Moving the clock re-raises open alerts because days past due changed. Those are updates."""
    before = live.stats()
    live.advance_clock()
    live.advance_clock()
    after = live.stats()
    feed = live.feed(300)
    updated = [i for i in feed if i["text"].startswith("Alert updated")]
    assert updated and after["alerts_updated"] == len(updated) > 0
    raised_ids = [i for i in feed if i["text"].startswith("Alert raised")]
    new_raised = sum(1 for i in raised_ids if i["at"] > "2026-04-23")
    # only a genuinely new alert (the electrician going overdue) counts as raised
    assert new_raised <= 1
    delta_auto = after["automatic_actions"] - before["automatic_actions"]
    assert delta_auto < len(updated) + 10            # refreshes are not in the headline number
