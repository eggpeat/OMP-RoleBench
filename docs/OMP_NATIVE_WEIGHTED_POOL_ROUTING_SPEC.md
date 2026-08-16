# OMP Native Weighted Routing Boundary

- **Status:** Authoritative routing-boundary decision
- **Applies to:** OMP RoleBench policy generation and the future upstream OMP router

## Decision

RoleBench benchmarks native OMP model roles, but weighted routing is permitted only for roles whose invocations are naturally fungible helper/execution work.

### Weighted pools

```text
smol
commit
tiny
task
```

Each new logical invocation may deterministically select a qualified route from that role's weighted pool. Selection remains sticky for the operation or child session identified by the routing topology.

### Configured primary + ordinary fallback chain

```text
default
plan
slow
vision
designer
advisor
reviewer
```

These roles use one configured primary route and OMP's existing ordered retry fallback chain. They are benchmarked and may receive RoleBench recommendations for the primary and fallback order, but they are not load-balanced during normal execution.

`advisor` and `reviewer` remain separate native workflows and separate routing roles. Treating both as fallback-chain roles does not pool their evidence or make them interchangeable; it only avoids hard-coding one preferred model into the architecture.

`default` remains the primary long-lived session role, but that does not make its routing semantics fundamentally different from the other non-pooled roles: it has a configured primary and can use the ordinary retry fallback chain. RoleBench may recommend that chain without automatically rotating the session through a weighted pool.

## Why the boundary exists

The four weighted roles are repeated helper/execution surfaces where independent invocations can safely land on different qualified models:

- `smol`: fast, bounded helper work
- `commit`: commit-message generation
- `tiny`: titles, metadata, classification, memory, and other bounded utility work
- `task`: autonomous delegated child-agent execution

`default`, `plan`, `slow`, `vision`, `designer`, `advisor`, and `reviewer` instead behave like configured role primaries. Their model identity should remain stable for the logical session, operation, advisor runtime, or review run, with OMP's existing retry chain handling failures and depletion.

## Routing topology

| Native role | Strategy | Selection scope |
|---|---|---|
| `default` | fallback chain | main session |
| `plan` | fallback chain | role session |
| `slow` | fallback chain | role session |
| `vision` | fallback chain | operation |
| `designer` | fallback chain | child session |
| `advisor` | fallback chain | advisor runtime |
| `reviewer` | fallback chain | review run |
| `smol` | weighted pool | operation |
| `commit` | weighted pool | operation |
| `tiny` | weighted pool | operation |
| `task` | weighted pool | child session |

`contracts/routing-topology.json` is the authority for this classification. `omp.pool-policy/v1` structurally permits only `smol`, `commit`, `tiny`, and `task`.

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

A lane is not a model role and is not an agent identity. An agent or workflow may request `@task` plus a lane. If direct lane evidence is insufficient or specialization does not reduce held-out allocation regret enough, the lane inherits the generic `task` pool exactly.

No lanes are defined for non-pooled roles. No lanes are initially defined for `smol`, `commit`, or `tiny`.

## Artifact separation

RoleBench emits two different routing outputs:

1. **`omp.pool-policy/v1`** — weighted allocations for `smol`, `commit`, `tiny`, and `task` only.
2. **`omp.fixed-role-recommendation/v1`** — primary + ordered fallback-chain recommendations for `default`, `plan`, `slow`, `vision`, `designer`, `advisor`, and `reviewer`.

The fixed-role recommendation artifact contains no weights. A recommendation can be adopted by the operator or upstream OMP configuration without turning that role into pooled traffic.

## Capacity treatment

Non-pooled does not mean invisible. Before optimizing weighted pools:

```text
usable capacity
    = reported capacity
    - expected default load
    - expected plan load
    - expected slow load
    - expected vision load
    - expected designer load
    - expected advisor load
    - expected reviewer load
    - explicit reserves
```

RoleBench may report infeasibility, but it may not reassign these fixed-role loads merely to satisfy a weighted allocation.

## Runtime resolution

```text
explicit concrete override
    ↓
native non-pooled behavior
    ├── default: primary + retry fallback chain
    ├── plan: primary + retry fallback chain
    ├── slow: primary + retry fallback chain
    ├── vision: primary + retry fallback chain
    ├── designer: primary + retry fallback chain
    ├── advisor: primary + retry fallback chain
    └── reviewer: primary + retry fallback chain
    ↓
weighted pool policy for smol / commit / tiny / task
    ↓
static modelRoles assignment
    ↓
existing retry/fallback recovery
```

Weighted selection uses deterministic weighted rendezvous hashing once per logical invocation. Existing fallback chains remain reactive recovery and the normal resolution mechanism for all seven fallback-chain roles.

## Upstream OMP integration

The central routing seam can remain small:

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
2. native configured-primary/fallback-chain semantics;
3. active weighted pool policy for one of the four pool-eligible roles;
4. static `modelRoles` assignment;
5. existing retry and recovery machinery.

The router must reject or ignore any policy attempt to pool `default`, `plan`, `slow`, `vision`, `designer`, `advisor`, or `reviewer`.

## Rollout

Weighted enforcement can be introduced in the lowest-risk order:

```text
tiny → commit → smol → task
```

All other roles remain outside weighted enforcement.

## Repository compatibility

Existing role contracts, task admission, verifier isolation, accounting, and calibration packs remain valid. A benchmark pack's existence does not imply pool eligibility. Routing topology determines how evidence may be consumed:

- `smol`, `commit`, `tiny`, and `task` evidence may qualify weighted candidates;
- `default`, `plan`, `slow`, `vision`, `designer`, `advisor`, and `reviewer` evidence may rank a primary and fallback chain.
