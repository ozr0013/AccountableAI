"""Durable idempotent wake jobs with atomic leases and retry recovery."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

from psycopg.types.json import Jsonb

from contracts.models import Event
from rails.cases import aware, connection, identity, memory_lock
from workflow.cases import CaseStore


class LeaseConflict(RuntimeError):
    """The caller does not hold a live job lease."""


_COLUMNS = "id,case_id,due_at,payload,status,attempts,lease_owner,lease_until"
_KEYS = _COLUMNS.split(",")


def _job(row):
    return dict(zip(_KEYS, row)) if row else None


class Scheduler:
    def __init__(self, rails):
        self.rails = rails
        self.conn = connection(rails)
        self.lock = memory_lock(rails)
        if self.conn is None and not hasattr(rails, "_scheduled_jobs"):
            # Fold append-only snapshots so recreating MemoryRails(events) recovers leases too.
            rails._scheduled_jobs = {}
            for event in rails.events():
                if event.type == "job_changed":
                    job = dict(event.payload["job"])
                    for field in ("due_at", "lease_until"):
                        if isinstance(job[field], str):
                            job[field] = datetime.fromisoformat(job[field])
                    rails._scheduled_jobs[job["id"]] = job

    def _save(self, job):
        body = {k: v.isoformat() if isinstance(v, datetime) else v for k, v in job.items()}
        seq = sum(
            e.type == "job_changed" and e.payload.get("job", {}).get("id") == job["id"]
            for e in self.rails.events()
        )
        now = datetime.now(UTC)
        self.rails.append_event(
            Event(
                id=f"{job['id']}:revision:{seq}",
                type="job_changed",
                valid_from=now,
                learned_at=now,
                payload={"job": body},
            )
        )

    def schedule(self, case_id, due_at, key, payload=None) -> str:
        aware(due_at)
        CaseStore(self.rails).get(case_id)
        job_id = "job_" + identity(case_id, key)
        if self.conn is not None:
            self.conn.execute(
                "INSERT INTO scheduled_jobs(id,case_id,schedule_key,due_at,payload) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (job_id, case_id, key, due_at, Jsonb(payload or {})),
            )
        else:
            with self.lock:
                if job_id not in self.rails._scheduled_jobs:
                    job = {
                        "id": job_id,
                        "case_id": case_id,
                        "due_at": due_at,
                        "payload": deepcopy(payload or {}),
                        "status": "pending",
                        "attempts": 0,
                        "lease_owner": None,
                        "lease_until": None,
                    }
                    self.rails._scheduled_jobs[job_id] = job
                    self._save(job)
        return job_id

    def claim(self, worker_id, now, lease_seconds=120):
        aware(now)
        if not worker_id or lease_seconds <= 0:
            raise ValueError("worker and positive lease duration required")
        until = now + timedelta(seconds=lease_seconds)
        if self.conn is not None:
            row = self.conn.execute(
                f"""WITH candidate AS (
                SELECT id FROM scheduled_jobs WHERE due_at<=%s AND
                (status='pending' OR (status='leased' AND lease_until<=%s))
                ORDER BY due_at,id FOR UPDATE SKIP LOCKED LIMIT 1)
                UPDATE scheduled_jobs j SET status='leased',attempts=j.attempts+1,
                lease_owner=%s,lease_until=%s FROM candidate c WHERE j.id=c.id
                RETURNING {",".join("j." + k for k in _KEYS)}""",
                (now, now, worker_id, until),
            ).fetchone()
            return _job(row)
        with self.lock:
            jobs = sorted(self.rails._scheduled_jobs.values(), key=lambda j: (j["due_at"], j["id"]))
            for job in jobs:
                if job["due_at"] <= now and (
                    job["status"] == "pending"
                    or job["status"] == "leased"
                    and job["lease_until"] <= now
                ):
                    job.update(
                        status="leased",
                        attempts=job["attempts"] + 1,
                        lease_owner=worker_id,
                        lease_until=until,
                    )
                    self._save(job)
                    return deepcopy(job)
        return None

    def complete(self, job_id, worker_id, now):
        aware(now)
        self._finish(job_id, worker_id, "completed", now=now)

    def fail(self, job_id, worker_id, error, retry_at, *, now=None):
        aware(retry_at)
        # Explicit clocks support replay; ordinary workers default to wall UTC.
        failure_at = aware(now) if now is not None else datetime.now(UTC)
        if retry_at < failure_at:
            raise ValueError("retry time must not precede failure")
        self._finish(job_id, worker_id, "pending", now=failure_at, retry_at=retry_at, error=error)

    def _finish(self, job_id, worker_id, status, now, retry_at=None, error=None):
        if self.conn is not None:
            row = self.conn.execute(
                """UPDATE scheduled_jobs SET status=%s,lease_owner=NULL,lease_until=NULL,
                due_at=COALESCE(%s,due_at),last_error=%s WHERE id=%s AND status='leased'
                AND lease_owner=%s AND lease_until>%s RETURNING id""",
                (status, retry_at, error, job_id, worker_id, now),
            ).fetchone()
            if not row:
                raise LeaseConflict(job_id)
        else:
            with self.lock:
                job = self.rails._scheduled_jobs.get(job_id)
                if (
                    not job
                    or job["status"] != "leased"
                    or job["lease_owner"] != worker_id
                    or job["lease_until"] <= now
                ):
                    raise LeaseConflict(job_id)
                job.update(status=status, lease_owner=None, lease_until=None)
                if retry_at is not None:
                    job["due_at"] = retry_at
                self._save(job)

    def cancel(self, case_id, preserve_leased=False):
        if self.conn is not None:
            self.conn.execute(
                "UPDATE scheduled_jobs SET status='cancelled',lease_owner=NULL,lease_until=NULL WHERE case_id=%s AND (status='pending' OR (status='leased' AND NOT %s))",
                (case_id, preserve_leased),
            )
        else:
            with self.lock:
                for job in self.rails._scheduled_jobs.values():
                    if job["case_id"] == case_id and (job["status"] == "pending" or (job["status"] == "leased" and not preserve_leased)):
                        job.update(status="cancelled", lease_owner=None, lease_until=None)
                        self._save(job)

    def list(self, case_id=None):
        if self.conn is not None:
            return [
                _job(row)
                for row in self.conn.execute(
                    f"SELECT {_COLUMNS} FROM scheduled_jobs WHERE (%s::text IS NULL OR case_id=%s) ORDER BY due_at,id",
                    (case_id, case_id),
                ).fetchall()
            ]
        with self.lock:
            return [
                deepcopy(j)
                for j in sorted(
                    self.rails._scheduled_jobs.values(), key=lambda j: (j["due_at"], j["id"])
                )
                if case_id is None or j["case_id"] == case_id
            ]
