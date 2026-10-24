"""Rails: deterministic code, no LLM calls. Owner: Aaryan.

contracts/registry.py loads this when RAILS=real.
"""

from .real import RealRails


def build() -> RealRails:
    return RealRails()


__all__ = ["RealRails", "build"]
