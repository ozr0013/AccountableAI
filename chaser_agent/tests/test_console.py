"""The Judge Console's reader turns the sample paperwork into documents for the agents."""
from chaser_agent import console


def test_the_statement_sample_reads_as_three_statement_lines():
    docs, reader = console.read(console.sample_path("Keyway_Owner_Statement_Apr2026.pdf"))
    assert reader["kinds"] == ["manager_statement"]
    assert reader["invoices"] == ["INV-KW-0301", "INV-KW-0402", "INV-KW-0501"]
    [doc] = docs
    line = next(ln for ln in doc.data["lines"] if ln["invoice_id"] == "INV-KW-0301")
    assert line["amount"] == 640.0


def test_only_listed_samples_can_be_sent():
    import pytest
    with pytest.raises(KeyError):
        console.sample_path("../../.env")
