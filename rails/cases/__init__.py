"""Schema-scoped autonomous case persistence helpers."""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from threading import RLock


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return value


def identity(*parts: str) -> str:
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def connection(rails):
    conn = getattr(rails.store, "conn", None)
    if conn is not None:
        conn.execute((Path(__file__).parent / "schema.sql").read_text())
    return conn


def memory_lock(rails):
    if not hasattr(rails, "_autonomy_lock"):
        rails._autonomy_lock = RLock()
    return rails._autonomy_lock
