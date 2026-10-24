"""Durable local event log using the existing MemoryRails projections.

Every write appends. A workspace lock serializes CLI/watch sessions; a truncated or
conflicting log fails closed. This local adapter does not replace Postgres RealRails.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path

from contracts.models import Event
from rails.serialization import canonical
from workflow.memrails import MemoryRails


class FileRails(MemoryRails):
    def __init__(self, path):
        self.path = Path(path)
        self._loading = True
        self._known = {}
        events = []
        if self.path.exists():
            with self.path.open() as stream:
                for number, line in enumerate(stream, 1):
                    try:
                        events.append(Event.model_validate_json(line))
                    except ValueError as exc:
                        raise ValueError(f"event log is invalid at line {number}; no records were modified") from exc
        super().__init__(events)
        self._loading = False

    def append_event(self, event):
        encoded = canonical(event.model_dump(mode="json"))
        if event.id in self._known:
            if self._known[event.id] != encoded:
                raise ValueError(f"conflicting payload for immutable event {event.id}")
            return
        if not self._loading:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(encoded+"\n")
                stream.flush()
                os.fsync(stream.fileno())
        super().append_event(event)
        self._known[event.id] = encoded


@contextmanager
def workspace_lock(directory):
    import fcntl
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield directory
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def read_config(directory):
    path = Path(directory) / "workspace.json"
    if not path.exists():
        raise ValueError("workspace is not initialized; run the init command first")
    return json.loads(path.read_text())
