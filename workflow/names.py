"""Display names for managers and vendors, from the entity_registered events in the log."""

from __future__ import annotations

from collections.abc import Callable


def names_from(rails) -> Callable[[str], str]:
    cache: dict[str, str] = {}

    def name(entity_id: str) -> str:
        if entity_id not in cache:
            cache.update({e.payload["id"]: e.payload["name"] for e in rails.events()
                          if e.type == "entity_registered" and "name" in e.payload})
        return cache.get(entity_id) or entity_id.removeprefix("v-").replace("-", " ").title()

    return name
