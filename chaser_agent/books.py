"""Read-only SQL over the books, for the agents to investigate with.

The books (the event log, in SpacetimeDB) are flattened into a few plain tables inside an
in-process DuckDB snapshot, rebuilt whenever the log grows. SpacetimeDB's own SQL has no GROUP BY,
SUM, subqueries or ORDER BY, which an investigation needs; the snapshot gives the agents full SQL
without a second database server. An agent may run a single SELECT against these tables only:
DuckDB has no file or network access here, its settings are locked, and each query has a time
limit and a row cap. Every query is streamed to the page with its purpose and a preview of the
rows, so anyone watching sees exactly what the agent looked at.
"""
from __future__ import annotations

import re
import threading
from datetime import date, datetime

ROW_CAP = 200
ROWS_TO_MODEL = 60
TIMEOUT_MS = 4000

# name -> (columns described for the model, DuckDB column types)
TABLES: dict[str, tuple[str, str]] = {
    "vendors": ("vendor_id, vendor_name, trade", "vendor_id VARCHAR, vendor_name VARCHAR, trade VARCHAR"),
    "documents": ("document_id, kind, vendor_id, dated, invoice_id, text, filed_at",
                  "document_id VARCHAR, kind VARCHAR, vendor_id VARCHAR, dated DATE, invoice_id VARCHAR, "
                  "text VARCHAR, filed_at TIMESTAMPTZ"),
    "invoices": ("invoice_id, vendor_id, vendor_name, amount, received_on, document_id, text",
                 "invoice_id VARCHAR, vendor_id VARCHAR, vendor_name VARCHAR, amount DECIMAL(14,2), "
                 "received_on DATE, document_id VARCHAR, text VARCHAR"),
    "fundings": ("invoice_id, amount, funded_on, document_id",
                 "invoice_id VARCHAR, amount DECIMAL(14,2), funded_on DATE, document_id VARCHAR"),
    "bank_payments": ("document_id, export_date, paid_on, invoice_id (may be null), vendor_id, vendor_name, "
                      "amount, payee, bank, account, memo",
                      "document_id VARCHAR, export_date DATE, paid_on DATE, invoice_id VARCHAR, vendor_id VARCHAR, "
                      "vendor_name VARCHAR, amount DECIMAL(14,2), payee VARCHAR, bank VARCHAR, account VARCHAR, "
                      "memo VARCHAR"),
    "statement_lines": ("document_id, statement_date, line, invoice_id, amount, status, paid_on",
                        "document_id VARCHAR, statement_date DATE, line VARCHAR, invoice_id VARCHAR, "
                        "amount DECIMAL(14,2), status VARCHAR, paid_on DATE"),
    "vendor_messages": ("document_id, kind (vendor_confirmation | vendor_notice), vendor_id, invoice_id, "
                        "dated, amount, status, text",
                        "document_id VARCHAR, kind VARCHAR, vendor_id VARCHAR, invoice_id VARCHAR, dated DATE, "
                        "amount DECIMAL(14,2), status VARCHAR, text VARCHAR"),
    "manager_replies": ("document_id, dated, invoice_id, status, text",
                        "document_id VARCHAR, dated DATE, invoice_id VARCHAR, status VARCHAR, text VARCHAR"),
}
VIEWS = TABLES                      # the names the guard and the prompts refer to

GUIDE = ("Read-only SQL (DuckDB, close to PostgreSQL) over the books. One SELECT (or WITH ... SELECT) "
         "using only these tables: " + "; ".join(f"{n}({c})" for n, (c, _) in TABLES.items()) +
         f". Money is decimal dollars, dates are date. At most {ROW_CAP} rows; {TIMEOUT_MS} ms limit. "
         "bank_payments.invoice_id can be null or name another invoice: match by vendor, amount and "
         "date when it does.")

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|copy|call|do|execute|"
    r"prepare|listen|notify|vacuum|analyze|set|reset|lock|comment|refresh|security|into|attach|detach|"
    r"install|load|pragma|export|import|checkpoint|getenv|query|query_table|glob|"
    r"read_\w+|sniff_\w+|parquet_\w+|duckdb_\w+)\b|pg_\w+|"
    r"\binformation_schema\b|--|/\*|\\\\", re.IGNORECASE)
_RELATION = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][\w.]*)", re.IGNORECASE)


class QueryRefused(ValueError):
    pass


def check(sql: str) -> str:
    """The statement the agent may run, or QueryRefused saying why not."""
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        raise QueryRefused("empty query")
    if ";" in text:
        raise QueryRefused("one statement only")
    if not re.match(r"^(select|with)\b", text, re.IGNORECASE):
        raise QueryRefused("only SELECT queries are allowed")
    if m := _FORBIDDEN.search(text):
        raise QueryRefused(f"not allowed in a read-only query: {m.group(0)!r}")
    ctes = {c.lower() for c in re.findall(r"(?:with|,)\s*([a-zA-Z_]\w*)\s+as\s*\(", text, re.IGNORECASE)}
    # FROM also appears in expressions that name no table.
    scan = re.sub(r"\bdistinct\s+from\b", " ", text, flags=re.IGNORECASE)
    scan = re.sub(r"\b(extract|substring|trim|overlay|position)\s*\([^()]*\)", "f()", scan, flags=re.IGNORECASE)
    for rel in _RELATION.findall(scan):
        name = rel.lower()
        if "." in name or (name not in TABLES and name not in ctes and name not in ("lateral", "unnest")):
            raise QueryRefused(f"unknown table {rel!r}; use only: {', '.join(TABLES)}")
    return text


# -- the snapshot ---------------------------------------------------------------------------
def _d(v) -> date | None:
    if not v:
        return None
    try:
        return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _n(v) -> float | None:
    try:
        return None if v is None else round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _rows(events) -> dict[str, list[tuple]]:
    """Flatten the event log into the tables the agents query."""
    out: dict[str, list[tuple]] = {name: [] for name in TABLES}
    names = {}
    for e in events:
        p = e.payload
        if e.type == "entity_registered" and p.get("kind") == "vendor":
            names[p.get("id")] = p.get("name")
            out["vendors"].append((p.get("id"), p.get("name"), p.get("trade")))
    for e in events:
        if e.type != "document_ingested":
            continue
        p, data = e.payload, e.payload.get("data") or {}
        doc, kind, vendor, dated = p.get("id"), p.get("kind"), p.get("vendor_id"), _d(p.get("dated"))
        text = (p.get("text") or "")[:400]
        out["documents"].append((doc, kind, vendor, dated, data.get("invoice_id"), text, e.learned_at))
        if kind == "vendor_invoice":
            out["invoices"].append((data.get("invoice_id"), vendor, names.get(vendor), _n(data.get("amount")),
                                    _d(data.get("received")), doc, text[:300]))
        elif kind == "funding":
            out["fundings"].append((data.get("invoice_id"), _n(data.get("amount")), _d(data.get("funded_on")), doc))
        elif kind == "manager_bank":
            for r in data.get("payments") or []:
                out["bank_payments"].append((doc, dated, _d(r.get("on")), r.get("invoice_id"), r.get("vendor_id"),
                                             names.get(r.get("vendor_id")), _n(r.get("amount")), r.get("payee"),
                                             r.get("bank"), r.get("account"), r.get("memo")))
        elif kind == "manager_statement":
            for r in data.get("lines") or []:
                out["statement_lines"].append((doc, dated, r.get("line"), r.get("invoice_id"), _n(r.get("amount")),
                                               r.get("status"), _d(r.get("on"))))
        elif kind in ("vendor_confirmation", "vendor_notice"):
            out["vendor_messages"].append((doc, kind, vendor, data.get("invoice_id"), dated, _n(data.get("amount")),
                                           data.get("status"), text[:300]))
        elif kind == "manager_reply":
            out["manager_replies"].append((doc, dated, data.get("invoice_id"), data.get("status"), text))
    return out


_cache: dict[int, tuple[tuple, object]] = {}
_cache_lock = threading.Lock()


def _snapshot(rails):
    """A locked-down DuckDB connection holding the books, rebuilt when the log has grown."""
    import duckdb
    events = rails.events()
    stamp = (len(events), events[-1].id if events else None)
    with _cache_lock:
        hit = _cache.get(id(rails))
        if hit and hit[0] == stamp:
            return hit[1]
        con = duckdb.connect(":memory:")
        for name, rows in _rows(events).items():
            con.execute(f"CREATE TABLE {name} ({TABLES[name][1]})")
            if rows:
                marks = ", ".join("?" * len(rows[0]))
                con.executemany(f"INSERT INTO {name} VALUES ({marks})", rows)
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
        _cache[id(rails)] = (stamp, con)
        return con


def _plain(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if v is None or isinstance(v, (str, int, bool)):
        return v
    try:
        return float(v)                      # Decimal and friends
    except (TypeError, ValueError):
        return str(v)


def run(rails, sql: str) -> dict:
    """Run one checked SELECT on the books' snapshot. Returns columns, rows (capped) and count."""
    text = check(sql)
    con = _snapshot(rails).cursor()
    timer = threading.Timer(TIMEOUT_MS / 1000, con.interrupt)
    timer.start()
    try:
        cur = con.execute(f"SELECT * FROM ({text}) AS q LIMIT {ROW_CAP + 1}")
        cols = [d[0] for d in cur.description or []]
        rows = [[_plain(v) for v in r] for r in cur.fetchall()]
    finally:
        timer.cancel()
        con.close()
    return {"columns": cols, "rows": rows[:ROW_CAP], "row_count": min(len(rows), ROW_CAP),
            "truncated": len(rows) > ROW_CAP}


def tool_result(rails, sql: str, purpose: str) -> dict:
    """What the model gets back, and the event the page shows."""
    from .runtime import emit
    try:
        out = run(rails, sql)
    except QueryRefused as exc:
        emit({"type": "query", "purpose": purpose, "sql": sql, "error": str(exc)})
        return {"error": f"query refused: {exc}"}
    except Exception as exc:  # noqa: BLE001 - a bad query is the model's to fix, not a crash
        msg = str(exc).splitlines()[0][:300]
        emit({"type": "query", "purpose": purpose, "sql": sql, "error": msg})
        return {"error": f"query failed: {msg}"}
    emit({"type": "query", "purpose": purpose, "sql": sql, "columns": out["columns"],
          "rows": out["rows"][:8], "row_count": out["row_count"]})
    shown = out["rows"][:ROWS_TO_MODEL]
    return {"columns": out["columns"], "rows": shown, "row_count": out["row_count"],
            "note": (f"showing {len(shown)} of {out['row_count']} rows; aggregate or filter for the rest"
                     if out["row_count"] > len(shown) else None)}


def tool_for(rails):
    """query_books bound to these books."""
    from .runtime import Tool

    def query_books(purpose: str, sql: str) -> dict:
        return tool_result(rails, sql, purpose)
    return Tool("query_books", "Investigate the books yourself with read-only SQL. " + GUIDE,
                query_tool_spec(), query_books)


def query_tool_spec() -> dict:
    return {"type": "object", "additionalProperties": False, "required": ["purpose", "sql"],
            "properties": {"purpose": {"type": "string", "minLength": 5, "maxLength": 200,
                                       "description": "What you want to find out, in plain words"},
                           "sql": {"type": "string", "minLength": 10, "maxLength": 4000}}}


def known(rails, table: str, column: str, values: list[str]) -> set[str]:
    """Which of `values` exist in a table column (used to check what an agent cites)."""
    if not values or table not in TABLES:
        return set()
    con = _snapshot(rails).cursor()
    try:
        rows = con.execute(f"SELECT DISTINCT {column} FROM {table} WHERE {column} IN "
                           f"({', '.join('?' * len(values))})", values).fetchall()
    finally:
        con.close()
    return {r[0] for r in rows}


def forget(_book: str | None = None) -> None:
    """Drop cached snapshots (after a book was cleared)."""
    with _cache_lock:
        _cache.clear()
