"""Shared guardrail constants. Additive only."""

# Stems, matched case-insensitively against alert text. Templates top out at "unexplained".
BANNED_TERMS = ("fraud", "misuse", "stole", "steal", "theft", "embezzle", "misappropriat", "scam")
