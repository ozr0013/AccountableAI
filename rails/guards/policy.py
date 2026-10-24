"""Policy gate (placeholder mirroring the stand-in until the guardrails milestone)."""


def policy_check(action: str, actor: str, payload: dict) -> tuple[bool, str]:
    if action in ("move_money", "send_unapproved"):
        return False, f"{action} is never allowed"
    if action == "approve" and payload.get("proposed_by") == actor:
        return False, "proposer cannot approve"
    return True, "ok"
