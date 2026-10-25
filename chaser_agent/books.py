"""Read-only SQL over the books, for the agents to investigate with.

The event log in PostgreSQL is flattened into a few plain views (one schema per source schema,
`books_<schema>`). An agent may run a single SELECT against these views only, inside a read-only
transaction with a time limit and a row cap. Every query is streamed to the page with its
purpose and a preview of the rows, so anyone watching sees exactly what the agent looked at.
"""
from __future__ import annotations

import re
from decimal import Decimal

ROW_CAP = 200
ROWS_TO_MODEL = 60
TIMEOUT_MS = 4000

# name -> (columns described for the model, SQL over <schema>.events)
VIEWS: dict[str, tuple[str, str]] = {
    "vendors": ("vendor_id, vendor_name, trade", """
        SELECT payload->>'id' AS vendor_id, payload->>'name' AS vendor_name, payload->>'trade' AS trade
        FROM {s}.events WHERE type = 'entity_registered' AND payload->>'kind' = 'vendor'"""),
    "documents": ("document_id, kind, vendor_id, dated, invoice_id, text, filed_at", """
        SELECT payload->>'id' AS document_id, payload->>'kind' AS kind, payload->>'vendor_id' AS vendor_id,
               (payload->>'dated')::date AS dated, payload->'data'->>'invoice_id' AS invoice_id,
               left(payload->>'text', 400) AS text, learned_at AS filed_at
        FROM {s}.events WHERE type = 'document_ingested'"""),
    "invoices": ("invoice_id, vendor_id, vendor_name, amount, received_on, document_id, text", """
        SELECT d.payload->'data'->>'invoice_id' AS invoice_id, d.payload->>'vendor_id' AS vendor_id,
               v.vendor_name, (d.payload->'data'->>'amount')::numeric AS amount,
               (d.payload->'data'->>'received')::date AS received_on, d.payload->>'id' AS document_id,
               left(d.payload->>'text', 300) AS text
        FROM {s}.events d LEFT JOIN books_{s}.vendors v ON v.vendor_id = d.payload->>'vendor_id'
        WHERE d.type = 'document_ingested' AND d.payload->>'kind' = 'vendor_invoice'"""),
    "fundings": ("invoice_id, amount, funded_on, document_id", """
        SELECT payload->'data'->>'invoice_id' AS invoice_id, (payload->'data'->>'amount')::numeric AS amount,
               (payload->'data'->>'funded_on')::date AS funded_on, payload->>'id' AS document_id
        FROM {s}.events WHERE type = 'document_ingested' AND payload->>'kind' = 'funding'"""),
    "bank_payments": ("document_id, export_date, paid_on, invoice_id (may be null), vendor_id, vendor_name, "
                      "amount, payee, bank, account, memo", """
        SELECT e.payload->>'id' AS document_id, (e.payload->>'dated')::date AS export_date,
               (p->>'on')::date AS paid_on, p->>'invoice_id' AS invoice_id, p->>'vendor_id' AS vendor_id,
               v.vendor_name, (p->>'amount')::numeric AS amount, p->>'payee' AS payee, p->>'bank' AS bank,
               p->>'account' AS account, p->>'memo' AS memo
        FROM {s}.events e CROSS JOIN LATERAL jsonb_array_elements(e.payload->'data'->'payments') p
        LEFT JOIN books_{s}.vendors v ON v.vendor_id = p->>'vendor_id'
        WHERE e.type = 'document_ingested' AND e.payload->>'kind' = 'manager_bank'"""),
    "statement_lines": ("document_id, statement_date, line, invoice_id, amount, status, paid_on", """
        SELECT e.payload->>'id' AS document_id, (e.payload->>'dated')::date AS statement_date,
               l->>'line' AS line, l->>'invoice_id' AS invoice_id, (l->>'amount')::numeric AS amount,
               l->>'status' AS status, (l->>'on')::date AS paid_on
        FROM {s}.events e CROSS JOIN LATERAL jsonb_array_elements(e.payload->'data'->'lines') l
        WHERE e.type = 'document_ingested' AND e.payload->>'kind' = 'manager_statement'"""),
    "vendor_messages": ("document_id, kind (vendor_confirmation | vendor_notice), vendor_id, invoice_id, "
                        "dated, amount, status, text", """
        SELECT payload->>'id' AS document_id, payload->>'kind' AS kind, payload->>'vendor_id' AS vendor_id,
               payload->'data'->>'invoice_id' AS invoice_id, (payload->>'dated')::date AS dated,
               (payload->'data'->>'amount')::numeric AS amount, payload->'data'->>'status' AS status,
               left(payload->>'text', 300) AS text
        FROM {s}.events WHERE type = 'document_ingested'
          AND payload->>'kind' IN ('vendor_confirmation', 'vendor_notice')"""),
    "manager_replies": ("document_id, dated, invoice_id, status, text", """
        SELECT payload->>'id' AS document_id, (payload->>'dated')::date AS dated,
               payload->'data'->>'invoice_id' AS invoice_id, payload->'data'->>'status' AS status,
               left(payload->>'text', 400) AS text
        FROM {s}.events WHERE type = 'document_ingested' AND payload->>'kind' = 'manager_reply'"""),
}

GUIDE = ("Read-only SQL (PostgreSQL) over the books. One SELECT (or WITH ... SELECT) using only these "
         "views: " + "; ".join(f"{n}({c})" for n, (c, _) in VIEWS.items()) +
         f". Money is numeric dollars, dates are date. At most {ROW_CAP} rows; {TIMEOUT_MS} ms limit. "
         "bank_payments.invoice_id can be null or name another invoice: match by vendor, amount and "
         "date when it does.")

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|copy|call|do|execute|"
    r"prepare|listen|notify|vacuum|analyze|set|reset|lock|comment|refresh|security|into)\b|pg_\w+|"
    r"\binformation_schema\b|--|/\*|\\\\", re.IGNORECASE)
_RELATION = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][\w.]*)", re.IGNORECASE)
_ensured: set[str] = set()


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
        if "." in name or (name not in VIEWS and name not in ctes and name != "lateral"):
            raise QueryRefused(f"unknown table {rel!r}; use only: {', '.join(VIEWS)}")
    return text


def ensure_views(conn, schema: str) -> None:
    """(Re)create the views for one source schema. Cheap; done after every demo reset."""
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
        raise ValueError("bad schema name")
    conn.execute(f'CREATE SCHEMA IF NOT EXISTS "books_{schema}"')
    for name, (_, body) in VIEWS.items():
        conn.execute(f'CREATE OR REPLACE VIEW "books_{schema}".{name} AS {body.format(s=schema)}')
    _ensured.add(schema)


def forget(schema: str) -> None:
    _ensured.discard(schema)


def _plain(v):
    if isinstance(v, Decimal):
        return float(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def run(schema: str, sql: str) -> dict:
    """Run one checked SELECT read-only. Returns columns, rows (capped) and the total count."""
    from rails.db import connect
    text = check(sql)
    conn = connect()
    try:
        if schema not in _ensured:
            ensure_views(conn, schema)
            conn.commit()
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute(f"SET LOCAL statement_timeout = {TIMEOUT_MS}")
            conn.execute(f'SET LOCAL search_path = "books_{schema}"')
            cur = conn.execute(f"SELECT * FROM ({text}) AS q LIMIT {ROW_CAP + 1}")
            cols = [c.name for c in cur.description or []]
            rows = [[_plain(v) for v in r] for r in cur.fetchall()]
        return {"columns": cols, "rows": rows[:ROW_CAP], "row_count": min(len(rows), ROW_CAP),
                "truncated": len(rows) > ROW_CAP}
    finally:
        conn.close()


def tool_result(schema: str, sql: str, purpose: str) -> dict:
    """What the model gets back, and the event the page shows."""
    from .runtime import emit
    try:
        out = run(schema, sql)
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


def schema_of(rails) -> str | None:
    """The PostgreSQL schema a rails object reads, or None for in-memory books (tests)."""
    conn = getattr(getattr(rails, "store", None), "conn", None)
    if conn is None:
        return None
    return conn.execute("SELECT current_schema()").fetchone()[0]


def tool_for(rails):
    """query_books bound to these books (an error result when they are not in PostgreSQL)."""
    from .runtime import Tool
    schema = schema_of(rails)

    def query_books(purpose: str, sql: str) -> dict:
        if schema is None:
            return {"error": "the books are not in PostgreSQL in this run"}
        return tool_result(schema, sql, purpose)
    return Tool("query_books", "Investigate the books yourself with read-only SQL. " + GUIDE,
                query_tool_spec(), query_books)


def query_tool_spec() -> dict:
    return {"type": "object", "additionalProperties": False, "required": ["purpose", "sql"],
            "properties": {"purpose": {"type": "string", "minLength": 5, "maxLength": 200,
                                       "description": "What you want to find out, in plain words"},
                           "sql": {"type": "string", "minLength": 10, "maxLength": 4000}}}
