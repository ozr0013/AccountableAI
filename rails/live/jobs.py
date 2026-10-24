"""One background worker, so an API request never waits for model calls.

The workflow must run one event at a time, so there is exactly one worker thread. Run the API
with a single uvicorn worker (several processes would each have their own queue and lock).
"""

from __future__ import annotations

import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime


class Jobs:
    def __init__(self):
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="live-worker")
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def submit(self, label: str, fn, *args, **kwargs) -> str:
        job_id = f"job-{uuid.uuid4().hex[:8]}"
        with self._lock:
            self._jobs[job_id] = {"job_id": job_id, "label": label, "status": "queued",
                                  "queued_at": datetime.now(UTC).isoformat(), "result": None,
                                  "error": None}
        self._pool.submit(self._run, job_id, fn, args, kwargs)
        return job_id

    def _run(self, job_id, fn, args, kwargs):
        self._set(job_id, status="running", started_at=datetime.now(UTC).isoformat())
        try:
            self._set(job_id, status="done", result=fn(*args, **kwargs))
        except Exception as e:      # noqa: BLE001 - reported on the job, never lost
            self._set(job_id, status="failed", error=f"{type(e).__name__}: {e}",
                      trace=traceback.format_exc(limit=4))
        self._set(job_id, finished_at=datetime.now(UTC).isoformat())

    def _set(self, job_id, **kw):
        with self._lock:
            self._jobs[job_id].update(kw)

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            j = self._jobs.get(job_id)
            return dict(j) if j else None

    def pending(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j["status"] in ("queued", "running"))
