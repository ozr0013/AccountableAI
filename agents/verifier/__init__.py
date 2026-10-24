"""Omar: verifier. VERIFIER=real loads build() through contracts.registry."""

from .verifier import LLMVerifier


def build() -> LLMVerifier:
    return LLMVerifier()


__all__ = ["LLMVerifier", "build"]
