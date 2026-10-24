"""Never accuse. Templates and alerts top out at "unexplained"."""

from __future__ import annotations

import re

BANNED_TERMS = (
    "fraud", "misuse", "stole", "stolen", "steal", "theft", "embezzle", "misappropriat",
    "scam", "liar", "lied", "crook", "criminal",
)
_PATTERN = re.compile("|".join(BANNED_TERMS), re.IGNORECASE)


def banned_terms_in(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in _PATTERN.finditer(text or "")})
