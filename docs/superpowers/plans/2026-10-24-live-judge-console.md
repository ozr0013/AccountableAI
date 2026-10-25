# Live Judge Console streaming — narrowed scope

User clarification: three agent APIs already exist; connect their live streams to Judge Console only. No new judge sessions, agent workflow, task scheduler, or demo endpoints.

Frontend: original checkout/main dashboard/app/console/page.tsx embeds JudgeAgentStream, connecting existing pipeline8773/pipeline/stream and factworkflow8765/api/stream. Agreement8772 gains read-only /stream because it previously had no stream. Cards and expandable bounded audit show real run starts, reasoning summaries, tools/inputs/results, skill hashes and recorded communication/alert statuses. No inferred mail delivery or fabricated thinking.

Backend: judgment-pipeline worktree runtime.py emits call IDs, model turn metadata, actualskillloads; agreement graph broadcasts its existing LangGraph execution and streams model chunks, API runs it off the eventloop so SSE remains live. Existing persisted outbox transitions are broadcast. No routing/approval/sending policy changes.

Validation: reducer/unit tests, backend graph runtime tests with scriptedSDK only; tsc, code review, all3live SSE endpoints andbrowserpanel. No paidmodelcalls or externalmail duringvalidation.
