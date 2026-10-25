"""The verdict a paying agent gets, one planted scenario per invoice (offline, no payment)."""

import pytest

from paid_api.verdict import verdict


@pytest.mark.parametrize("invoice,status", [
    ("INV-KW-0101", "partially_paid_explained"),   # 1: holdback the agreement allows
    ("INV-KW-0201", "not_paid_explained"),         # 2: vendor paperwork missing, not the manager's fault
    ("INV-KW-0301", "unexplained"),                # 3: statement says paid, bank and vendor say no
    ("INV-KW-0501", "paid"),                       # 5: paid on time
    ("INV-KW-0601", "not_paid_explained"),         # 6: Oakline never approved or funded it
    ("INV-KW-0701", "not_paid_explained"),         # 7: held for a bank-detail change
    ("INV-KW-0901", "unexplained"),                # 9: "we paid" with no proof
])
def test_verdict_matches_the_answer_key(invoice, status):
    v = verdict(invoice)
    assert v["status"] == status, v
    for a in v["alerts"]:
        assert a["citations"], a                   # every alert a buyer gets cites its records


def test_unknown_invoice_is_none():
    assert verdict("INV-NOPE") is None
