import json
from pathlib import Path

from contracts.models import Document, LedgerLine
from world import generate

FIXTURE = Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "sample"


def load(name):
    return json.loads((FIXTURE / name).read_text())


def test_scenario_3_matches_shared_fixture():
    w = generate(n_clean=0)
    fixture_docs = [Document(**d) for d in load("documents.json")]
    mine = {d.id: d for d in w.documents}
    for d in fixture_docs:
        assert mine[d.id] == d, d.id
    assert w.ledger == [LedgerLine(**r) for r in load("ledger.json")]
    exp = load("expected.json")
    assert w.truth == [{k: v for k, v in exp.items()}]
    meta = load("world.json")              # the fixture keeps its own as_of (Apr 25)
    assert meta["owner"] == w.owner and meta["managers"] == [m.model_dump() for m in w.managers]
    assert all(d.dated <= w.as_of for d in fixture_docs)


def test_deterministic_and_seed_sensitive():
    a, b, c = generate(5), generate(5), generate(6)
    assert a.documents == b.documents and a.ledger == b.ledger
    assert a.ledger != c.ledger


def test_clean_invoices_are_clean():
    w = generate()
    clean = [l for l in w.ledger if l.invoice_id != "INV-KW-0301"]
    assert len(clean) == 100
    assert {l.manager_id for l in clean} == {"keyway"}
    assert {l.vendor_id for l in clean}.isdisjoint({"v-sparkline", "v-amperpro"})   # no electricians
    assert len({l.invoice_id for l in w.ledger}) == len(w.ledger)
    ids = [d.id for d in w.documents]
    assert len(ids) == len(set(ids))
    for l in clean:
        assert l.funded_amount == l.paid_amount == l.amount < 5000
        assert l.paid_on <= l.due_date and l.paid_on <= w.as_of


def test_clean_statements_agree_with_bank():
    w = generate()
    stmts = {d.id[len("doc-stmt-"):]: d for d in w.documents if d.id.endswith("-ops") and d.kind == "manager_statement"}
    banks = {d.id[len("doc-bank-"):]: d for d in w.documents if d.id.endswith("-ops") and d.kind == "manager_bank"}
    assert stmts.keys() == banks.keys()
    for k in stmts:
        assert [l["invoice_id"] for l in stmts[k].data["lines"]] == [p["invoice_id"] for p in banks[k].data["payments"]]


def test_moment_receipt_date_comes_from_the_statement():
    from datetime import date
    from rails.ledger import build_ledger
    from world.generator import MOMENT, MOMENT_RECORDED
    w = generate(scenarios=(3,), n_clean=5, moment=True)
    line = next(l for l in build_ledger(w.events()) if l.invoice_id == "INV-KW-0402")
    assert (line.invoice_received, line.due_date) == (date(2026, 3, 28), date(2026, 4, 7))
    assert line.paid_amount == 0 and line.funded_amount == 900.0
    fact = next(e for e in w.events() if e.id == MOMENT_RECORDED)
    assert fact.type == "fact_recorded" and fact.payload["fact_key"] == MOMENT["fact_key"]
    assert fact.source.source_id == "doc-stmt-kw-2026-03-elec"
    inv = next(d for d in w.documents if d.id == "doc-inv-kw-0402")
    assert "received" not in inv.data          # the receipt date is only on the statement
    assert generate(scenarios=(3,)).moment is None
