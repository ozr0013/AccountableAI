"""The agents' SQL is read-only, single-statement and limited to the books' views."""
import pytest

from chaser_agent import books


@pytest.mark.parametrize("sql", [
    "SELECT * FROM invoices",
    "select vendor_name, sum(amount) from bank_payments group by 1",
    "WITH open AS (SELECT invoice_id FROM invoices) SELECT * FROM open o JOIN fundings f USING (invoice_id)",
    "SELECT * FROM bank_payments WHERE amount IS DISTINCT FROM 0 AND extract(month FROM paid_on) = 4",
])
def test_reads_over_the_views_are_allowed(sql):
    assert books.check(sql)


@pytest.mark.parametrize("sql,why", [
    ("DELETE FROM invoices", "only SELECT"),
    ("SELECT 1; DROP TABLE events", "one statement"),
    ("SELECT * FROM events", "unknown table"),
    ("SELECT * FROM public.events", "unknown table"),
    ("SELECT pg_sleep(10)", "not allowed"),
    ("SELECT * INTO copy FROM invoices", "not allowed"),
    ("SELECT * FROM information_schema.tables", "not allowed"),
    ("SELECT 1 -- hidden", "not allowed"),
])
def test_anything_else_is_refused(sql, why):
    with pytest.raises(books.QueryRefused, match=why):
        books.check(sql)


def test_in_memory_books_answer_with_an_error_not_a_crash():
    from workflow.memrails import MemoryRails
    tool = books.tool_for(MemoryRails.from_world((3,), n_clean=0))
    assert "not in PostgreSQL" in tool.fn(purpose="count invoices", sql="SELECT count(*) FROM invoices")["error"]
