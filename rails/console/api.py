"""HTTP API for the Judge Console UI:  uvicorn rails.console.api:app --port 8001

GET  /presets                       -> presets with parameter help and defaults
POST /presets/{id}  {"params": {}}  -> apply a preset
POST /text          {"text": "...", "manager_id": "keyway"}
Both POSTs return {summary, event_ids, events, affected}. Bad input is 422 with suggestions.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .console import ConsoleError, ConsoleResult, JudgeConsole

app = FastAPI(title="Judge Console")
_console: JudgeConsole | None = None


def console() -> JudgeConsole:
    global _console
    if _console is None:
        from ..real import RealRails
        _console = JudgeConsole(RealRails())
    return _console


class PresetBody(BaseModel):
    params: dict = Field(default_factory=dict)


class TextBody(BaseModel):
    text: str
    manager_id: str | None = None


def _out(r: ConsoleResult) -> dict:
    return {"summary": r.summary, "event_ids": r.event_ids, "affected": r.affected,
            "events": [e.model_dump(mode="json") for e in r.events]}


def _fail(e: ConsoleError):
    raise HTTPException(422, {"error": str(e), "suggestions": e.suggestions})


@app.get("/presets")
def presets():
    return [asdict(p) for p in console().presets()]


@app.post("/presets/{preset_id}")
def apply_preset(preset_id: str, body: PresetBody):
    try:
        return _out(console().apply_preset(preset_id, body.params))
    except ConsoleError as e:
        _fail(e)


@app.post("/text")
def apply_text(body: TextBody):
    try:
        return _out(console().apply_text(body.text, body.manager_id))
    except ConsoleError as e:
        _fail(e)
