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
    r.load(generate(scenarios=(3, 5), n_clean=30).events())
    return JudgeConsole(r)


def due(con, iid):
    return {l.invoice_id: l for l in build_ledger(con.rails.store.events(current_only=True))}[iid].due_date


def test_moment_preset_moves_deadline_and_selects_only_that_item(con):
    assert due(con, "INV-KW-0402") == date(2026, 3, 30)
    r = con.apply_preset("invoice_arrived")                       # defaults = the demo moment
    assert r.affected == ["INV-KW-0402"]
    assert due(con, "INV-KW-0402") == date(2026, 4, 11)
    assert due(con, "INV-KW-0301") == date(2026, 3, 13)           # the plumber alert is untouched
    assert r.events[0].learned_at.date() == con.rails.now() and r.events[0].valid_from.date() == date(2026, 4, 1)


def test_same_click_twice_is_a_noop_and_new_value_supersedes(con):
    a = con.apply_preset("invoice_arrived")
    b = con.apply_preset("invoice_arrived")
    assert a.event_ids == b.event_ids and len(con.rails.store.events(type="fact_corrected")) == 1
    c = con.apply_preset("invoice_arrived", {"date": "2026-04-03"})
    assert c.events[0].supersedes == a.events[0].id
    assert due(con, "INV-KW-0402") == date(2026, 4, 13)           # latest wins; history kept
    assert len(con.rails.store.events(type="fact_corrected")) == 2


def test_free_text_moment(con):
    r = con.apply_text("The electrician's invoice only arrived Apr 1", manager_id="keyway")
    assert r.affected == ["INV-KW-0402"] and due(con, "INV-KW-0402") == date(2026, 4, 11)


def test_free_text_names_manager_in_sentence(con):
    r = con.apply_text("Keyway says the electrician invoice arrived on April 1")
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
    r = con.apply_text("Sparkline Electric asks to change bank details")
    assert "identity verification" in r.events[0].payload["decision"]


def test_unclear_text_is_refused_with_suggestions(con):
    for text in ["", "make everything look fine", "the invoice arrived Apr 1", "the electrician invoice arrived"]:
        with pytest.raises(ConsoleError) as e:
            con.apply_text(text)
        assert e.value.suggestions
    assert len(con.rails.store.events(type="fact_corrected")) == 0


def test_bad_params(con):
    for preset, params in [("invoice_arrived", {"date": "next week"}),
                           ("invoice_arrived", {"invoice_id": "NOPE"}),
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
    assert {p["id"] for p in client.get("/presets").json()} >= {"invoice_arrived", "manager_replies_paid"}
    r = client.post("/presets/invoice_arrived", json={"params": {}})
    assert r.status_code == 200 and r.json()["affected"] == ["INV-KW-0402"]
    r = client.post("/text", json={"text": "nonsense"})
    assert r.status_code == 422 and r.json()["detail"]["suggestions"]
