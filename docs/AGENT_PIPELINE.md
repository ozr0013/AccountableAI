# Agent pipeline: agreement -> chaser -> anomaly -> controller

Three Claude agents hand work to each other through a task queue in SpacetimeDB
(`agent_task`). Code tools are the guardrails: Claude chooses, the tools refuse anything the
approved rules do not allow. Every task stores its full trace for the frontend.

```
 Upload agreement PDF
   AGREEMENT AGENT (Claude: reads sections, records each term with a verbatim quote)
     -> agreement + rules saved (SpacetimeDB agreement, agreement_rule)
     -> POST /pipeline/dispatch: one chaser task per invoice under the agreement
 Daily (or POST /pipeline/run)
   CHASER AGENT (Claude + tools + skills/chasing)
     get_invoice_context -> one decision: send_reminder | wait | escalate | handoff_to_anomaly
     handoff_to_anomaly -> new anomaly task (created_by: chaser_agent)
   ANOMALY AGENT (Claude + tools + skills/payment_review)
     get_payment_case, get_agreement_rules, vendor_history -> record_verdict
     verdict: clean | awaiting_statement | needs_review -> hand-off marked reviewed
 Controller
   approves firm/escalation reminders, confirms agreements, resolves cases (dashboard)
```

## The agents

| | Agreement | Chaser | Anomaly |
|---|---|---|---|
| Code | `agreement_agent/graph.py` | `chaser_agent/llm_chaser.py` | `anomaly_agent/agent.py` |
| Skill | built into its prompt | `chaser_agent/skills/chasing/SKILL.md` | `chaser_agent/skills/payment_review/SKILL.md` |
| Read tools | read_header, list_sections, read_section, search, find_candidates | get_invoice_context | get_payment_case, get_agreement_rules, vendor_history |
| Decision tools | record_field, mark_missing, mark_unclear | send_reminder, wait, escalate, handoff_to_anomaly | record_verdict |
| Guardrail | a value is accepted only if its quote is in the document | only actions in `policy.allowed` (approved ladder, 5-day floor, bank evidence) | the verdict must match the findings; every finding must be cited |
| Hands off to | chaser (dispatch) | anomaly (task) | controller (case review) |

Shared loop: `chaser_agent/runtime.py` - Claude (`PIPELINE_MODEL`, default `claude-opus-5-5`),
adaptive thinking with summaries, prompt caching of system + skill + tools, at most 10 turns.
A refused tool call goes back to Claude as an error; it must choose again.

## APIs for the frontend (chaser service, port 8773; dashboard proxy `/chaser-agent-api`)

| Method | Path | Body / query | Returns |
|---|---|---|---|
| POST | `/pipeline/dispatch` | `?vendor=` optional | `{as_of, queued: [task_id]}` - queues only, no model call |
| POST | `/pipeline/run` | `?limit=10` | `{as_of, processed: [{task_id, agent, decision, rationale, verdict, ...}]}` |
| POST | `/pipeline/invoices/{invoice_id}/run` | - | `{processed, flow}` - the whole chain for one invoice now |
| GET | `/pipeline/tasks` | `?invoice_id=&agent=&status=` | tasks without traces |
| GET | `/pipeline/tasks/{task_id}` | - | one task with `trace`: `thinking[]`, `steps[]` (tool, input, result, refused), `decision`, `usage` |
| GET | `/pipeline/flow/{invoice_id}` | - | `{invoice, vendor, agreement, rules, tasks, chaser_actions, case}` in order |

Agreement service (port 8772): `POST /review` saves the agreement and queues chaser tasks
(`workflow.chaser_tasks_queued` in the response).

Dashboard client: `dashboard/lib/agent-api.ts` - `pipelineDispatch`, `pipelineRun`,
`pipelineRunInvoice`, `pipelineTasks`, `pipelineTask`, `pipelineFlow` and the `AgentTask`,
`AgentTrace`, `PipelineFlow` types.

## Task lifecycle

`pending` -> `running` -> `done` | `failed`. Ids are `<agent>:<invoice>:<date>`, so a day's
dispatch is idempotent. A failed run (no decision, refusal, API error) is recorded with its
error and the queue moves on. Once an invoice has an anomaly task it is no longer dispatched to
the chaser.

## Verified run

`RAILS=real python scripts/scenario_pipeline.py` (real Claude, throwaway data copy), recorded in
`docs/PIPELINE_SCENARIO_RUN.txt`: Apr 22 polite reminder -> Apr 27 firm reminder (after the
polite one was delivered) -> Apr 29 bank payment -> Apr 30 chaser hands off -> anomaly agent
`needs_review` (late_payment, statement_predates_payment), noting that Keyway's March statement
claimed the invoice was paid on Mar 10 while the bank shows Apr 29.
