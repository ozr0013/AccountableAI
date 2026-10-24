"""Serialize event projections across processes using a PostgreSQL advisory lock."""
from contextlib import contextmanager

from rails.cases import identity, memory_lock


@contextmanager
def runtime_lock(rails, key):
    with memory_lock(rails):
        conn = getattr(getattr(rails, 'store', None), 'conn', None)
        if conn is None:
            yield
        else:
            with conn.transaction():
                lock_id = int(identity(key)[:15], 16)
                conn.execute('SELECT pg_advisory_xact_lock(%s)', (lock_id,))
                yield
