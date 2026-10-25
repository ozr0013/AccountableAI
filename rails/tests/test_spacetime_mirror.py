"""The SpacetimeDB mirror: reducer arguments, opt-in switch, and never breaking Postgres."""
from datetime import UTC, datetime

from contracts.models import Event
from rails.live import spacetime

NOW = datetime(2026, 4, 22, 12, tzinfo=UTC)
EVENT = Event(id="ev-1", type="chase_sent", manager_id="keyway", valid_from=NOW, learned_at=NOW,
              payload={"level": "polite"})


def test_publish_sends_reducer_args_in_module_order(monkeypatch):
    calls = []
    monkeypatch.setattr(spacetime, "_post", lambda path, body, token: calls.append((path, body)))
    spacetime.SpacetimeMirror(token="t").publish(7, EVENT)
    [(path, args)] = calls
    assert path == "call/publish_event"
    assert args[:5] == ["ev-1", 7, "chase_sent", {"some": "keyway"}, {"none": []}]
    assert args[-1] == '{"level": "polite"}'


def test_mirror_is_off_unless_enabled(monkeypatch):
    monkeypatch.delenv("SPACETIME", raising=False)
    monkeypatch.setattr(spacetime, "SpacetimeMirror", lambda: (_ for _ in ()).throw(AssertionError))
    spacetime.mirror_new(None, EVENT)


def test_mirror_failure_never_raises(monkeypatch):
    monkeypatch.setenv("SPACETIME", "on")
    monkeypatch.setattr(spacetime, "_MIRROR", None)

    class Down:
        def publish(self, seq, event):
            raise OSError("connection refused")

    class Store:
        class conn:
            @staticmethod
            def execute(sql, params):
                return type("R", (), {"fetchone": staticmethod(lambda: (1,))})()

    monkeypatch.setattr(spacetime, "SpacetimeMirror", Down)
    spacetime.mirror_new(Store, EVENT)
