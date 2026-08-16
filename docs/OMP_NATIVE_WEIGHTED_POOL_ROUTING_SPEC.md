# OMP Native Routing Topology Decision

- **Status:** Authoritative routing-boundary decision
- **Applies to:** OMP RoleBench policy generation and the future upstream OMP router
- **Supersedes:** Any language that implies every benchmarked OMP role is eligible for weighted rotation

## Decision

RoleBench continues to benchmark native OMP model roles, but weighted model pools are permitted only for independent helper and execution roles that act on behalf of the user-controlled primary model.

### Weighted pools

```text
smol
slow
vision
designer
commit
tiny
task
```

Each new logical invocation may deterministically select a qualified route from that role's weighted pool. Selection remains sticky for the operation, role session, or child session identified by the routing topology.

### Non-pooled native roles

```text
default   manual
plan      ordered fallback chain
advisor   dedicated route
reviewer  dedicated route
```

`default` is selected by the user and remains pinned for the primary session. RoleBench must not automatically rotate or replace it.

`plan` uses one configured primary route and the ordinary ordered OMP retry fallback chain. The plan session is not load-balanced. Execution of an accepted plan may subsequently fan out through pooled helper roles.

`advisor` is a dedicated advisor/watchdog model role. Named advisor configurations may each specify their own model, tools, and instructions. Advisor runtimes are not interchangeable traffic and must never be pooled with review or helper work.

`reviewer` is a dedicated native `/review` model role with structured review behavior. Reviewer traffic is not advisor traffic and is not eligible for weighted rotation.

## Why this boundary exists

The pooled roles are stateless or independently sticky helper executions. Reassigning one invocation does not rewrite the identity of the primary session or collapse distinct native OMP workflows.

The four fixed roles have stronger runtime semantics:

- `default` owns the long-lived primary transcript.
- `plan` owns plan-mode authoring and handoff.
- `advisor` follows transcript deltas and can steer the primary run.
- `reviewer` owns diff preparation, reviewer orchestration, and structured findings.

Their demand still consumes provider capacity. The optimizer therefore subtracts expected fixed-role load and configured reserves before allocating the seven weighted pools. Fixed load is an input to optimization, never a decision variable.

## Task lanes

Only `task` receives workload-sensitive lanes in v1:

```text
task/implementation
task/debugging
task/test-repair
task/refactor
task/repo-research
task/mechanical-edit
```

A lane is not a model role and is not an agent identity. An agent or workflow may request `@task` plus a lane. If direct lane evidence is insufficient or specialization does not reduce held-out allocation regret, the lane inherits the generic `task` pool exactly.

No lanes are defined for `default`, `plan`, `advisor`, or `reviewer`. No lanes are initially defined for the other six pooled roles.

## Artifact separation

RoleBench emits two different kinds of routing output:

1. **`omp.pool-policy/v1`** for weighted pools only. Its schema rejects `default`, `plan`, `advisor`, and `reviewer`.
2. **`omp.fixed-role-recommendation/v1`** for a plan primary/fallback chain and dedicated advisor/reviewer recommendations. It contains no weights and does not control `default`.

This split prevents an optimizer, UI, or future extension from accidentally treating a specialized native workflow as fungible traffic.

## Runtime resolution

```text
explicit concrete override
    ↓
native fixed-role behavior
    ├── default: user-selected primary
    ├── plan: primary + retry fallback chain
    ├── advisor: dedicated runtime route
    └── reviewer: dedicated review route
    ↓
weighted pool policy for an eligible helper role
    ↓
static modelRoles assignment
    ↓
existing retry/fallback recovery
```

Weighted selection uses deterministic weighted rendezvous hashing and occurs once per logical invocation. Existing OMP fallback chains remain reactive recovery; they are not the normal allocation mechanism.

## Acceptance rule

A role may appear in `omp.pool-policy/v1` only when the authoritative routing topology marks it `weighted-pool`. The exact v1 set is `smol`, `slow`, `vision`, `designer`, `commit`, `tiny`, and `task`. Any other key is a contract violation.

---

# OMP Weighted Helper-Role Pooling Specification

- **Status:** Draft implementation contract
- **Version:** v1
- **Depends on:** `OMP_NATIVE_ROUTING_TOPOLOGY_DECISION.md`

## Product boundary

RoleBench produces benchmark evidence for native OMP model roles. It only allocates weighted traffic for seven helper/execution roles:

```text
smol, slow, vision, designer, commit, tiny, task
```

The primary `default` model is user-controlled. `plan` remains one primary plus an ordered fallback chain. `advisor` and `reviewer` are separate dedicated native model roles with separate slash-command and runtime behavior.

## Control flow

```text
default primary session
    │
    ├── bounded helper work ────────→ smol pool
    ├── hard delegated reasoning ──→ slow pool
    ├── image operation ───────────→ vision pool
    ├── designer child ────────────→ designer pool
    ├── commit generation ─────────→ commit pool
    ├── utility operation ─────────→ tiny pool
    └── autonomous child task ─────→ task pool
                                          │
                                          └── optional task lane
```

Roles do not spawn agents. Existing OMP workflows create operations and child sessions; the centralized router resolves an eligible role to one concrete route before execution starts.

## Routing topology

`contracts/routing-topology.json` is the authority for whether a role is pool eligible.

| Role | Strategy | Stickiness |
|---|---|---|
| `default` | manual | main session |
| `plan` | fallback chain | plan/role session |
| `advisor` | dedicated | advisor runtime |
| `reviewer` | dedicated | review run |
| `smol` | weighted pool | operation |
| `slow` | weighted pool | role session |
| `vision` | weighted pool | operation |
| `designer` | weighted pool | child session |
| `commit` | weighted pool | operation |
| `tiny` | weighted pool | operation |
| `task` | weighted pool | child session |

The policy schema repeats this boundary structurally. A policy containing a fixed role under `pools` is invalid even when a caller attempts to configure it deliberately.

## Pool lanes

A pool lane is an optional specialization below one pool role. It changes only the candidate weights; it does not change prompts, tools, agent identity, or native role semantics.

V1 supports task lanes only:

- `task/implementation`
- `task/debugging`
- `task/test-repair`
- `task/refactor`
- `task/repo-research`
- `task/mechanical-edit`

Example agent declaration after the upstream OMP field exists:

```yaml
name: debugger
model: "@task"
routingLane: debugging
```

The router receives:

```yaml
role: task
lane: task/debugging
routing_key: <child-session-id>
```

A missing, unknown, inactive, or insufficiently supported lane falls back to the generic task allocation.

## Evidence model

Capability evidence is keyed by:

```text
pool role × optional task lane × exact route
```

The parent `task × route` estimate remains available for hierarchical pooling. A task lane earns a distinct allocation only when:

1. it has at least the lane contract's minimum direct sample count;
2. the route estimates clear task quality and reliability gates; and
3. the specialized policy reduces held-out allocation regret by at least the lane threshold.

Otherwise the lane's route list and emergency fallback must exactly match the generic task pool.

## Fixed load and capacity

Weighted pools do not own all provider demand. Before optimization:

```text
usable capacity
    =
reported capacity
    - expected default load
    - expected plan load
    - expected advisor load
    - expected reviewer load
    - explicit reserves
```

The optimizer may report infeasibility but may not reassign fixed-role traffic or weaken quality floors.

## Pool policy

`omp.pool-policy/v1` contains:

- immutable topology, lane, evidence, capacity, and fixed-load references;
- deterministic weighted-rendezvous seed;
- one or more of the seven permitted role pools;
- generic role allocation for every included pool;
- optional task lane allocations;
- emergency fallback order.

Weights use integer basis points and must sum to exactly 10,000 for every allocation.

## Fixed-role recommendation

`omp.fixed-role-recommendation/v1` is separate from the pool policy:

- `plan`: recommended primary and ordered fallback chain;
- `advisor`: one dedicated recommended route and emergency fallbacks;
- `reviewer`: one dedicated recommended route and emergency fallbacks.

The artifact contains no `default` assignment and no weights.

## OMP integration

The upstream routing seam should receive:

```ts
interface RoleRouteRequest {
  role: string;
  routingKey: string;
  consumer: string;
  lane?: string;
  explicitOverride?: string;
}
```

Resolution precedence:

1. explicit concrete override;
2. native manual/dedicated/fallback-chain semantics;
3. active weighted pool policy for a pool-eligible role;
4. static `modelRoles`;
5. existing retry and recovery machinery.

The router rejects or ignores a policy attempt to pool a fixed role. `off` remains identical to current OMP; `shadow` computes decisions without changing execution; `enforce` is opt-in.

## Rollout

Enforce the shortest and lowest-risk operations first:

```text
tiny → commit → vision → designer → task → smol/slow
```

`default`, `plan`, `advisor`, and `reviewer` never enter weighted enforcement.

## Repository compatibility

Existing role contracts, task admission, verifier isolation, accounting, and calibration packs remain valid. A benchmark pack's existence does not imply pool eligibility. The routing topology determines how evidence may be consumed:

- pool-eligible evidence can qualify weighted candidates;
- plan evidence can rank a primary and fallback chain;
- advisor/reviewer evidence can rank dedicated routes;
- default evidence is informational unless the user explicitly applies it.
