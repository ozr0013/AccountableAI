"""Auditable feedback and human-reviewed runtime guidance promotions.

Replay results are explicit controller-attested evaluations, never inferred from a model's
claim. Skill text remains data and cannot alter the runtime capability registry.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256

from agents.skill_registry import ALLOWLIST, load_skill
from rails.cases import identity
from workflow.cases import CaseStore
from workflow.runtime_lock import runtime_lock


class FeedbackStore:
    def __init__(self, rails):
        self.rails = rails
        self.cases = CaseStore(rails)

    def _rows(self, kind):
        return [deepcopy(e.payload["record"]) for e in self.rails.events() if e.type == kind]

    def list(self, case_id=None):
        return {
            kind: [
                r
                for r in self._rows("skill_" + kind)
                if case_id is None or r.get("case_id") == case_id
            ]
            for kind in ("feedback", "candidate", "evaluation", "promotion", "rollback")
        }

    def _get(self, kind, record_id):
        for row in self._rows("skill_" + kind):
            if row["id"] == record_id:
                return row
        raise KeyError(record_id)

    def _save(self, kind, case_id, body, now, key):
        record = {"id": "skill_" + identity(kind, case_id, key), "case_id": case_id, **body}
        event = self.cases.record(
            case_id, "skill_" + kind, {"record": record}, now, "skill:" + kind + ":" + key
        )
        return deepcopy(event.payload["record"])

    @staticmethod
    def _controller(principal):
        if principal.role != "controller" or not principal.id:
            raise PermissionError("human controller required")

    def feedback(self, case_id, decision_id, text, now, key):
        self.cases.get(case_id)
        decision = next((e for e in self.rails.events() if e.id == decision_id), None)
        if (
            not decision
            or decision.payload.get("case_id") != case_id
            or not decision.type.startswith("supervisor_")
        ):
            raise ValueError("decision must be an existing supervisor event in the selected case")
        if not isinstance(text, str) or not text.strip() or len(text) > 6000:
            raise ValueError("feedback must contain 1 to 6000 characters")
        return self._save("feedback", case_id, {"decision_id": decision_id, "text": text}, now, key)

    def candidate(self, feedback_id, skill_name, version, text, capabilities, now, key):
        feedback = self._get("feedback", feedback_id)
        base = load_skill(skill_name)
        if not isinstance(text, str) or not text.strip() or len(text) > 6000:
            raise ValueError("skill text must contain 1 to 6000 characters")
        if (
            not isinstance(version, str)
            or not version.strip()
            or len(version) > 100
            or version == base["version"]
        ):
            raise ValueError("a distinct candidate version is required")
        if (
            not isinstance(capabilities, list)
            or not all(isinstance(c, str) for c in capabilities)
            or not set(capabilities) <= set(ALLOWLIST[skill_name][1])
        ):
            raise ValueError("candidate cannot expand registry capabilities")
        with runtime_lock(self.rails, "skill:" + skill_name):
            prior_id = "skill_" + identity("candidate", feedback["case_id"], key)
            prior = next((r for r in self._rows("skill_candidate") if r["id"] == prior_id), None)
            if prior:
                expected = {
                    "feedback_id": feedback_id,
                    "name": skill_name,
                    "version": version,
                    "text": text,
                    "capabilities": sorted(set(capabilities)),
                }
                if any(prior[k] != v for k, v in expected.items()):
                    raise ValueError("candidate key reused with different content")
                return prior
            if any(
                r["name"] == skill_name and r["version"] == version
                for r in self._rows("skill_candidate")
            ):
                raise ValueError("skill version already binds another candidate")
            active = self.active_override(skill_name) or base
            if version == active["version"]:
                raise ValueError("candidate version is already active")
            return self._save(
                "candidate",
                feedback["case_id"],
                {
                    "feedback_id": feedback_id,
                    "name": skill_name,
                    "version": version,
                    "text": text,
                    "sha256": sha256(text.encode()).hexdigest(),
                    "capabilities": sorted(set(capabilities)),
                    "base_sha256": active["sha256"],
                    "base_version": active["version"],
                },
                now,
                key,
            )

    def evaluate(self, candidate_id, replay_results, now, key):
        candidate = self._get("candidate", candidate_id)
        case = self.cases.get(candidate["case_id"])
        if not isinstance(replay_results, list) or not replay_results:
            raise ValueError("at least one explicit replay result is required")
        checked = []
        seen = set()
        for result in replay_results:
            if (
                not isinstance(result, dict)
                or set(result) != {"case_id", "passed"}
                or not isinstance(result["passed"], bool)
            ):
                raise ValueError("replay result requires case_id and boolean passed")
            replay_case = self.cases.get(result["case_id"])
            if (replay_case.owner_id, replay_case.manager_id) != (case.owner_id, case.manager_id):
                raise ValueError("replay case is outside candidate business scope")
            if replay_case.id in seen:
                raise ValueError("duplicate replay case")
            seen.add(replay_case.id)
            checked.append({**result, "case_version": replay_case.version})
        return self._save(
            "evaluation",
            case.id,
            {
                "candidate_id": candidate_id,
                "candidate_sha256": candidate["sha256"],
                "replay_results": checked,
                "passed": all(r["passed"] for r in checked),
                "source": "controller_attestation",
            },
            now,
            key,
        )

    def promote(self, candidate_id, principal, now, key):
        self._controller(principal)
        candidate = self._get("candidate", candidate_id)
        with runtime_lock(self.rails, "skill:" + candidate["name"]):
            # Same-key retries are accepted before base-version checks.
            record_id = "skill_" + identity("promotion", candidate["case_id"], key)
            prior = next((r for r in self._rows("skill_promotion") if r["id"] == record_id), None)
            if prior:
                if prior["candidate_id"] != candidate_id or prior["promoted_by"] != principal.id:
                    raise ValueError("promotion key reused")
                return prior
            evaluations = [
                r for r in self._rows("skill_evaluation") if r["candidate_id"] == candidate_id
            ]
            if (
                not evaluations
                or not evaluations[-1]["passed"]
                or evaluations[-1].get("source") != "deterministic_native_replay_v1"
                or evaluations[-1]["candidate_sha256"] != candidate["sha256"]
            ):
                raise ValueError("latest evaluation must pass all replay cases for exact candidate")
            for result in evaluations[-1]["replay_results"]:
                if self.cases.get(result["case_id"]).version != result["case_version"]:
                    raise ValueError("replay evaluation is stale; rerun against current cases")
            base = self.active_override(candidate["name"]) or load_skill(candidate["name"])
            if (
                base["sha256"] != candidate["base_sha256"]
                or base["version"] != candidate["base_version"]
            ):
                raise ValueError("candidate base changed; create and evaluate a new candidate")
            return self._save(
                "promotion",
                candidate["case_id"],
                {
                    "candidate_id": candidate_id,
                    "name": candidate["name"],
                    "evaluation_id": evaluations[-1]["id"],
                    "promoted_by": principal.id,
                    "previous_candidate_id": base.get("candidate_id"),
                },
                now,
                key,
            )

    def active_override(self, skill_name):
        load_skill(skill_name)  # rejects unknown names and traversal
        active = None
        for event in self.rails.events():
            if event.type not in ("skill_promotion", "skill_rollback"):
                continue
            record = event.payload["record"]
            if record["name"] == skill_name:
                active = (
                    record.get("candidate_id")
                    if event.type == "skill_promotion"
                    else record.get("restore_candidate_id")
                )
        if active is None:
            return None
        candidate = self._get("candidate", active)
        if not set(candidate["capabilities"]) <= set(ALLOWLIST[skill_name][1]):
            raise ValueError("promoted candidate exceeds current registry")
        if sha256(candidate["text"].encode()).hexdigest() != candidate["sha256"]:
            raise ValueError("candidate hash mismatch")
        return {k: candidate[k] for k in ("name", "version", "text", "sha256", "capabilities")} | {
            "candidate_id": active
        }

    def rollback(self, skill_name, principal, now, key):
        self._controller(principal)
        with runtime_lock(self.rails, "skill:" + skill_name):
            prior = next(
                (
                    r
                    for r in self._rows("skill_rollback")
                    if r["name"] == skill_name and r.get("rollback_key") == key
                ),
                None,
            )
            if prior:
                if prior["rolled_back_by"] != principal.id:
                    raise ValueError("rollback key reused")
                return prior
            active = self.active_override(skill_name)
            if active is None:
                raise ValueError("no active override to roll back")
            candidate = self._get("candidate", active["candidate_id"])
            promotion = next(
                r
                for r in reversed(self._rows("skill_promotion"))
                if r["candidate_id"] == candidate["id"]
            )
            return self._save(
                "rollback",
                candidate["case_id"],
                {
                    "name": skill_name,
                    "candidate_id": candidate["id"],
                    "restore_candidate_id": promotion.get("previous_candidate_id"),
                    "rolled_back_by": principal.id,
                    "rollback_key": key,
                },
                now,
                key,
            )

    def run_evaluation(self, candidate_id, runner=None, case_ids=None, *, now, key):
        """Measure runtime invariants in isolated memory; no LLM semantic-quality claim.

        A trusted Python runner may replace the built-in scripted native client. HTTP callers
        cannot supply code or results. Every run still receives an isolated case snapshot and
        is checked independently for citations, resolution safety, context and side effects.
        """
        from agents.supervisor import Supervisor
        from contracts.models import Document
        from rails.events.ingest import document_event
        from workflow.memrails import MemoryRails

        candidate = self._get("candidate", candidate_id)
        source_case = self.cases.get(candidate["case_id"])
        ids = case_ids or [source_case.id]
        if not isinstance(ids, list) or not ids or len(ids) > 20 or len(set(ids)) != len(ids):
            raise ValueError("provide 1 to 20 distinct replay case IDs")
        results = []
        override = {k: candidate[k] for k in ("name", "version", "text", "sha256", "capabilities")}
        for case_id in ids:
            case = self.cases.get(case_id)
            if (case.owner_id, case.manager_id) != (source_case.owner_id, source_case.manager_id):
                raise ValueError("replay case is outside candidate business scope")
            original = Supervisor(self.rails)
            line = next(
                (
                    l.model_copy(deep=True)
                    for l in self.rails.ledger(case.manager_id)
                    if l.invoice_id == case.invoice_id and l.vendor_id == case.vendor_id
                ),
                None,
            )
            if line is None:
                raise ValueError("replay requires an existing case-scoped ledger invoice")

            class SnapshotRails(MemoryRails):
                def __init__(self, snapshot_line):
                    super().__init__()
                    self.snapshot_line = snapshot_line.model_copy(deep=True)

                def ledger(self, manager_id):
                    return (
                        [self.snapshot_line.model_copy(deep=True)]
                        if manager_id == self.snapshot_line.manager_id
                        else []
                    )

            sandbox = SnapshotRails(line)
            for data in original._docs(case, now):
                sandbox.append_event(document_event(Document.model_validate(data), now))
            for event in self.rails.events(case.manager_id):
                if event.valid_from > now or event.learned_at > now:
                    continue
                if (
                    event.type in ("terms_confirmed", "agreement_confirmed")
                    and event.payload.get("vendor_id") in (None, case.vendor_id)
                    or event.type in ("payment_reviewed", "human_anomaly_acknowledged")
                    and event.invoice_id == case.invoice_id
                ):
                    sandbox.append_event(event.model_copy(deep=True))
            replay_case = CaseStore(sandbox).ensure(line, now, owner_id=case.owner_id)
            source_snapshot = [e.model_dump_json() for e in sandbox.events()]
            source_count = len(source_snapshot)
            try:
                (runner or self._scripted_replay)(sandbox, replay_case, deepcopy(override), now)
                proof = Supervisor(sandbox)._proof(replay_case, now)
                final = CaseStore(sandbox).get(replay_case.id)
                events = sandbox.events()
                contexts = [e for e in events if e.type == "supervisor_context"]
                tools = [e for e in events if e.type == "supervisor_tool_result"]
                scoped_sources = {d["id"] for d in Supervisor(sandbox)._docs(final, now)} | {
                    e.id for e in events
                }
                citations = final.last_observation.get("source_ids", [])
                can_resolve = (
                    proof["valid_bank_proof"]
                    and proof["statement_matches"]
                    and not proof.get("resolution_blockers")
                    and (not proof["anomalies"] or proof["anomalies_acknowledged"])
                )
                checks = {
                    "ledger_snapshot_unchanged": sandbox.ledger(case.manager_id) == [line],
                    "source_snapshot_unchanged": [
                        e.model_dump_json() for e in events[:source_count]
                    ]
                    == source_snapshot
                    and not any(
                        e.type
                        in (
                            "document_ingested",
                            "terms_confirmed",
                            "agreement_confirmed",
                            "payment_reviewed",
                            "human_anomaly_acknowledged",
                        )
                        for e in events[source_count:]
                    ),
                    "candidate_context": bool(contexts)
                    and any(
                        s.get("sha256") == candidate["sha256"]
                        and s.get("version") == candidate["version"]
                        for s in contexts[-1].payload.get("skills", [])
                    ),
                    "native_tools_ran": bool(tools),
                    "bad_citation_rejected": any(
                        e.payload.get("tool") == "update_plan"
                        and e.payload.get("result", {}).get("error")
                        == "Citations must refer to existing case-scoped sources"
                        for e in tools
                    ),
                    "valid_citations_saved": bool(citations) and set(citations) <= scoped_sources,
                    "resolution_safe": final.state != "resolved" or bool(can_resolve),
                    "future_wake_or_resolved": final.state == "resolved"
                    or final.next_wake_at is not None
                    and final.next_wake_at > now,
                    "no_external_side_effects": not any(
                        e.type in ("outbox_changed", "human_approval_decided", "parties_notified")
                        for e in events
                    ),
                }
                result = {
                    "case_id": case_id,
                    "case_version": case.version,
                    "checks": checks,
                    "passed": all(checks.values()),
                    "tool_calls": len(tools),
                }
            except Exception as exc:  # noqa: BLE001 - persist failures from trusted replay runners
                result = {
                    "case_id": case_id,
                    "case_version": case.version,
                    "checks": {},
                    "passed": False,
                    "error": type(exc).__name__,
                }
            results.append(result)
        return self._save(
            "evaluation",
            source_case.id,
            {
                "candidate_id": candidate_id,
                "candidate_sha256": candidate["sha256"],
                "replay_results": results,
                "passed": all(r["passed"] for r in results),
                "source": "deterministic_native_replay_v1",
                "limitation": "Scripted native runtime invariants; does not measure live model semantic quality.",
            },
            now,
            key,
        )

    @staticmethod
    def _scripted_replay(sandbox, case, override, now):
        from datetime import timedelta
        from types import SimpleNamespace

        from agents.supervisor import Supervisor

        source = next(e.id for e in sandbox.events() if e.type == "case_changed")
        script = [
            ("read", {"kind": "payment"}),
            (
                "update_plan",
                {
                    "summary": "Invalid citation test",
                    "steps": [],
                    "open_questions": [],
                    "source_ids": ["nonexistent-replay-source"],
                },
            ),
            (
                "update_plan",
                {
                    "summary": "Review scoped sources before resolution",
                    "steps": ["Check proof and statement"],
                    "open_questions": [],
                    "source_ids": [source],
                },
            ),
            ("resolve", {"reason": "Exercise deterministic proof and statement gate"}),
            (
                "wait",
                {
                    "until": (now + timedelta(hours=1)).isoformat(),
                    "reason": "Await verified evidence",
                },
            ),
        ]

        class ScriptedClient:
            def __init__(self):
                self.messages = self
                self.index = 0

            def create(self, **kwargs):
                name, arguments = script[self.index]
                self.index += 1
                return SimpleNamespace(
                    content=[
                        {
                            "type": "tool_use",
                            "id": f"replay-{self.index}",
                            "name": name,
                            "input": arguments,
                        }
                    ],
                    usage=None,
                    model="deterministic-replay",
                )

        return Supervisor(
            sandbox, client=ScriptedClient(), skill_override=override, max_steps=8
        ).run(case.id, now)
