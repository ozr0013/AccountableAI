"""Bounded restart context with source citations and reproducible manifests."""
import json
from hashlib import sha256

from agents.skill_registry import load_skill, validate_override


def build_context(case, observations: list, max_chars: int = 16000, skill_override=None, skill_overrides=None) -> dict:
    if max_chars < 2000:
        raise ValueError('Context budget too small')
    skill = validate_override(skill_override) if skill_override is not None else load_skill('case-investigation')
    overlays = skill_overrides or {}
    skills = [skill] + [validate_override(overlays[name]) if overlays.get(name) else load_skill(name)
                        for name in ('agreement', 'chasing', 'payment_review', 'escalation')]
    # Summaries are hypotheses; authoritative facts are retrieved through tools.
    body = {'case': case.model_dump(mode='json'), 'recent_observations': [],
            'guidance': '\n'.join(s['text'] for s in skills), 'summary_is_authoritative': False}
    body['case']['summary'] = body['case'].get('summary', '')[:2000]
    body['case']['plan'] = [str(x)[:250] for x in case.plan[:6]]
    body['case']['open_questions'] = [str(x)[:250] for x in case.open_questions[:6]]
    # Full memory stays in PostgreSQL and remains available through read case.
    body['case']['last_observation'] = {'evidence': case.last_observation.get('evidence'),
                                      'source_ids': case.last_observation.get('source_ids', [])[:10]}

    for item in reversed(observations[-12:]):
        candidate = [item] + body['recent_observations']
        if len(json.dumps({**body, 'recent_observations': candidate}, default=str)) > max_chars:
            break
        body['recent_observations'] = candidate
    text = json.dumps(body, default=str, sort_keys=True)
    if len(text) > max_chars:
        raise ValueError('Case context exceeds budget')
    return {'text': text, 'manifest': {'version': 1, 'sha256': sha256(text.encode()).hexdigest(),
            'characters': len(text), 'token_budget_estimate': (len(text) + 3) // 4,
            'skills': [{k: s[k] for k in ('name', 'version', 'sha256')} for s in skills]}}
