# OMP Native Routing Strategy and Context-Continuity Spec

- **Status:** Draft upstream architecture contract
- **Applies to:** RoleBench policy generation and a future upstream OMP router

## Decision

Every native OMP model role can use either normal configured-primary selection or weighted selection. This is an upstream capability surface, not an opinion about which models or strategies a particular user should prefer.

```text
selection_strategy = primary | weighted
retry_fallback_chain = always available
```

The two concepts are orthogonal:

- `primary` chooses the configured primary route for normal execution.
- `weighted` chooses one route from a weighted roster for normal execution.
- `retry_fallback_chain` is reactive recovery after the selected route fails, exhausts retries, loses auth/capacity, or otherwise becomes unusable.

Weighted selection therefore never removes fallbacks.

## Baseline RoleBench policy

RoleBench's current baseline recommendation remains intentionally opinionated:

```text
primary:
  default
  plan
  slow
  vision
  designer
  advisor
  reviewer

weighted:
  smol
  commit
  tiny
  task
```

That baseline is represented by `baseline_strategy` in `contracts/routing-topology.json`. It is **not** an immutable OMP restriction. Any native role can be configured as `primary` or `weighted` by another operator or policy.

## Native selection scopes

Weighted selection must happen only at the role's native logical execution boundary and must remain pinned for that boundary.

| Native role | Selection scope |
|---|---|
| `default` | main session |
| `plan` | plan/role session |
| `slow` | role session |
| `vision` | operation |
| `designer` | child session |
| `advisor` | advisor runtime |
| `reviewer` | review run |
| `smol` | operation |
| `commit` | operation |
| `tiny` | operation |
| `task` | child session |

A weighted role **must not re-hash on every API request or turn**. The selected route is pinned until the logical execution boundary ends. This prevents provider/model churn from fragmenting one conversation.

## Weighted selection + fallback recovery

Normal weighted execution follows a normative 5-stage pipeline:

```text
routing key + role + policy checksum
        ↓
deterministic weighted-rendezvous ranking
        ↓
selected route (pinned for execution boundary)
        ↓
normal retries on that route
        ↓ if replay-safe recovery leaves route
remaining qualified ranked candidates from deterministic ranking (deduplicated)
        ↓ when ranked candidates are exhausted or skipped
configured retry fallback chain (deduplicated against ranked candidates)
```

### Normative recovery and deduplication rules:
1. **Sequential Traversal:** When the pinned route exhausts retries or suffers an outage, the recovery engine advances to the next eligible candidate in the deterministic rendezvous ranking.
2. **Deduplication:** Any route ID in the remaining ranked candidates that also appears in `fallback_chain` is traversed in the ranked order and omitted from the trailing fallback chain, preventing duplicate evaluation loops.
3. **Cooldown Reversion:** Following transient failure recovery, the execution returns to its **original pinned route**, never jumping to a global static primary or re-hashing the session.
4. **Fallback Chain Guarantee:** The fallback chain is required on all weighted allocations in `omp.pool-policy/v1`.

The existing OMP retry engine remains authoritative for whether a failed turn is replay-safe. OMP already refuses automatic replay after visible partial output, images, tool calls, or other replay-unsafe side effects; safe failures remove the failed assistant turn and continue the same logical prompt. A weighted router must not weaken those safeguards.
## Context and handoff safety

### New weighted task child

A newly spawned `task` child is already a separate OMP session. OMP does **not** blindly clone the parent transcript into the child. The task executor builds a child-specific runtime with:

- the selected agent's system prompt and tool contract;
- the task/assignment;
- optional shared task context;
- the active plan reference when execution is following a plan;
- inherited workspace/tool/auth configuration as allowed by the task contract.

That isolation is desirable. Picking a different model for a **new** task child does not pollute an existing child context because there is no prior child transcript to pollute. The selected model starts from the same explicit task handoff that any statically configured `@task` model would receive.

The router must resolve the weighted route **before** `createAgentSession` starts the child. Once created, the child route is pinned.

### Retry fallback inside an existing child/session

Fallback recovery is different from spawning a new child. OMP's native retry path switches the model on the existing `AgentSession` and continues against the same session history, subject to replay-safety checks. No synthetic summary handoff should be inserted merely because the provider/model changed.

This means a model fallback gets the conversation and tool history that OMP already considers safe to replay/continue. The router should delegate switching to the existing `TurnRecovery`/fallback machinery rather than creating a replacement session.

### Revive/resume

A parked or persisted task child must **not** be re-routed just because the weighted policy would choose a different route today. OMP already reopens the saved JSONL, restores the full message history, and reconstructs the persisted subagent runtime from `session_init`, including the stored model role/resolved model pattern.

Therefore the integration contract is:

```text
new logical execution → route once
existing live execution → keep pin
park/revive → restore pin + transcript
process resume → restore pin + transcript
retry fallback → switch within same session via native recovery
```

A future explicit operator command may request re-selection, but ordinary revive/resume must never silently re-hash.

### Parent → child handoff quality

Weighted routing does not itself improve or degrade the semantic task handoff. The quality of a new task child depends on the assignment/context supplied by the spawning workflow. Upstream integration should preserve OMP's existing task inputs unchanged and add routing metadata separately.

The routing layer must never:

- inject model-specific prose into the task prompt;
- append the weighted roster to child context;
- copy hidden parent scratch state into a child;
- truncate the existing plan reference to fit a selected model without using normal OMP context/compaction rules;
- synthesize a summary when ordinary same-session retry can continue safely.

## Task lanes

Only `task` receives routing lanes in v1:

```text
task/implementation
task/debugging
task/test-repair
task/refactor
task/repo-research
task/mechanical-edit
```

A lane is neither a new role nor an agent identity. It only changes the weighted candidate distribution. If evidence is insufficient, the lane inherits the generic `task` weighted allocation and fallback chain exactly.

## Policy artifacts

### Weighted policy

`omp.pool-policy/v1` may contain **any native role**. Each allocation contains:

- qualified weighted routes summing to 10,000 basis points;
- the role's native stickiness/selection scope;
- an ordered `fallback_chain`;
- optional task-lane allocations for `task` only.

### Configured-primary recommendation

`omp.primary-routing-recommendation/v1` may also contain **any native role**. Each role receives:

- a recommended primary route;
- an ordered fallback chain;
- no weights.

These are alternative normal-selection strategies over the same native role. An upstream user can choose either regardless of the RoleBench baseline.

## Capacity treatment

A role configured as `primary` still consumes provider/account capacity and must be included in demand/capacity planning. A role configured as `weighted` contributes allocatable demand. Changing a role's strategy changes what the optimizer may allocate; it does not change the role's semantic runtime contract.

## Upstream routing request

The upstream seam preserves semantic role identity and execution continuity:

```ts
interface RoleRouteRequest {
  role: string;
  routingKey: string;
  consumer: string;
  lane?: string;
  selectionScope: string;
  resumedPin?: PersistedRoutePin;
  explicitOverride?: string;
}
```

### Resolution precedence (normative):

1. **Explicit concrete caller/user model override:** Direct model names (e.g. `google/gemini-2.5-flash`, explicit thinking suffix, manual `/model` command) bypass weighted policy resolution for that session/turn.
2. **Persisted route pin:** An existing, resumed, or revived execution restores its original pinned route from `session_init` rather than re-evaluating weighted selection.
3. **Active role routing policy:** The active `omp.pool-policy/v1` (for `weighted`) or `omp.primary-routing-recommendation/v1` (for `primary`) resolves the route when requested via semantic alias (e.g. `@task`, `@smol`).
4. **Existing static `modelRoles` assignment:** Fallback to static configuration when no active policy applies to the role.
5. **Existing OMP automatic/default resolution:** Standard OMP runtime default model resolution.

The router returns one initial concrete route and its routing metadata. It does not own message replay, compaction, tool side-effect safety, or persisted-session reconstruction.
## Required upstream tests

Before weighted routing can be enforced, upstream OMP should add integration tests proving:

1. a weighted `task` route is selected before child-session creation;
2. two new task children can select different models without sharing child transcripts;
3. one task child's route remains pinned across multiple turns;
4. task `context` and `planReference` are byte-for-byte unchanged by routing;
5. retry fallback switches models within the same child session and preserves replay-safe history;
6. replay-unsafe partial output prevents automatic cross-model replay exactly as it does today;
7. persisted/cold-revived children restore their original resolved route and full history instead of re-hashing;
8. main-session weighted `default` selection is pinned for the whole session;
9. every native role accepts both `primary` and `weighted` strategy configuration;
10. every weighted role retains an ordinary fallback chain.

## Role and Reviewer Inventory Alignment

RoleBench contracts define `reviewer` in `contracts/routing-topology.json` as a first-class native routing role alongside `advisor`. This guarantees that `advisor` (the runtime watchdog) and `reviewer` (the code/artifact reviewer) maintain strictly isolated evidence, thresholds, and allocations even when configured with the same model or selection strategy. Upstream OMP integration formalizes `@reviewer` as a dedicated built-in role to complement the existing `/review` workflow.

## Compatibility

No benchmark pack grants or removes a routing capability. RoleBench evidence ranks routes; policy chooses a strategy; OMP's native runtime defines the selection boundary and continuity semantics.
