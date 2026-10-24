"""Outcomes judge both models and code. This module never moves money.

Prediction settlement only labels an error when evidence was effective on/before the
prediction. Payments after a reminder resolve work; they are not retroactive errors.
A model disagreement blocks the proposal; it does not establish which side was wrong.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from agents.intake.engine import stamp
from contracts.models import Citation, Event, SourceKind
from rails.guards import policy_check
from rails.guards.accounting import accounting_check
from rails.serialization import digest


class InvariantViolation(RuntimeError):
    pass


class Audit:
    def __init__(self, rails, regression_dir=None):
        self.rails = rails
        self.regression_dir = Path(regression_dir) if regression_dir else None
        self.halted = any(e.type == "processing_stopped" for e in rails.events())

    def emit(self, kind, key, day, invoice_id=None, payload=None, source=None, supersedes=None):
        e = Event(id=f"audit-{kind}-{digest(key)[:24]}", type=kind, manager_id="keyway",
                  invoice_id=invoice_id, valid_from=stamp(day), learned_at=stamp(day),
                  payload=payload or {}, source=source, supersedes=supersedes)
        self.rails.append_event(e)
        return e

    def predictions(self):
        replaced = {e.supersedes for e in self.rails.events() if e.supersedes}
        settled = {e.payload["prediction_id"] for e in self.rails.events()
                   if e.type == "prediction_settled"}
        latest = {}
        for e in self.rails.events():
            if e.type == "prediction_recorded" and e.id not in settled and e.id not in replaced:
                latest[e.invoice_id, e.payload["target"]] = e
        return latest

    def predict(self, invoice_id, target, value, day, citations, action_type="assess", context=None):
        if target not in ("alert", "cause") or not citations:
            raise ValueError("prediction needs alert/cause and evidence")
        source = citations[0]
        previous = self.predictions().get((invoice_id, target))
        if previous and previous.payload["value"] == value and previous.payload.get("context") == (context or {}):
            return previous
        return self.emit("prediction_recorded", [invoice_id, target, value, context or {}, str(day)], day,
                         invoice_id, {"target": target, "value": value,
                         "action_type": action_type, "context": context or {}, "citations": [c.model_dump(mode="json") for c in citations]},
                         source=source, supersedes=previous.id if previous else None)

    def settle(self, invoice_id, observed, effective_on, day, source, caused_by="late_evidence"):
        """observed is an independently checked outcome {alert: bool, cause: str}."""
        corrections = []
        for (iid, target), pred in list(self.predictions().items()):
            if iid != invoice_id or target not in observed:
                continue
            # An outcome after the original prediction is a state change, not a mistake.
            retroactive = effective_on <= pred.valid_from.date()
            wrong = retroactive and pred.payload["value"] != observed[target]
            if not retroactive and pred.payload["value"] != observed[target]:
                continue
            self.emit("prediction_settled", [pred.id, observed[target]], day, iid,
                      {"prediction_id": pred.id, "correct": not wrong, "outcome": observed[target],
                       "action_type": pred.payload["action_type"], "effective_on": str(effective_on)}, source)
            if wrong:
                corrections.append(self.mistake("outcome", iid, day, source,
                    {"prediction_id": pred.id, "target": target, "predicted": pred.payload["value"],
                     "observed": observed[target], "effective_on": str(effective_on),
                     "prediction_on": str(pred.valid_from.date()), "caused_by": caused_by},
                    corrected_value=observed[target]))
        return corrections

    def mistake(self, signal, invoice_id, day, source, evidence, corrected_value=None):
        key = [signal, invoice_id, evidence]
        miss = self.emit("mistake_detected", key, day, invoice_id,
                         {"signal": signal, "evidence": evidence, "confirmed_error": signal in ("outcome", "invariant", "human_override")}, source)
        affected = self.rails.dependents_of(f"invoice:{invoice_id}.payment") if invoice_id else []
        if invoice_id and not affected:
            affected = [invoice_id]
        self.emit("recheck_requested", miss.id, day, invoice_id,
                  {"mistake_id": miss.id, "affected": affected, "reason": signal}, source)
        if corrected_value is not None:
            operation = "withdraw" if corrected_value is False else "reopen" if corrected_value is True else "correct_cause"
            self.emit("correction_recorded", miss.id, day, invoice_id,
                      {"mistake_id": miss.id, "operation": operation, "value": corrected_value,
                       "affected": affected, "reason": evidence, "requires_recheck": True}, source)
        self.emit("controller_notified", miss.id, day, invoice_id,
                  {"mistake_id": miss.id, "simulated": True, "delivery": "recorded_only"}, source)
        if evidence.get("caused_by") == "rule":
            self.emit("rule_change_proposed", miss.id, day, invoice_id,
                      {"mistake_id": miss.id, "status": "awaiting_human", "evidence": evidence}, source)
        if self.regression_dir:
            self.regression_dir.mkdir(parents=True, exist_ok=True)
            path = self.regression_dir / f"{miss.id}.json"
            # Append-only, durable regression fixtures; replay cannot replace old evidence.
            if not path.exists():
                record = {"mistake": miss.model_dump(mode="json"),
                          "events": [e.model_dump(mode="json") for e in self.rails.events()
                                     if e.id != miss.id],
                          "expected": {"value": corrected_value, "affected": affected}}
                with path.open("x") as f:
                    json.dump(record, f, sort_keys=True, indent=2)
        return miss

    def disagreement(self, invoice_id, rules, model, day, source, target="cause"):
        if rules == model:
            return False
        self.mistake("disagreement", invoice_id, day, source,
                     {"target": target, "rules": rules, "model": model})
        return True

    def override(self, invoice_id, decision, day, source, actor, proposed_by="case_agent"):
        ok, why = policy_check("approve", actor, {"proposed_by": proposed_by})
        if not ok:
            raise ValueError(why)
        e = self.emit("human_override", [invoice_id, decision, str(day), source.source_id], day,
                      invoice_id, {"decision": decision, "actor": actor, "human_touch": True}, source)
        self.mistake("human_override", invoice_id, day, source,
                     {"override_id": e.id, "decision": decision, "caused_by": decision.get("caused_by", "human_override")}, corrected_value=decision.get("alert"))
        return e

    def check(self, day):
        issues = [i.__dict__ for i in accounting_check(self.rails.events())]
        seen = set()
        states = {}
        legal = {None: {"open"}, "open": {"withdrawn", "resolved"}, "withdrawn": {"open"}, "resolved": {"open"}}
        for e in self.rails.events():
            if e.type == "action_issued":
                key = e.payload["action_key"]
                if key in seen:
                    issues.append({"check": "duplicate_action", "ref": e.id})
                seen.add(key)
            elif e.type == "alert_transition":
                old = states.get(e.invoice_id)
                new = e.payload["state"]
                if new not in legal.get(old, set()):
                    issues.append({"check": "illegal_transition", "ref": e.id, "from": old, "to": new})
                states[e.invoice_id] = new
        if issues:
            source = next((e.source for e in reversed(self.rails.events()) if e.source),
                          Citation(source_id="audit-invariant", kind=SourceKind.controller))
            self.mistake("invariant", None, day, source, {"issues": issues, "caused_by": "rule"})
            self.emit("processing_stopped", issues, day, payload={"issues": issues}, source=source)
            self.halted = True
            raise InvariantViolation(str(issues))
        if self.halted:
            raise InvariantViolation("processing remains stopped pending human repair")

    def track_record(self):
        result = defaultdict(lambda: {"settled": 0, "correct": 0})
        for e in self.rails.events():
            if e.type in ("prediction_settled", "action_outcome"):
                r = result[e.payload["action_type"]]
                r["settled"] += 1
                r["correct"] += int(e.payload["correct"])
        return dict(result)

    def autonomy(self, action_type):
        r = self.track_record().get(action_type, {"settled": 0, "correct": 0})
        n, k = r["settled"], r["correct"]
        # Wilson lower 95% bound: a short lucky streak cannot earn expanded reminders.
        z = 1.96
        lower = ((k/n + z*z/(2*n) - z*((k/n*(1-k/n)+z*z/(4*n))/n)**.5) /
                 (1+z*z/n)) if n else 0
        return {**r, "lower_95": lower, "expanded": action_type == "reminder" and n >= 40 and lower >= .9}
