"""Kanishka: payment trail. Deterministic: the winner comes from rails' source-trust rules."""

from .rules import build_trail

__all__ = ["build_trail"]
