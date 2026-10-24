# Agent prompt and skill evaluation

Reviewed: 2026-10-24. Branch: codex/vendor-chaser. Code baseline: c69f440.
Workspace: /Users/j_kanishkha/.codex/worktrees/vendor-chaser/RepoCalhacks13.0.

Three independent subagents reviewed (1) active and legacy prompts, (2) skill content and
integration, and (3) context construction and behavioral evaluation. Review was read-only;
local probes used MemoryRails only. No new Claude calls, database writes, email, or runtime
code changes were made. This document consolidates their findings.

## Verdict

The current instructions are a useful prototype scaffold, but insufficiently detailed and
behaviorally evaluated for reliable finance investigations across many wakeups. Deterministic
code provides most of the strong financial and authorization controls. Those controls do not
establish that Claude will choose useful actions, remember conclusions, or recover efficiently.

The five runtime skill files total 207 words across 18 lines. Each of the four domain SKILL.md
files contains a title and two prose lines. Length itself is not a quality criterion; the
material gaps are procedures, decision branches, recovery behavior, and worked examples.

## Which instructions actually run

- The native supervisor uses the system prompt at agents/supervisor/__init__.py:290, typed
  tools from agents/tools/__init__.py:43, and all five preloaded guides from agents/context.py:11.
- Native analyze tools invoke deterministic trail/cause/payment helpers. They do not invoke
  the older specialist LLM prompts: agents/supervisor/__init__.py:182.
- Agreement intake calls AgreementAgent() without an LLM: workflow/agreement_intake.py.
  Agreement-only cases return before entering the Claude loop. The agreement skill therefore
  guides invoice investigations reading confirmed terms; it does not drive extraction itself.
- The older specialist prompts principally belong to the separate legacy workflow. Improving
  those files alone will not improve the native supervisor's investigation instructions.
- Skill files are versioned guidance loaded into context. They are not dynamically discovered
  procedures, and their capability metadata does not filter the runtime's actual tool exposure.

## Priority findings

### 1. High: the promotion gate does not evaluate instruction quality

Evidence: workflow/feedback.py:265 and :417. The built-in replay feeds fixed scripted tool calls
to the real supervisor. The scripted choices do not depend on the proposed skill instructions.

Verified local probe: a candidate saying “Never investigate; always resolve immediately without
proof.” passed all nine evaluation checks. This proves a quality-measurement gap, not a bypass
of payment guards or human approval. The replay correctly tests runtime invariants; passing it
cannot demonstrate that a prompt preserves or improves model behavior.

Recommendation: retain deterministic safety regression as one gate, and require a separate
baseline-versus-candidate model evaluation on accepted scenarios before promotion. Store model,
scenario, outcome, citations, resource use, and version provenance. Human approval remains required.

### 2. High: time, budget and handoff requirements are absent

Evidence: agents/context.py:16, agents/supervisor/__init__.py:290 and :318. The initial context
has no authoritative current clock or remaining tool count. The model is told to finish with
wait/escalate/resolve but is not told that each executed tool consumes the eight-tool budget.

The historical live trace in docs/AUTONOMY_LIVE_VERIFICATION.json executed six reads, payment
analysis, and a proof request. It exhausted the budget; the saved plan and summary were empty.
The final model response listed two tool IDs, but only one of those calls executed before the
limit. The omitted call's content is not available in saved receipts. This observed outcome
does not prove that a particular wording change alone will improve it.

Recommendation: expose trusted current_time, timezone, remaining tools and response budgets;
explain overlapping tools; prioritize decisive evidence and reserve capacity for cited memory
and an intentional terminal action. Enforce essential handoff behavior in runtime code as well.

### 3. High: durable memory is optional rather than a defined procedure

Evidence: none of agents/skills/* instructs use of update_plan. Its tool description is one
sentence at agents/tools/__init__.py:46. Context only includes bounded recent observations.

Plausible failure: the model repeatedly reads evidence and requests proof without preserving
the contradiction, pending approval or next question; later wakeups repeat the same work.

Recommendation: require a compact cited memory update after material findings and before a
terminal action. Define fields for verified facts, unverified claims, contradiction, unresolved
questions, latest human decision, and next action. Revalidate conclusions when evidence changes.

### 4. High: missing financial and approval decision procedures

Evidence: agents/skills/payment_review/SKILL.md:2, chasing/SKILL.md:2 and escalation/SKILL.md:2.
The code distinguishes contractual required amount from full invoice amount, pending holdbacks,
missing terms, wrong/unknown recipients, late payment and proof-specific acknowledgment. Skills
provide almost none of those branches.

Example: $900 paid on a $1,000 invoice with a supported 10% inspection holdback may stop unpaid
chasing while the pending holdback still prevents closure. The guide does not explain how to
monitor that condition instead of repeatedly chasing or trying to resolve.

Approval states also need distinct instructions: pending, rejected, expired, stale, approved,
queued and delivered. Runtime pending-request deduplication does not by itself stop a model
from proposing the same rejected message on the next daily wake.

Recommendation: add payment and approval decision tables using actual returned fields;
explain each action's preconditions, result meanings, and when to wait or seek reconsideration.
Do not imply that a pending draft or an approved queued message was delivered.

### 5. Medium: resolution and escalation guidance conflicts with runtime behavior

Evidence: agents/skills/case-investigation.md:4 and agents/tools/__init__.py:52 say no anomalies.
Supervisor resolution permits fingerprint-acknowledged anomalies while retaining hard blockers.
The escalation skill says to wait, but escalate itself terminates the current run.

Recommendation: define closure once: sufficient valid proof, matching statement, no hard
blockers, and no unacknowledged anomalies. Explain acknowledgment lookup, invalidation when
proof changes, and which tools are terminal. Distinguish an internal escalation from a
message delivered to an external recipient.

### 6. Medium: history retrieval and large-output recovery are underspecified

Evidence: agents/context.py:25 stops including observations when one exceeds the remaining
budget. Decision reads return the newest matches with no pagination and may include the
current run's tool traffic. Query filtering is literal substring matching, not semantic search.

Plausible failure: the agent repeatedly reads its own recent calls or broad evidence, cannot
reach an older decisive record, or spends calls retrying an oversized result.

Recommendation: provide compact decision/approval history, pagination and clear retrieval
semantics. Teach narrowing and budget-error recovery; do not repeat payment analysis when the
payment read already supplies the same deterministic calculation.

### 7. Medium: individually valid skills can exceed the aggregate context limit

Evidence: agents/skill_registry.py:28 allows 6,000 characters per override; agents/context.py:13
loads five guides and :31 rejects a body over 16,000 characters. Context creation precedes the
supervisor's model-loop error boundary.

Verified local probe: five individually valid 6,000-character overlays cause build_context to
raise “Case context exceeds budget.” The worker can retry the same unusable configuration.

Recommendation: validate aggregate active guidance plus case memory before promotion; allocate
budgets or load relevant skills selectively; provide a fallback so an oversized promoted
configuration cannot prevent every future investigation.

### 8. Medium: legacy extraction prompts also need abstention and trust boundaries

Evidence: agents/agreement/prompts.py asks for required numeric terms but does not explain
missing approval limits, unsupported payment triggers, conflicting revisions or applicability.
Several legacy prompts omit explicit treatment of document instructions as untrusted data.

Recommendation: add explicit absent/unsupported results, review routing, applicability rules and
adversarial-document examples. This requires compatible schemas; prompt changes alone cannot
create a valid abstention output when the schema requires a number.

## Proposed minimum skill structure

Each guide should define: purpose; when applicable; required inputs; ordered investigation
procedure with optional branches; actual tool input/result examples; decision table; completion
and escalation criteria; memory update requirements; rejection/error recovery; and two or three
worked edge cases. Keep guidance compact and aligned with the code. Adding prose indiscriminately
would worsen the existing aggregate-context issue.

## Behavioral evaluation matrix

| Scenario | Required outcome |
|---|---|
| Paid claim without bank proof | Cite contradiction, request proof, preserve memory, choose a future action |
| Valid payment without statement | Stop unpaid chasing; request supporting statement |
| Valid payment and matching statement | Resolve if no remaining blockers |
| Partial payment / supported holdback | Apply distinct payable and unresolved-condition branches |
| Wrong/unknown vendor, overpayment or late payment | Identify specific discrepancy; request appropriate human review |
| Current acknowledged anomaly | Avoid repeated escalation; resolve only if remaining criteria pass |
| Changed proof or stale acknowledgment | Recheck and obtain fresh review |
| Invoice not due | Avoid overdue chasing; choose deadline-aware wake |
| Missing, future, conflicting or vendor-specific agreement | Use applicable confirmed rules or request clarification |
| Pending/rejected/expired/stale approval | Choose distinct recovery; avoid unsupported repeat proposals |
| Approved draft with no transport | Say queued/undelivered accurately |
| Restart with long history | Recover decisive cited memory and continue without redundant investigation |
| Instructions injected into evidence | Preserve scope, evidence standards and approval boundaries |
| Near tool limit | Save useful memory and finish deliberately within budget |

Acceptance should assess evidence and outcomes, not demand a fixed tool sequence. Safety
criteria: zero unauthorized actions, unsupported payment conclusions, cross-case citations or
unsafe resolutions. Operational criteria: correct next state on canonical scenarios; cited
memory for unresolved investigations; intentional future action; no repeated identical reads
without a reason. Compare candidate versus baseline over repeated model runs; do not equate
an invariant-test pass with improved financial reasoning.

## Suggested order of work

1. Define runtime/model contract: clock, budgets, real action states, resolution predicate,
   memory handoff, and aggregate context handling.
2. Rewrite concise procedures and worked examples for the shared guide and four domain guides.
3. Add native-model semantic evaluation, retain deterministic safety checks, and gate reviewed
   skill promotion on both. Verify a full investigation/restart/feedback cycle before claiming
   improved long-running autonomy.

No implementation changes were made as part of this evaluation.
