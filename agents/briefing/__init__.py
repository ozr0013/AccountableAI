"""Omar: briefing agent. BRIEFING=real loads build() through contracts.registry."""

from .briefer import Briefer, Record, code_briefing


def build() -> Briefer:
    from contracts import registry
    return Briefer(registry.rails())


__all__ = ["Briefer", "Record", "build", "code_briefing"]
