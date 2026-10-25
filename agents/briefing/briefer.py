"""Briefing agent: "What's open with Keyway?" answered from the workflow's own record, cited.

Two layers, same output (contracts.models.Briefing):
  * code briefing: built straight from open alerts, past rulings and reminders. Free, always
    available, every claim carries the alert's own citations. Also the fallback.
  * model briefing: one structured model call (Gemini when GEMINI_API_KEY is set, else Claude)
    that writes a short answer over the same record.
    Code checks every claim: it must cite documents the model was shown, use no accusatory
    wording, and the answer itself must pass the wording check. Claims that fail are dropped;
    if the answer fails, or the call fails, the code briefing is returned instead.
"""

from __future__ import annotations

import json
import logging
import os
import re

import anthropic
from pydantic import BaseModel

from contracts.models import Alert, AlertKind, Briefing, Citation, Claim, Document
from contracts.steps import Rails

from ..verifier.rules import find_banned

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5-5"     # README: Sonnet for the briefing agent
# Gemini's free tier, tried in order. "-latest" aliases follow Google's current versions (pinned
# versions get retired and 404); Flash-Lite answers fastest when the free tier is busy.
GEMINI_MODELS = ("gemini-flash-lite-latest", "gemini-flash-latest")
GEMINI_TIMEOUT_MS = 20_000
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1"}

SYSTEM = """You brief Oakline Homes' controller on a property manager that Oakline funds to pay \
vendors. You are given the system's current record: open alerts (each with its cited source \
documents), past rulings (alerts that were withdrawn, with the reason), reminders sent to the \
manager, the funded-versus-paid reconciliation, and the source documents themselves.

Answer the controller's question using only that record.
- Every claim must cite one or more source documents by exact id, taken from the documents given.
- Lead with what needs the controller's attention: unexplained unpaid vendor money and its total.
- Separate items that are NOT the manager's fault (Oakline's own pending approval, missing vendor \
paperwork, a safety hold on a bank-detail change) from items that are.
- Mention relevant past rulings, e.g. an alert withdrawn after a corrected fact.
- Neutral wording only. Never accuse: no "fraud", "misuse", "theft" or similar. The strongest \
word for an unexplained item is "unexplained".
- The answer is 2-5 short sentences and states no fact that isn't in your claims."""

_NOT_MANAGER = {AlertKind.owner_pending_approval: "Oakline's own approval or funding is pending",
                AlertKind.paperwork_request: "waiting on vendor paperwork",
                AlertKind.bank_detail_change: "payment held for a vendor bank-detail check"}


class _ClaimOut(BaseModel):
    text: str
    source_ids: list[str]


class _BriefingOut(BaseModel):
    answer: str
    claims: list[_ClaimOut]
    prior_rulings: list[str]


def partial_answer(text: str) -> str | None:
    """Decode the answer string while the structured JSON is still arriving."""
    match = re.search(r'"answer"\s*:\s*"((?:[^"\\]|\\.)*)', text)
    if match is None:
        return None
    try:
        return json.loads('"' + match.group(1) + '"')
    except ValueError:  # An escape (e.g. a Unicode character) spans chunks.
        return None


def _cites(alert: Alert) -> list[Citation]:
    seen: dict[str, Citation] = {}
    for cl in alert.draft.claims:
        for c in cl.citations:
            seen.setdefault(c.source_id, c)
    return list(seen.values())


class Record:
    """Everything the briefing may say, for one manager."""

    def __init__(self, rails: Rails, manager_id: str):
        from workflow.book import WorkflowBook
        book = WorkflowBook.load(rails)
        self.manager_id = manager_id
        self.open = book.open_alerts(manager_id)
        self.withdrawn = sorted((a for a in book.alerts.values()
                                 if a.draft.manager_id == manager_id and a.state == "withdrawn"),
                                key=lambda a: a.draft.id)
        self.chases = book.chases.get(manager_id, [])
        self.recon = rails.reconcile(manager_id)
        cited = {c.source_id for a in self.open + self.withdrawn for c in _cites(a)}
        self.docs: list[Document] = [d for d in rails.documents(manager_id) if d.id in cited]

    @property
    def unexplained(self) -> list[Alert]:
        return [a for a in self.open if a.draft.kind == AlertKind.unexplained_unpaid and a.draft.amount > 0]

    def render(self) -> str:
        def alert_line(a: Alert) -> str:
            ids = ", ".join(c.source_id for c in _cites(a))
            return f"- [{a.draft.kind.value}] {a.draft.headline} (${a.draft.amount:,.2f}) [sources: {ids}]"
        r = self.recon
        alerts = [alert_line(a) for a in self.open] or ["- none"]
        rulings = [f"- {a.draft.headline}: withdrawn because {a.withdrawn_reason}"
                   for a in self.withdrawn] or ["- none"]
        chases = [f"- {c.sent_at.date() if c.sent_at else '?'} {c.level}: {', '.join(c.invoice_ids)}"
                  for c in self.chases] or ["- none"]
        parts = [f"Manager: {self.manager_id}",
                 f"Reconciliation: funded ${r['funded']:,.2f}, paid to vendors ${r['paid']:,.2f}, "
                 f"held ${r['held']:,.2f}, still owed ${r['owed']:,.2f}",
                 "", "Open alerts:", *alerts,
                 "", "Past rulings (withdrawn alerts):", *rulings,
                 "", "Reminders sent:", *chases,
                 "", "Source documents:"]
        for d in sorted(self.docs, key=lambda d: (str(d.dated or ""), d.id)):
            parts.append(f'<document id="{d.id}" kind="{d.kind.value}" dated="{d.dated or ""}">'
                         f"{d.text}</document>")
        return "\n".join(parts)


def code_briefing(rec: Record, question: str) -> Briefing:
    """Deterministic briefing: one cited claim per open alert, past rulings verbatim."""
    unexplained = rec.unexplained
    total = sum(a.draft.amount for a in unexplained)
    other = [a for a in rec.open if a.draft.kind in _NOT_MANAGER]
    pattern = [a for a in rec.open if a.draft.kind == AlertKind.recurring_pattern]
    dup = [a for a in rec.open if a.draft.kind == AlertKind.duplicate_billing]
    sentences = [f"{rec.manager_id.title()} has {len(unexplained)} unexplained unpaid vendor "
                 f"item(s) totaling ${total:,.2f}."]
    if pattern:
        sentences.append(pattern[0].draft.headline.rstrip(".") + ".")
    if dup:
        sentences.append(f"{len(dup)} duplicate billing item(s) flagged.")
    if other:
        sentences.append(f"{len(other)} open item(s) are not the manager's fault: "
                         + "; ".join(f"{a.draft.invoice_ids[0] if a.draft.invoice_ids else ''} "
                                     f"({_NOT_MANAGER[a.draft.kind]})" for a in other) + ".")
    claims = [Claim(text=a.draft.headline, citations=_cites(a)) for a in rec.open]
    rulings = [f"{a.draft.headline} -> withdrawn: {a.withdrawn_reason}" for a in rec.withdrawn]
    return Briefing(manager_id=rec.manager_id, question=question, answer=" ".join(sentences),
                    claims=claims, prior_rulings=rulings)


def _prompt(rec: Record, question: str) -> str:
    return f"<record>\n{rec.render()}\n</record>\n\nController's question: {question}"


class GeminiBriefer:
    """The same structured call on Gemini (google-genai). Returns (answer, meta) or raises."""

    def __init__(self, client=None, model: str | None = None):
        from google import genai
        from google.genai import types
        # One attempt per model with a hard timeout: a busy free tier must not hang the Ask page.
        self.client = client or genai.Client(
            api_key=os.environ["GEMINI_API_KEY"],
            http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=1)))
        chosen = model or os.getenv("GEMINI_MODEL")
        self.models = (chosen,) if chosen else GEMINI_MODELS

    def __call__(self, rec: Record, question: str) -> tuple[_BriefingOut | None, dict]:
        import httpx
        from google.genai import errors
        for i, model in enumerate(self.models):
            try:
                return self._one(model, rec, question)
            except (errors.APIError, httpx.HTTPError) as e:     # busy (503), timeout (504), network
                if i == len(self.models) - 1:
                    raise
                log.warning("Gemini %s failed (%s), trying %s", model, type(e).__name__, self.models[i + 1])
        raise AssertionError("unreachable")

    def _one(self, model: str, rec: Record, question: str) -> tuple[_BriefingOut | None, dict]:
        from google.genai import types
        resp = self.client.models.generate_content(
            model=model, contents=_prompt(rec, question),
            config=types.GenerateContentConfig(system_instruction=SYSTEM, response_mime_type="application/json",
                                               response_schema=_BriefingOut))
        out = resp.parsed if isinstance(resp.parsed, _BriefingOut) else None
        u = resp.usage_metadata
        return out, {"model": model, "provider": "gemini",
                     "input_tokens": getattr(u, "prompt_token_count", 0) or 0,
                     "output_tokens": getattr(u, "candidates_token_count", 0) or 0}


class Briefer:
    """contracts.steps.Briefer. Uses the model when a client is available, else the code briefing.
    With GEMINI_API_KEY set, the model is Gemini; otherwise Claude."""

    def __init__(self, rails: Rails, client: anthropic.Anthropic | None = None,
                 model: str | None = None, use_model: bool = True, gemini: GeminiBriefer | None = None,
                 provider: str | None = None):
        self.rails = rails
        self.model = model or os.getenv("BRIEFING_MODEL", DEFAULT_MODEL)
        self.client = client
        self.gemini = gemini
        if gemini is None and client is None and use_model and (provider or os.getenv("BRIEFING_PROVIDER")) == "anthropic":
            self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"),
                                              timeout=30.0, max_retries=0)
            self.client.auth_token = None  # Use API key authentication for this provider.
        elif gemini is None and client is None and use_model and os.getenv("GEMINI_API_KEY"):
            self.gemini = GeminiBriefer()
        elif client is None and use_model and (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
            self.client = anthropic.Anthropic()
        self.last: dict = {}             # how the last briefing was made, for logs and evals

    def brief(self, manager_id: str, question: str) -> Briefing:
        rec = Record(self.rails, manager_id)
        fallback = code_briefing(rec, question)
        if self.gemini is not None:
            return self._brief_gemini(rec, question, fallback)
        if self.client is None:
            self.last = {"mode": "code"}
            return fallback
        try:
            resp = self._call(rec, question)
        except anthropic.APIError as e:
            log.warning("briefing model call failed: %s", e)
            self.last = {"mode": "code", "why": f"model unavailable: {type(e).__name__}"}
            return fallback
        out = resp.parsed_output
        if resp.stop_reason == "refusal" or out is None:
            self.last = {"mode": "code", "why": f"no model answer ({resp.stop_reason})"}
            return fallback
        meta = {"provider": "anthropic", "model": resp.model, "input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
        return self._check(rec, question, out, fallback, meta)

    def stream_brief(self, manager_id: str, question: str):
        yield from self._stream_anthropic(manager_id, question)

    def _stream_anthropic(self, manager_id: str, question: str):
        if self.client is None:
            yield {"event": "error", "message": "Anthropic is not configured on the server."}
            return
        rec = Record(self.rails, manager_id)
        model = os.getenv("ANTHROPIC_BRIEFING_MODEL", self.model)
        yield {"event": "status", "message": "Claude is reading the record."}
        try:
            with self.client.messages.stream(
                model=model, max_tokens=4096,
                system=SYSTEM + '\nKeep the answer to 2-3 sentences, at most 4 claims and 2 prior rulings. Return only JSON, without Markdown fences, matching this schema: ' + json.dumps(_BriefingOut.model_json_schema()),
                messages=[{"role": "user", "content": _prompt(rec, question)}]) as stream:
                text, previous = "", ""
                for delta in stream.text_stream:
                    text += delta
                    answer = partial_answer(text)
                    if answer is not None and answer != previous:
                        previous = answer
                        yield {"event": "answer", "text": answer}
                response = stream.get_final_message()
            meta = {"provider": "anthropic", "model": model,
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens}
            yield {"event": "usage", **meta}
            if response.stop_reason != "end_turn":
                yield {"event": "error", "message": f"Claude did not return a complete answer ({response.stop_reason})."}
                return
            out = _BriefingOut.model_validate_json(text)
            result = self._check(rec, question, out, code_briefing(rec, question), meta)
            if self.last.get("mode") != "model":
                yield {"event": "error", "message": "The generated answer did not pass citation and wording checks."}
                return
            yield {"event": "done", "briefing": {**result.model_dump(mode="json"), "meta": self.last}}
        except (anthropic.APIError, ValueError) as exc:
            yield {"event": "error", "message": f"Anthropic could not complete the request ({type(exc).__name__})."}

    def _brief_gemini(self, rec: Record, question: str, fallback: Briefing) -> Briefing:
        import httpx
        from google.genai import errors
        try:
            out, meta = self.gemini(rec, question)
        except (errors.APIError, httpx.HTTPError, ValueError) as e:   # quota, busy, timeout, bad JSON
            log.warning("Gemini briefing call failed: %s", e)
            self.last = {"mode": "code", "why": f"Gemini unavailable: {type(e).__name__}"}
            return fallback
        if out is None:
            self.last = {"mode": "code", "why": "no Gemini answer", **meta}
            return fallback
        return self._check(rec, question, out, fallback, meta)

    def _call(self, rec: Record, question: str):
        extra = {}
        if self.model in _FALLBACK_MODELS:
            extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        return self.client.beta.messages.parse(
            model=self.model, max_tokens=16000, system=SYSTEM,
            messages=[{"role": "user", "content": _prompt(rec, question)}],
            output_format=_BriefingOut, **extra)

    def _check(self, rec: Record, question: str, out: _BriefingOut, fallback: Briefing, meta: dict) -> Briefing:
        kinds = {d.id: d.kind for d in rec.docs}
        claims, dropped = [], []
        for c in out.claims:
            ids = [i for i in c.source_ids if i in kinds]
            if not ids or len(ids) < len(c.source_ids) or find_banned(c.text):
                dropped.append(c.text)
                continue
            claims.append(Claim(text=c.text, citations=[Citation(source_id=i, kind=kinds[i]) for i in ids]))
        meta = {**meta, "dropped_claims": dropped}
        if find_banned(out.answer) or not claims:
            self.last = {"mode": "code", "why": "model answer failed the checks", **meta}
            return fallback
        self.last = {"mode": "model", **meta}
        rulings = out.prior_rulings or fallback.prior_rulings
        return Briefing(manager_id=rec.manager_id, question=question, answer=out.answer,
                        claims=claims, prior_rulings=rulings)
