"""Closed tool vocabulary and typed arguments for the case supervisor."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Args(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Read(Args):
    kind: Literal['case', 'terms', 'invoice', 'payment', 'evidence', 'decisions']
    query: str = Field(default='', max_length=200)
    limit: int = Field(default=10, ge=1, le=20)


class Request(Args):
    kind: Literal['statement', 'payment_proof', 'approval', 'notification']
    reason: str = Field(min_length=1, max_length=1000)


class Wait(Args):
    until: datetime
    reason: str = Field(min_length=1, max_length=1000)


class Finish(Args):
    reason: str = Field(min_length=1, max_length=1000)


class Plan(Args):
    summary: str = Field(max_length=2000)
    steps: list[str] = Field(max_length=12)
    open_questions: list[str] = Field(max_length=12)
    source_ids: list[str] = Field(min_length=1, max_length=30)


class Analyze(Args):
    kind: Literal['trail', 'cause', 'payment_review']


ARGUMENTS = {'read': Read, 'request': Request, 'wait': Wait,
             'escalate': Finish, 'resolve': Finish, 'update_plan': Plan, 'analyze': Analyze}
TOOLS = [{'name': name, 'description': {
    'update_plan': 'Save a cited summary, plan and open questions to durable case memory.',
    'analyze': 'Delegate scoped trail, cause or payment review to deterministic specialists.',
    'read': 'Retrieve scoped case facts with source IDs; payment is deterministic bank proof.',
    'request': 'Request evidence, a human approval or internal notification; never sends mail.',
    'wait': 'Persist a future wake while waiting for evidence or human action.',
    'escalate': 'Escalate a conflict to human review.',
    'resolve': 'Close only with valid payment, matching statement and no anomalies.'}[name],
    'input_schema': schema.model_json_schema()} for name, schema in ARGUMENTS.items()]
