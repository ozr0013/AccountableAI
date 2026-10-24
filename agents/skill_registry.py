"""Reviewed local guidance; skill text never grants tool permissions."""
from hashlib import sha256
from pathlib import Path

CAPABILITIES = frozenset({'read', 'request', 'wait', 'escalate', 'resolve', 'analyze', 'update_plan'})
ROOT = Path(__file__).with_name('skills')
ALLOWLIST = {name: ('1', CAPABILITIES) for name in
             ('case-investigation', 'agreement', 'chasing', 'payment_review', 'escalation')}


def load_skill(name: str) -> dict:
    if name not in ALLOWLIST:
        raise ValueError('Unknown skill')
    version, capabilities = ALLOWLIST[name]
    if not capabilities <= CAPABILITIES:
        raise ValueError('Unknown capability')
    path = ROOT / f'{name}.md' if name == 'case-investigation' else ROOT / name / 'SKILL.md'
    text = path.read_text()
    return {'name': name, 'version': version, 'sha256': sha256(text.encode()).hexdigest(),
            'capabilities': sorted(capabilities), 'text': text}


def validate_override(skill: dict) -> dict:
    if not isinstance(skill, dict) or not isinstance(skill.get('text'), str):
        raise TypeError('Invalid skill override')
    if not set(skill.get('capabilities', [])) <= CAPABILITIES:
        raise ValueError('Unknown capability')
    if len(skill['text']) > 6000 or not skill.get('version'):
        raise ValueError('Invalid skill version or size')
    return {**skill, 'name': str(skill.get('name', 'reviewed-override')),
            'sha256': sha256(skill['text'].encode()).hexdigest()}
