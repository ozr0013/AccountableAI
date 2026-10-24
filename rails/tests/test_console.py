from datetime import date

import pytest
from fastapi.testclient import TestClient

from rails import RealRails
from rails.console import ConsoleError, JudgeConsole
from rails.console import api as console_api
from rails.ledger import build_ledger
from world import generate


@pytest.fixture
def con(schema):
    r = RealRails(schema=schema)
    r.load(generate(scenarios=(3,), n_clean=30, moment=True).events())
    return JudgeConsole(r)


def due(con, iid):
    return {l.invoice_id: l for l in build_ledger(con.rails.store.events(current_only=True))}[iid].due_date


def test_moment_preset_supersedes_the_recorded_date_and_selects_only_that_item(con):
    from world.generator import MOMENT_RECORDED
    assert due(con, "INV-KW-0402") == date(2026, 4, 7)            # Mar 28 per the statement
    r = con.apply_preset("invoice_received_correction")           # defaults = the demo moment
    assert r.affected == ["INV-KW-0402"]
    assert due(con, "INV-KW-0402") == date(2026, 4, 25)
    assert due(con, "INV-KW-0301") == date(2026, 3, 13)           # the plumber alert is untouched
    ev = r.events[0]
    assert ev.type == "fact_corrected" and ev.supersedes == MOMENT_RECORDED
    assert ev.learned_at.date() == con.rails.now() == date(2026, 4, 22)
    assert ev.valid_from.date() == date(2026, 4, 15)
    assert "$900.00" in r.summary and "not Mar 28" in r.summary and "Apr 25" in r.summary


def test_same_click_twice_is_a_noop_and_new_value_supersedes(con):
    a = con.apply_preset("invoice_received_correction")
    b = con.apply_preset("invoice_received_correction")
    assert a.event_ids == b.event_ids and len(con.rails.store.events(type="fact_corrected")) == 1
    c = con.apply_preset("invoice_received_correction", {"date": "2026-04-16"})
    assert c.events[0].supersedes == a.events[0].id
    assert due(con, "INV-KW-0402") == date(2026, 4, 26)           # latest wins; history kept
    assert len(con.rails.store.events(type="fact_corrected")) == 2


def test_free_text_moment(con):
    r = con.apply_text("The electrician's invoice only arrived Apr 15", manager_id="keyway")
    assert r.affected == ["INV-KW-0402"] and due(con, "INV-KW-0402") == date(2026, 4, 25)


def test_free_text_names_manager_in_sentence(con):
    r = con.apply_text("Keyway says the electrician invoice arrived on April 15")
    assert r.affected == ["INV-KW-0402"]


def test_manager_reply_paid_keeps_open_item(con):               # scenario 9
    r = con.apply_text("Keyway says they paid the plumber")
    assert r.affected == ["INV-KW-0301"]
    docs = con.rails.documents("keyway", "INV-KW-0301")
    assert any(d.kind == "manager_reply" for d in docs)
    from rails.trust import decide
    from rails.matcher import gather_evidence
    d = decide(gather_evidence(docs).evidence["INV-KW-0301"])
    assert d.claim == "unpaid"            # a reply is not proof


def test_vendor_confirmation_and_bank_presets_close_the_item(con):
    con.apply_preset("bank_shows_payment", {"invoice_id": "INV-KW-0301", "on": "2026-04-22"})
    line = {l.invoice_id: l for l in con.rails.ledger("keyway")}["INV-KW-0301"]
    assert line.paid_amount == 1240.0


def test_bank_change_is_held(con):
    r = con.apply_text("Summit Heating & Air asks to change bank details")
    assert "identity verification" in r.events[0].payload["decision"]


def test_unclear_text_is_refused_with_suggestions(con):
    for text in ["", "make everything look fine", "the invoice arrived Apr 1", "the electrician invoice arrived"]:
        with pytest.raises(ConsoleError) as e:
            con.apply_text(text)
        assert e.value.suggestions
    assert len(con.rails.store.events(type="fact_corrected")) == 0


def test_bad_params(con):
    for preset, params in [("invoice_received_correction", {"date": "next week"}),
                           ("invoice_received_correction", {"invoice_id": "NOPE"}),
                           ("terms_changed", {"manager_id": "keyway", "days": "ten"}),
                           ("nope", {})]:
        with pytest.raises(ConsoleError):
            con.apply_preset(preset, params)


def test_terms_change_hits_every_invoice_of_that_manager(con):
    r = con.apply_preset("terms_changed", {"manager_id": "keyway", "days": "20"})
    keyway = {l.invoice_id for l in con.rails.ledger("keyway")}
    assert set(r.affected) == keyway


def test_http_api(con, monkeypatch):
    monkeypatch.setattr(console_api, "_console", con)
    client = TestClient(console_api.app)
    assert {p["id"] for p in client.get("/presets").json()} >= {"invoice_received_correction", "manager_replies_paid"}
    r = client.post("/presets/invoice_received_correction", json={"params": {}})
    assert r.status_code == 200 and r.json()["affected"] == ["INV-KW-0402"]
    r = client.post("/text", json={"text": "nonsense"})
    assert r.status_code == 422 and r.json()["detail"]["suggestions"]


@pytest.fixture
def demo(schema):
    """The exact world `python -m rails.load` builds for the demo."""
    from world.generator import ALL_SCENARIOS
    r = RealRails(schema=schema)
    r.load(generate(13, ALL_SCENARIOS, 100).events())
    return JudgeConsole(r)


def test_every_example_sentence_and_preset_default_works_on_the_demo_world(demo):
    from rails.console import PRESETS
    from rails.console.parse import EXAMPLES
    for text in EXAMPLES:
        demo.apply_text(text)
    for p in PRESETS:
        demo.apply_preset(p.id)


def test_moment_on_the_demo_world_selects_only_the_electrician(demo):
    r = demo.apply_text("The electrician's invoice only arrived Apr 15", manager_id="keyway")
    assert r.affected == ["INV-KW-0402"]


def test_old_preset_name_still_works(con):
    r = con.apply_preset("invoice_arrived")
    assert r.affected == ["INV-KW-0402"] and due(con, "INV-KW-0402") == date(2026, 4, 25)
