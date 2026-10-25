"""The 15-day simulation on Live investigation: the page animates exactly this, so these are
the numbers it may show."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from session import DemoSession
from simulation import SCRIPT, run


@pytest.fixture(scope="module")
def frames():
    s = DemoSession()                  # the same world the page runs (100 background invoices)
    return [run(s, i) for i in range(len(SCRIPT))]


def test_every_step_runs(frames):
    assert all(f["ok"] for f in frames)
    assert [f["day"] for f in frames] == [1, 1, 2, 2, 4, 4, 7, 7, 12, 15]


def test_unexplained_money_day_by_day(frames):
    assert [f["state"]["headline"]["unexplained"] for f in frames] == [
        3140.0, 3140.0, 3140.0, 2240.0, 2240.0, 1000.0, 1900.0, 900.0, 900.0, 900.0]


def test_what_each_step_does(frames):
    def writes(i):
        return [w["type"] for w in frames[i]["writes"]["notes"]]
    assert "detect_anomalies" in frames[0]["steps"] and "alert_raised" in writes(0)   # statement mismatch
    assert "read_reply" in frames[1]["steps"] and "alert_withdrawn" not in writes(1)   # "we paid" isn't proof
    assert "alert_withdrawn" in writes(3) and "case_proposed" in writes(3)            # the moment: $3,140 -> $2,240
    assert "alert_withdrawn" in writes(5)                                             # vendor confirmation
    assert writes(6).count("chase_sent") == 2 and "alert_raised" in writes(6)         # due date passes, reminders
    assert "alert_withdrawn" in writes(7)                                             # bank export
    assert all(f["writes"]["total"] > 0 for f in frames)                              # every step writes the log


def test_reminders_are_recorded_not_emailed(frames):
    notes = [w["text"] for f in frames for w in f["writes"]["notes"] if w["type"] == "chase_sent"]
    assert notes and all("not emailed" in n for n in notes)


def _figures_agree(state):
    """The dashboard shows these as one breakdown (dashboard/lib/balances.ts): they must add up."""
    r, items = state["recon"], state["items"]
    open_ = round(sum(i["owed"] for i in items if i["owed"] > 0), 2)
    parts = round(r["owed"] + r["unfunded"] + r["held"] + r["duplicate_funded"], 2)
    unexplained = round(sum(i["owed"] for i in items if i["cause"] == "unexplained" and i["owed"] > 0), 2)
    funded = round(r["paid"] + r["held"] + r["duplicate_funded"] + r["owed"], 2)  # the Overview strip
    return (open_ == parts and state["headline"]["unexplained"] == unexplained and funded == r["funded"],
            (open_, parts, unexplained, funded, r["funded"]))


def test_open_balance_reconciles_at_the_start_and_after_every_step(frames):
    ok, got = _figures_agree(DemoSession().snapshot())
    assert ok, got
    for f in frames:
        ok, got = _figures_agree(f["state"])
        assert ok, (f["label"], got)
