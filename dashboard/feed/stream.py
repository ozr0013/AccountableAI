"""Live stream of what the agents are doing, for /api/stream (Server-Sent Events).

The workflow runs in a request thread; every DecisionRecord (and action start/finish) is
published here and fanned out to each connected browser's queue. Slow viewers lose the oldest
messages rather than slowing the workflow down.
"""

from __future__ import annotations

import queue
import threading


class Hub:
    def __init__(self, maxsize: int = 1000):
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self.maxsize = maxsize

    def add(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(self.maxsize)
        with self._lock:
            self._subs.append(q)
        return q

    def remove(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, message: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(message)
            except queue.Full:
                try:
                    q.get_nowait()          # drop the oldest, keep the newest
                    q.put_nowait(message)
                except (queue.Empty, queue.Full):
                    pass

    @property
    def viewers(self) -> int:
        with self._lock:
            return len(self._subs)
