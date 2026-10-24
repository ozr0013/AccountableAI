"""Messy raw files -> documents. Rules only (no OCR, no model), so it runs anywhere."""

from datetime import date

import pytest

from agents.extract.fields import VendorIndex, keyway_refs, parse_date
from evals.extraction import run
from world.generator import generate


@pytest.fixture(scope="module")
def result(tmp_path_factory, monkeypatch_module):
    monkeypatch_module.setenv("EXTRACT_OCR", "off")
    monkeypatch_module.setenv("EXTRACT_LLM", "off")
    return run(13, 20, tmp_path_factory.mktemp("raw"))


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def test_dates_people_type():
    ref = date(2026, 4, 22)
    assert parse_date("03/03/2026") == date(2026, 3, 3)
    assert parse_date("3/3/26") == date(2026, 3, 3)
    assert parse_date("March 3, 2026") == date(2026, 3, 3)
    assert parse_date("3-Mar-2026") == date(2026, 3, 3)
    assert parse_date("hit our account Tuesday", ref) == date(2026, 4, 21)


def test_keyway_refs_in_every_spelling():
    for s in ("KW-0301", "KW0301", "kw 0301", "WO #0301", "Keyway WO 0301", "INV-KW-0301"):
        assert keyway_refs(s) == ["INV-KW-0301"], s
    assert keyway_refs("Invoice RG-2304, PPD ID:4545211074") == []


def test_vendor_names_from_letterheads_and_bank_memos():
    idx = VendorIndex(generate(13, (3,), 0).vendors)
    assert idx.match("RIO GRANDE PLMB")[0] == "v-riogrande"
    assert idx.match("Green Edge Lawn & Landscape")[0] == "v-greenedge"
    assert idx.match("SUMMIT HTG AIR")[0] == "v-summit"
    assert idx.match("Acme Pest Control")[0] is None


def test_every_scenario_document_reads_back_exactly(result):
    w, res, s = result
    scenario_misses = [m for m in s["misses"] if "-kw-0" in m.split(" ")[0]]
    assert not scenario_misses, scenario_misses


def test_overall_accuracy_and_refusals(result):
    w, res, s = result
    assert s["fields_ok"] / s["fields_all"] >= 0.95
    assert s["ignored_ok"] == s["ignored_total"]          # junk mail and duplicate forwards
    assert s["flagged_ok"] == s["flagged_total"]          # unreadable scans go to a person


def test_lookalike_bank_change_sender_is_flagged(result):
    w, res, s = result
    notes = [p for p in res.records if p.data.get("status") == "bank_change_request"]
    assert notes and any("not the vendor's address on file" in x for x in notes[0].warnings)
