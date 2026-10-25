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


def _books():
    from workflow.memrails import MemoryRails
    return MemoryRails.from_world((3,), n_clean=0)


def test_queries_run_on_a_snapshot_of_the_books_with_full_sql():
    tool = books.tool_for(_books())
    out = tool.fn(purpose="invoices per vendor",
                  sql="SELECT vendor_name, count(*) n, sum(amount) total FROM invoices GROUP BY 1 ORDER BY 3 DESC")
    assert "error" not in out and out["columns"] == ["vendor_name", "n", "total"] and out["row_count"] >= 1
    sub = tool.fn(purpose="open invoices", sql="SELECT invoice_id FROM invoices i WHERE NOT EXISTS "
                  "(SELECT 1 FROM bank_payments b WHERE b.invoice_id = i.invoice_id)")
    assert "error" not in sub


def test_the_snapshot_cannot_reach_files_or_change_settings_even_past_the_guard():
    con = books._snapshot(_books()).cursor()
    for sql in ("SELECT * FROM read_csv('/etc/hosts')", "SET enable_external_access = true",
                "COPY invoices TO '/tmp/leak.csv'"):
        with pytest.raises(Exception):
            con.execute(sql)


@pytest.mark.parametrize("sql", ["SELECT * FROM read_csv('/etc/passwd')", "SELECT * FROM glob('*')",
                                 "SELECT getenv('ANTHROPIC_API_KEY')", "SELECT * FROM query('SELECT 1')"])
def test_duckdb_file_and_env_functions_are_refused(sql):
    with pytest.raises(books.QueryRefused):
        books.check(sql)


def test_citations_are_checked_against_the_books():
    rails = _books()
    real = books.run(rails, "SELECT document_id FROM documents LIMIT 1")["rows"][0][0]
    assert books.known(rails, "documents", "document_id", [real, "made-up"]) == {real}
