# Benchmark-Informed Model-to-Role Assignment and Routing for OMP

- **Status:** Living target architecture
- **Last reviewed:** August 14, 2026
- **Project:** OMP RoleBench

## Executive decision

Build an open-source offline toolkit with a narrow, explicit boundary to OMP runtime behavior:

1. **`omp-rolebench`** owns the canonical role benchmark profile, diagnostic tasks, benchmark orchestration, evidence, model-route qualification and recommendation, capability estimation, optional local task generation, capacity-aware optimization, validation, and policy generation.
2. **Focused upstream OMP changes** provide the generic runtime seam required to load, explain, shadow, and enforce a versioned role-allocation policy.

RoleBench must deliver useful model-to-role recommendations without session history, a capacity optimizer, or OMP runtime integration. Develop upstream integration against an OMP fork, but do **not** make a permanent product fork the distribution model. The benchmark changes more quickly than the runtime, carries heavier datasets and statistical dependencies, and should be independently versioned.

OMP already contains canonical model roles, role-alias resolution, preserved role identity on structured subagent launches, normalized provider/account usage reporting, retry and fallback behavior, a credential-distribution diagnostic, and a Harbor-backed metaharness. RoleBench should extend those capabilities rather than replace them.

## Product definition

> Given OMP's canonical roles, a fixed default benchmark profile, and candidate model routes, produce reproducible role-specific evidence and ranked model-route recommendations. Optionally supplement the default profile with reviewed tasks derived from explicitly selected local sessions. Where demand and capacity data are available, turn qualified recommendations into a versioned allocation policy that OMP can execute deterministically.

The complete product loop is:

```text
OMP canonical roles + fixed default benchmark profile
                     + optional reviewed session-derived tasks
    ↓
candidate exact model routes
    ↓
objective, sandboxed benchmark evidence
    ↓
role × route capability and reliability estimates
    ↓
ranked model-route recommendations per role
    ↓ optional
capacity-constrained allocation policy
    ↓ optional
deterministic OMP role routing
```

The core toolkit replaces arbitrary model assignments with reproducible recommendations backed by measured quality, operational performance, provenance, and uncertainty. Capacity-aware traffic distribution and runtime enforcement are advanced layers, not prerequisites for the offline recommendation workflow.

## Problem

OMP can map roles to models and recover from provider failures, but a user still has to decide manually:

- which provider/model/effort route is qualified for each canonical role;
- which qualified route is the best default assignment for that role;
- whether a cheaper or faster route is actually good enough;
- how confident the evidence is and which capabilities remain untested;
- when assignments should change after a model, provider, harness, or OMP release; and
- optionally, how traffic should be distributed across subscription, API, account, and concurrency limits.

General leaderboards are insufficient. Terminal-Bench is valuable evidence for terminal competence, but its aggregate score does not directly answer whether a route satisfies OMP's `task`, `plan`, `vision`, `commit`, or other role contracts. It also does not distinguish every exact route or cover every OMP capability.

## Goals

1. Ship a fixed, versioned default benchmark profile mapped to every canonical OMP role.
2. Use pinned Terminal-Bench tasks where appropriate and Terminal-Bench-style OMP-native executable tasks for uncovered role capabilities.
3. Evaluate exact provider/model/effort routes rather than ambiguous model names.
4. Produce reproducible role-specific qualification and ranked assignment recommendations with explicit uncertainty and coverage.
5. Work without access to OMP session history.
6. Optionally derive supplemental private task candidates from explicitly selected local sessions.
7. Enforce role-specific capability and reliability floors and distinguish model failures from provider or infrastructure failures.
8. Run only enough diagnostics to make a stable recommendation or explain why evidence is insufficient.
9. Optionally allocate traffic across capacity pools and quota windows and emit immutable, inspectable policies.
10. Reuse OMP's existing model resolution, authentication, usage health, and recovery behavior for runtime integration.
11. Preserve explicit user model selection as an intentional override.

## Non-goals

- Require session logs, sweep a user's history by default, or treat raw session content as benchmark evidence.
- Reproduce an official Terminal-Bench score from a small or modified subset.
- Treat a public benchmark aggregate as a direct OMP role score.
- Build another general model leaderboard.
- Benchmark or allocate by agent name.
- Let an online LLM choose a model at request time.
- Hide quality, cost, latency, capacity, and uncertainty inside one arbitrary composite score.
- Put benchmark tasks, large run artifacts, statistical notebooks, or optimizer dependencies into OMP core.
- Dynamically mutate routing weights on every request without publishing a new policy version.

## Core principles

### Roles—not agents—are the control-plane key

Diagnostics, capability estimates, allocation policies, and route decisions are keyed by canonical OMP model role. Agents and workflows request roles and consume the resulting route:

```text
agent/workflow → @role → role router → concrete route
```

Agent identity may be retained in telemetry to diagnose interactions, but it is not a benchmark lane or allocation dimension. If a genuinely different workload requires different thresholds or task mixes, it should become a custom role contract rather than an agent-specific exception.

### Fixed canonical baseline; optional local extension

Every installation begins with the same versioned mapping from canonical OMP roles to required capabilities and fixed task packs. That baseline makes recommendations reproducible, comparable across installations, and usable without private user data. Missing default coverage makes a role ineligible for recommendation; it must not silently turn session access into a prerequisite.

An operator may explicitly select local sessions for an offline authoring run when recurring work is not represented by the fixed profile. Inside that private boundary, RoleBench may identify goals, tool patterns, artifacts, constraints, and failure modes and propose minimal self-contained diagnostic drafts with canonical role bindings and capability tags. Approved local diagnostics supplement the baseline; they do not replace it or change the role taxonomy.

Raw session content may be read only inside the operator-controlled local authoring boundary. It must not enter candidate metadata, benchmark evidence, policies, routing telemetry, or public artifacts. Generated drafts remain private and non-authoritative until independent privacy, redistribution, verifier, split, qualification, and calibration reviews approve them. The current `scan-session` command implements only privacy-minimized signal discovery; executable session-to-task synthesis remains planned.

This extension is dynamic but not online: workload drift may trigger a new offline authoring and calibration cycle that produces new immutable task and policy versions. It never changes weights during a request.

### Exact routes—not model names—are evaluated

A route is the actual callable deployment:

```text
provider + model + thinking/effort + transport/upstream + capacity pool + OMP version
```

The same underlying model through two providers can differ materially in quantization, context limits, tool compatibility, latency, price, reliability, and quota behavior. Those must remain separate routes.

### Quality is a constraint

A route cannot receive role traffic merely because it is cheap or available. It must first clear the role's capability, reliability, and required-feature thresholds with adequate confidence.

### Optimization is lexicographic

The optimizer should make decisions in this order:

1. Satisfy hard capability, quality, and reliability constraints.
2. Minimize maximum normalized utilization across capacity pools and quota windows.
3. Limit unnecessary policy churn from the previous valid policy.
4. Minimize expected cost per accepted solve.
5. Minimize latency as the final tie-breaker.

### Live inputs become immutable snapshots

Live capacity is sampled by the optimizer and recorded in the generated policy. The runtime executes a versioned policy rather than silently recomputing weights. Immediate health or depletion checks may make a route temporarily ineligible, but material capacity changes should produce a new policy version.

## Canonical OMP roles

OMP currently defines ten built-in roles. These IDs are the v1 diagnostic taxonomy and the required keys of the completed default benchmark profile.

| Role | Diagnostic contract | Primary decision signal |
| --- | --- | --- |
| `default` | Broad interactive coding, terminal work, and tool use | Accepted-solve rate across representative work |
| `smol` | Bounded mechanical and lightweight work | Success within strict latency/cost budgets |
| `slow` | Hard diagnosis, reasoning, and recovery | Quality on difficult tasks |
| `vision` | Image-grounded and multimodal work | Correct evidence grounding and required input support |
| `plan` | Architecture, decomposition, and sequencing | Executability and correctness of the plan |
| `designer` | UI/UX judgment and implementation | Functional correctness plus visual quality |
| `commit` | Commit-message generation | Semantic coverage, convention adherence, and no invention |
| `tiny` | Titles, memory operations, classification, and metadata | Exact output, reliability, speed, and minimal consumption |
| `task` | Self-contained delegated implementation | Reliable autonomous completion |
| `advisor` | Independent review and defect discovery | Defect/risk recall with controlled false positives |

Each built-in role must map to at least one versioned default task pack whose capability tags cover the role's required capabilities before RoleBench calls the default profile complete. A route receives no recommendation for a role with inadequate task coverage or calibration. Session-derived tasks may improve local coverage, but cannot stand in for a missing canonical default.

The runtime and policy schemas support all built-in roles from the beginning. Diagnostic spending may prioritize roles with active demand, but the public default-profile contract remains complete and explicit.

Custom roles are optional extensions. A custom role must either inherit a built-in diagnostic contract and override explicit thresholds or demand, or provide its own versioned contract and diagnostic task mix.

## System architecture and repository ownership

### OMP core repository

OMP owns runtime truth and generic routing behavior:

- canonical built-in role IDs and alias semantics;
- model catalog, provider capabilities, authentication, and route resolution;
- normalized usage and capacity health;
- a context-aware role-routing seam;
- versioned policy loading and compatibility checks;
- deterministic weighted route selection;
- stickiness, health gating, and fallback execution;
- shadow/enforce/off modes;
- routing-decision telemetry and explanation;
- static `modelRoles` fallback behavior; and
- conformance, regression, and replay tests.

OMP must not depend on the benchmark corpus, estimator, optimizer, Harbor, or a database in order to route normal requests.

### `omp-rolebench` repository

The separate toolkit owns offline evidence production, recommendation, and policy generation:

- canonical role-contract manifests and version history;
- the fixed default role benchmark profile and versioned task packs;
- pinned references to applicable public Terminal-Bench datasets and tasks;
- OMP-native Terminal-Bench-style diagnostics and executable verifiers;
- private, opt-in user-session analysis and local session-to-task candidate generation;
- Harbor dataset packaging and the OMP benchmark adapter/configuration;
- run manifests and normalized evidence ingestion;
- task selection and adaptive stopping;
- role × route capability estimation and ranked recommendations;
- latency, reliability, token, cost, and quota-consumption models;
- optional capacity snapshots and demand forecasts;
- constrained optimization and allocation-regret validation;
- policy schema, generation, checksums, and publishing; and
- reports and dashboards.

Run artifacts should remain outside both source repositories or in an artifact store. Curated, normalized evidence snapshots can be committed only when small, license-compatible, and intentionally public.

### Existing OMP infrastructure to reuse

- The built-in role registry already defines the canonical role set.
- Structured subagent policy resolution already retains `modelRole` before expanding aliases to concrete patterns.
- OMP's usage model normalizes provider, account, model, shared scope, remaining capacity, and reset windows.
- Existing retry/fallback logic already understands credentials, usage limits, provider errors, and recovery chains.
- `dry-balance` already measures credential distribution and can optionally collect per-account TTFT and throughput.
- `packages/metaharness` already runs OMP through Harbor, records pass/cost/token data, manages experiments, normalizes traces, and can run local source builds.

For the prototype, RoleBench can run against an OMP checkout's existing metaharness. Long term, RoleBench should consume a stable CLI/JSON or package contract rather than import OMP's private internal TypeScript modules. Any necessary metaharness change should be a narrow, generic upstream PR.

## Data contracts

Every artifact uses an explicit `schema_version`, immutable ID, creation time, and source digest. Secrets and bearer credentials are prohibited.

### Role contract

```yaml
schema_version: omp.role-contract/v1
role: task
required_capabilities:
  - terminal_tools
  - code_editing
primary_metric: accepted_solve
quality_floor: 0.80
reliability_floor: 0.97
confidence: 0.95
latency_slo_seconds: 900
failure_cost: high
task_mix: task-v1
```

### Route identity

```yaml
route_id: openai-codex/gpt-5.6-terra:high
provider: openai-codex
model: gpt-5.6-terra
thinking: high
transport: responses
upstream: null
capacity_pool: openai-codex:account-pool-a
omp_version: 17.2.15
```

`capacity_pool` is an opaque stable identifier. It must never expose account tokens or other secrets.

### Benchmark evidence row

At minimum:

```text
evidence_id
run_id
role_contract_version
role
route_id
task_id
task_version/digest
attempt
verifier_outcome
failure_class
start/end timestamps
end-to-end latency
provider requests/retries/fallbacks
input/output/cache tokens
billed cost
capacity consumed by pool/window
OMP commit/version
runner/verifier version
trajectory-integrity status
```

### Capability snapshot

For each `role × route` pair:

- posterior success distribution;
- point estimate and confidence/credible interval;
- probability of clearing the role's quality floor;
- operational reliability distribution;
- p50/p90 end-to-end latency;
- expected tokens, cost, and quota consumption per attempt;
- expected cost and time per accepted solve;
- required-capability compatibility;
- sample count, task coverage, and evidence freshness; and
- calibration/model version.

### Role recommendation

The first independently useful product artifact is a capacity-independent recommendation snapshot:

```yaml
schema_version: omp.role-recommendation/v1
recommendation_id: role-recommendation-2026-08-14.001
created_at: 2026-08-14T13:00:00Z
role_registry_digest: sha256:...
benchmark_profile: default-v1
evidence_snapshot: evidence-2026-08-14.001
capability_snapshot: capability-2026-08-14.001
recommender_version: role-recommender-v1

roles:
  task:
    status: recommended
    selected_route: openai-codex/gpt-5.6-terra:high
    qualified_routes:
      - route_id: openai-codex/gpt-5.6-terra:high
        rank: 1
        probability_above_quality_floor: 0.97
      - route_id: anthropic/claude-sonnet-5:high
        rank: 2
        probability_above_quality_floor: 0.95
    excluded_routes:
      - route_id: example/provider-route:low
        reason_codes:
          - insufficient-capability-confidence
```

Each role records `recommended`, `insufficient-evidence`, `no-qualified-route`, or `inactive`. A recommended route must clear the role's capability and reliability floors; rank may then consider accepted-solve quality, latency, and cost under an explicit deterministic rule. Every selection and exclusion is traceable to the profile, evidence, capability snapshot, thresholds, and reason codes.

This artifact answers “which route should I assign to this role?” without requiring demand forecasts, capacity data, traffic weights, or OMP runtime integration. The allocation optimizer may consume its qualified route set, but cannot weaken its quality gates.

### Capacity snapshot

Capacity is indexed by pool and time window, not by model name alone:

```text
pool × window → used, remaining, limit, reset time, reserve, concurrency, health
```

The snapshot may combine:

- OMP provider/account usage reports;
- API budget and rate-limit data;
- subscription quota windows;
- local/GPU concurrency and occupied time;
- user-configured hard budgets; and
- observed request consumption when a provider exposes no authoritative quota.

Unknown capacity is explicit. The optimizer applies a configured conservative, fail-open, or fail-closed policy rather than interpreting unknown as unlimited.

### Allocation policy

Use integer basis-point weights to avoid cross-runtime floating-point ambiguity.

```yaml
schema_version: omp.route-policy/v1
policy_id: role-policy-2026-08-12.001
created_at: 2026-08-12T13:00:00Z
valid_from: 2026-08-12T13:00:00Z
valid_until: 2026-08-13T13:00:00Z
role_registry_digest: sha256:...
evidence_snapshot: evidence-2026-08-12.001
capability_snapshot: capability-2026-08-12.001
capacity_snapshot: capacity-2026-08-12T12:55:00Z
demand_snapshot: demand-2026-08-12.001
optimizer_version: role-allocator-v1
seed: 4815162342

roles:
  task:
    quality_floor: 0.80
    confidence: 0.95
    stickiness: child_session
    routes:
      - route_id: deepseek-direct/deepseek-v4-pro:high
        weight_bps: 4500
      - route_id: openai-codex/gpt-5.6-terra:high
        weight_bps: 3500
      - route_id: anthropic/claude-sonnet-5:high
        weight_bps: 2000
    emergency_fallback:
      - openai-codex/gpt-5.6-terra:high
      - anthropic/claude-sonnet-5:high
```

Each policy includes a content checksum. OMP activates it atomically only after schema, checksum, role-registry, route-resolution, and validity checks pass.

### Routing-decision event

Every enforced or shadow decision records:

```text
decision_id
timestamp
mode: shadow|enforce
policy_id/checksum
role
routing_key hash
capacity/health epoch
eligible route set
selected route
skipped routes and reason codes
explicit override status
fallback/recovery outcome
OMP version
```

The raw prompt, credentials, and sensitive account identifiers are not required.

## Diagnostic methodology

### Default benchmark profile and task sources

The default profile is a versioned mapping from every canonical role to required capabilities and fixed task packs. It is sufficient for the standard recommendation workflow and contains no user session data.

1. **Terminal-Bench anchors:** pinned public tasks useful for `default`, `task`, and `slow`, with selected evidence for `plan` and `advisor` where justified.
2. **Fixed OMP-native anchors:** small, purpose-built Terminal-Bench-style tasks for all roles, especially `smol`, `tiny`, `commit`, `vision`, and `designer`.
3. **Held-out routing tests:** task families and model routes excluded from task selection and estimator fitting, used only to measure recommendation and allocation regret.
4. **Optional session-derived diagnostics:** private supplemental tasks generated from recurring work in explicitly selected sessions. They are outside the default profile unless independently reviewed, licensed for redistribution, and deliberately published.

Do not copy or relabel an official Terminal-Bench dataset in a way that implies an official score. Record the exact dataset release, task digest, harness, and route identity. Report results as RoleBench role estimates.

An `omp.diagnostic-task/v1` Terminal-Bench source uses `source.version` for the immutable Harbor dataset package plus numeric revision and `source.task` for the exact Harbor task package. `source.digest_sha256` binds the Harbor task-version content hash; `source.dataset_digest_sha256` binds the Harbor dataset-version content hash. Both hashes omit the `sha256:` prefix, and mutable package references such as `@latest` are prohibited.

### Candidate authoring, admission, and evidence use

The task pipeline is source-agnostic and separates candidate acquisition, authoring, admission, calibration, and evidence use. No step requires a user session:

1. A candidate may come from a pinned public task, a purpose-built OMP-native task, `import-omp-gym`, or the optional local session workflow.
2. `import-omp-gym` parses only the public `task.toml` and `workspace/` format into a private candidate directory. It does not import OMP Gym code, follow links, infer licensing, or grant admission.
3. For the optional local workflow, `scan-session` reads one caller-selected OMP JSONL session and returns a versioned privacy-minimized candidate with a fresh opaque reference, `entry_count` for parsed v3 message entries, and signal objects whose `ordinal` indexes those message entries and whose `kind` identifies the signal. Raw text, paths, IDs, model/provider/account fields, and stable session fingerprints are prohibited. A planned local generator may inspect selected content only inside the private boundary and synthesize de-identified task drafts; that synthesis is not implemented in v1.
4. An operator turns a candidate into `omp.diagnostic-task/v1` with a canonical role binding, capability tags, public/private asset digests, exact single-platform agent, admission-agent, runner, and verifier image identities, objective criteria, and independent privacy, license, verifier, and split approvals.
5. `prepare-admission-run` independently re-inspects the image manifest, config ID, platform, fixed role/content/stage labels, and in-image asset trees. The agent and runner images must expose the exact public tree and no verifier-private tree; the verifier image must expose the exact private tree and no public tree.
6. At least two healthy runs for each distinct baseline, reference, and tamper image establish deterministic failure, success, and tamper rejection. `qualify` reparses every report, recomputes accounting, requires exact run/envelope/isolation/task/policy/probe-image mappings, and produces `evaluation_provenance` and `runner_isolation` while requiring identical artifact/reward results across repeats.
7. V1 qualifications are `calibration-required`, not proof of cross-model discrimination. V1 cannot claim `admitted` or freeze a routing-eligible pack until a later contract defines independently reviewed discrimination evidence. `prepare-run` therefore permits only non-holdout `calibration-only` execution and preserves the observed task/image/policy bindings in `omp.worker-run-manifest/v1`.
8. The normal rootless Docker/`runsc` worker executes admission and calibration manifests across a three-container isolation pipeline: agent -> candidate runner -> passive verifier. The candidate artifact is supplied only to and may execute only in the candidate runner; it never executes in or becomes instructions for the verifier container. Because runner output may reflect artifact bytes, the verifier receives those bytes only as bounded inert untrusted data inside a host-framed evidence envelope. The host seals that evidence with a per-attempt nonce, `run_id`, stream lengths and digests, and bound request digests. Verifiers may score the bytes only as observable output under declared authority. Verifier verdicts echo exact bindings and emit strict `omp.verifier-result/v1`. Candidate execution semantics remain untrusted unless externally observable; runner stdout/events/clocks are untrusted payloads and internally self-reported semantics remain inadmissible without source-separated observation. Tasks requiring semantic observation like cancel-async remain inadmissible until such observation is available. No cheat-proof claim is made for internally self-reported execution.
9. The append-only experiment ledger may journal immutable artifacts and worker reports locally, but it has no admission, calibration, recommendation, or routing authority. Every consumer revalidates source artifacts and recomputes worker outcomes.

The public fixed profile contains seven independently reviewed, `calibration-required` Terminal-Bench 2.1 anchors across the populated `default`, `task`, `slow`, `plan`, and `advisor` packs; all remain routing-ineligible. The `smol` pack remains an empty `authoring` queue, and the other four role packs have not yet been created. Public repository validation rejects holdout content; confidential holdouts require an author-independent split outside the public tree. The `synthetic-fixture` source kind exists only for provider-disabled smoke tests and is mechanically excluded from all task packs. Raw runtime outputs, journals, image archives, and imported candidates stay under ignored `.rolebench/` or an external private artifact store. Only the small, normalized admission reports, qualifications, and verifier fixtures explicitly referenced by a reviewed public pack may be published.

### Verifier philosophy by role

- `default`, `task`, `slow`: executable repository/task verifiers.
- `plan`: structural checks plus downstream executability; avoid pure prose preference scoring.
- `advisor`: seeded defects and known risk labels; measure recall and false positives.
- `smol`: executable bounded tasks with explicit cost and latency ceilings.
- `tiny`: exact classification/extraction/metadata outputs.
- `commit`: changed-file facts and repository convention checks; penalize invented claims.
- `vision`: objective image-grounded answers and capability checks.
- `designer`: functional browser/DOM checks plus a separately identified visual-quality evaluator.

LLM judges may supplement objective verification, but cannot be the only verifier for core coding success.

### Minimum anchors and adaptive trials

For every active `role × route` candidate:

1. Run a small, fixed anchor set that covers the role contract.
2. Fit or update the capability posterior.
3. Determine whether uncertainty can change eligibility or allocation materially.
4. If so, select the next task that maximizes expected allocation-regret reduction per dollar/minute.
5. Stop when additional trials are unlikely to change the eligibility set or allocation weights beyond a configured tolerance.

Routes that fail a hard capability check do not receive paid diagnostic trials for that role. Roles with zero expected demand do not consume benchmark budget until activated.

### Statistical model

The initial estimator can use:

- a hierarchical Bernoulli/logistic model for verifier success;
- role, route, task-difficulty, and capability-tag effects;
- partial pooling across related roles without collapsing their outputs;
- hierarchical log-time and log-cost models;
- a separate operational-failure model; and
- weak public-benchmark priors that are updated by direct RoleBench evidence.

Published Terminal-Bench aggregate results are priors or calibration context, not direct OMP role scores. Direct evidence comes from executing pinned applicable tasks under the recorded RoleBench harness against the exact candidate route. Model, provider, effort, transport, harness, and OMP settings that are not identical must not be treated as the same route or result.

### Validation target

Optimize and validate for **allocation correctness**, not leaderboard correlation.

Primary held-out measures:

- quality-floor violations;
- qualified routes incorrectly excluded;
- agreement with full-information eligibility decisions;
- maximum-capacity-utilization regret;
- cost and latency regret;
- policy-weight instability under posterior draws;
- provider/failure-domain concentration; and
- policy churn.

Provisional go-live gates for a diagnostic pack should include:

- at least 90% held-out eligibility-decision agreement;
- no systematic role or provider-family bias in calibration;
- at most five percentage points of maximum-utilization regret versus the full-information allocation; and
- documented uncertainty or refusal to allocate when those standards are not met.

These thresholds should be frozen only after the initial calibration pilot.

## Capability and reliability gating

A route may receive traffic for role `r` only when:

\[
P(q_{r,m} \ge q^{\min}_r \mid E) \ge \alpha_r
\]

and its reliability lower bound clears the role's reliability floor.

Additional hard gates can include:

- image input and detail support;
- tool calling and strict schema compatibility;
- minimum context/output limits;
- required transport behavior;
- acceptable trajectory-integrity status;
- maximum failure-domain concentration;
- evidence freshness; and
- route availability/authentication.

An unproven route is not treated as qualified. The optimizer may request more diagnostics if the route could materially improve the policy.

## Allocation optimizer

Let `x[r,m]` be the fraction of role `r` traffic allocated to route `m`, and `d[r]` expected demand for the role.

For each role:

\[
\sum_m x_{r,m}=1
\]

For every capacity pool `k` and quota window `w`:

\[
\sum_{r,m} d_r x_{r,m} u_{r,m,k,w} \le z C_{k,w}
\]

where:

- `u[r,m,k,w]` is expected consumption from pool/window `k,w`;
- `C[k,w]` is usable remaining capacity after reserves; and
- `z` is maximum normalized utilization to minimize.

The optimizer also supports:

- minimum/maximum route shares;
- provider and failure-domain concentration limits;
- concurrency constraints;
- API-dollar budgets;
- subscription reset windows;
- GPU occupied-time limits;
- capacity reserves;
- minimum evidence freshness;
- optional diversification requirements; and
- a bounded change from the previous policy.

Cost is measured as expected cost per accepted solve, not merely cost per request:

\[
\text{cost per accepted solve}_{r,m}=\frac{E[\text{request cost}_{r,m}]}{P(\text{accepted solve}_{r,m})}
\]

Subscription routes should report both marginal cash cost and an API-equivalent value or quota opportunity cost. They must not be labeled free.

## Deterministic OMP runtime

### Resolution precedence

1. An explicit user-selected concrete model/route bypasses automatic allocation.
2. A valid active policy handles canonical role requests.
3. If no compatible policy exists, current static `modelRoles` resolution applies.
4. Existing OMP priority/default and recovery behavior remains the final fallback.

Automatic routing must never silently override an explicit user request.

### Selection algorithm

Use deterministic weighted rendezvous hashing over:

```text
policy_id + policy_seed + role + routing_key + route_id
```

Advantages:

- deterministic weighted allocation;
- a complete deterministic ranking for fallback;
- minimal reassignment when a route becomes unavailable; and
- no shared mutable round-robin state.

The caller provides a stable routing key. Default stickiness scopes can be:

- main-session ID for `default`, `slow`, and `plan` sessions;
- child-session or delegated-operation ID for `task`;
- operation ID for `tiny`, `commit`, `vision`, `designer`, and one-shot calls; and
- configurable scope in the policy where a consumer needs different behavior.

### Health and capacity behavior

The runtime filters the policy's routes by:

- resolvable model and compatible OMP version;
- usable authentication;
- hard provider/account blocks;
- explicit depletion/reserve policy;
- required capabilities; and
- policy validity.

It then selects the highest-ranked eligible route. If the primary route fails before producing replay-unsafe output, OMP follows the policy's deterministic ranking and existing recovery machinery. Route unavailability, provider failure, credential rotation, and model fallback remain distinct events.

Reproducibility is defined by:

```text
policy version + role + routing key + recorded capacity/health epoch
```

### Modes

- `off`: current OMP behavior only.
- `shadow`: compute and log policy decisions without affecting the selected model.
- `enforce`: execute policy decisions.

New installations and initial deployments default to `off`. A policy should spend meaningful time in `shadow` before enforcement.

### Explainability

OMP should expose an explanation surface such as:

```text
omp route status
omp route explain @task --key <optional-key>
omp route decisions --last 20
```

An explanation reports the policy version, qualifying evidence, candidate weights, health/capacity exclusions, chosen route, and fallback ranking without exposing secrets.

## Required OMP changes

The present extension API can inspect/resolve models and change the current session model, but it does not expose a role-aware interception point: `before_agent_start` does not identify the requested role, and role resolution occurs across several core consumers. A pure extension therefore cannot implement the complete generic router safely today.

The preferred core change is a centralized, context-aware role-routing seam. Conceptually:

```ts
interface RoleRouteRequest {
  role: string;
  routingKey: string;
  consumer: string;
  explicitOverride?: string;
  requiredCapabilities?: string[];
}

interface RoleRouteDecision {
  role: string;
  route: string;
  thinking?: string;
  policyId?: string;
  rankedFallbacks?: string[];
  reason: string;
}
```

All built-in role consumers should resolve through the seam while preserving existing behavior when no policy provider is installed or enabled.

The runtime implementation can be native or provided by a packaged OMP extension after the seam exists. For the first release, native policy validation and selection is preferable because routing affects every role consumer and must work consistently in interactive, print, RPC, task, eval, and background paths.

## Pull-request strategy

OMP's contribution guide requires prior Discord discussion for major architectural changes and focused PRs with end-to-end verification. Discuss this design with maintainers before implementation. If the team intends to submit the change, do not create a duplicate implementation issue first.

Recommended sequence:

### PR 1 — Context-aware role-resolution seam

- Centralize role-route resolution behind one interface.
- Preserve current single-selector behavior exactly.
- Carry canonical role, consumer, explicit-override, and routing-key context.
- Add deterministic unit/replay fixtures.
- No policy file and no production behavior change.

### PR 2 — Route-decision events and shadow provider

- Add versioned route-decision event shape.
- Add a policy-provider interface or native loader in shadow-only mode.
- Add explanation output and telemetry redaction.
- Validate that shadow mode never changes the executed route.

### PR 3 — Versioned policy enforcement

- Validate and atomically activate `omp.route-policy/v1`.
- Implement weighted rendezvous selection and stickiness.
- Add compatibility, validity, and explicit-override rules.
- Integrate OMP auth/health gating and fallback ranking.
- Keep enforcement opt-in.

### PR 4 — Stable benchmark/introspection interface, only if needed

- Machine-readable role registry/digest.
- Candidate-route and capability inventory without secrets.
- Stable normalized trial/cost/usage output.
- Generic RoleBench/Harbor adapter support in metaharness.

Each PR should be independently useful, documented, tested, and manually exercised.

## Target RoleBench repository layout and CLI

The target layout extends the current repository without moving benchmark or statistical dependencies into OMP:

```text
omp-rolebench/
├── contracts/
│   ├── role-registry.json
│   ├── roles/
│   ├── task-packs/
│   └── schemas/
├── tasks/
│   ├── terminal-bench/
│   └── omp-native/
│       └── <canonical-role>/
├── adapters/
│   └── omp/
├── src/rolebench/
│   ├── evidence/
│   ├── estimate/
│   ├── recommend/
│   ├── capacity/
│   ├── optimize/
│   └── policy/
├── fixtures/
├── tests/
├── reports/
└── docs/
```

The target CLI makes recommendation a first-class step. These commands are planned; the README is authoritative for commands implemented today.

```text
rolebench profile show default-v1
rolebench inventory
rolebench run --profile default-v1 --routes <manifest>
rolebench fit --evidence <snapshot>
rolebench recommend --capabilities <snapshot>
rolebench optimize --recommendation <snapshot> --capacity <snapshot> --demand <snapshot>
rolebench validate --policy <policy>
rolebench publish --policy <policy>
```

## Delivery workstreams

### A. Default role benchmark

- Freeze v1 built-in role contracts and required capability tags.
- Define the versioned default profile across all ten canonical roles.
- Select and pin applicable Terminal-Bench anchors.
- Build OMP-native Terminal-Bench-style diagnostics and objective verifiers for uncovered capabilities.
- Maintain author-independent held-out tasks and trajectory-integrity checks.

### B. Evidence and recommendations

- Complete the credentialless provider-proxy execution boundary.
- Ingest normalized OMP/metaharness/Harbor artifacts.
- Fit calibrated role × route capability and reliability posteriors.
- Implement adaptive task selection and stopping.
- Emit ranked per-role recommendations with coverage, uncertainty, and refusal reasons.

### C. Optional local task generation

- Build the private, opt-in session-to-task generator on top of the existing discovery envelope.
- Require de-identification and the normal independent task reviews before any generated task contributes evidence.
- Support offline regeneration for workload drift without making session access a default-workflow dependency.

### D. Allocation and OMP runtime integration

- Build optional capacity and demand snapshots, constrained allocation, and regret validation.
- Generate immutable, explainable allocation policies.
- Add the central OMP role-routing seam.
- Implement shadow decisions, policy validation, deterministic selection, health gating, telemetry, and explanation.
- Preserve explicit overrides and legacy behavior.

These workstreams share the v1 role and artifact contracts. The default benchmark and recommendation path is the first independently useful product; optional task generation and runtime policy enforcement must not block it.

## Milestones

### Milestone 0 — Contract freeze

- Agree on repository ownership.
- Freeze v1 role, route, evidence, capability, capacity, policy, and decision schemas.
- Pin the first OMP and Terminal-Bench releases.
- Define minimum default task-pack coverage for all ten canonical roles.

### Milestone 1 — Default offline recommendation

- Complete and independently review the fixed public task set for `smol`, `vision`, `designer`, `commit`, and `tiny`, building from the seven anchors already pinned across the other five role packs.
- Run several exact candidate routes without using session logs.
- Produce normalized evidence and calibrated capability snapshots.
- Emit a ranked recommendation for each covered role, or an explicit insufficient-evidence result.
- Validate the recommendation on held-out task families.

### Milestone 2 — Complete canonical profile and optional local overlay

- Expand the fixed profile and recommendation output to all ten canonical roles.
- Add opt-in session-to-task generation as a supplemental source.
- Prove that the default workflow and its validation remain reproducible without local session data.
- Recalibrate only through new immutable task, evidence, and recommendation versions.

### Milestone 3 — Capacity-aware policy and OMP shadow routing

- Solve representative multi-provider capacity scenarios using only qualified routes.
- Generate and explain a valid policy artifact.
- Load the policy into OMP and produce deterministic shadow decisions.
- Compare shadow choices with static mappings and verify replay, explicit overrides, and safe fallback.

### Milestone 4 — Controlled enforcement and adaptive calibration

- Enable one or two noncritical roles first.
- Observe quality, availability, quota consumption, and policy churn.
- Roll back atomically to static role mappings on any policy/runtime failure.
- Expand route families and validate against fuller Terminal-Bench/RoleBench runs.
- Freeze task-selection behavior only after held-out recommendation and allocation-regret gates pass.

## Acceptance criteria

### Runtime correctness

- Same policy, role, routing key, and health epoch produce the same ranked routes in every supported OMP mode.
- Explicit model selection is never overridden.
- `off` mode is behaviorally identical to current OMP.
- `shadow` mode never changes execution.
- Invalid, expired, incompatible, or partially written policies are rejected without disturbing the last valid policy.
- Every enforced decision is replayable from its event record.
- Removed/unhealthy routes cause minimal deterministic reassignment.

### Optimizer correctness

- Every nonzero allocation clears hard capability, quality, and reliability gates.
- Synthetic solver tests satisfy every capacity pool/window constraint.
- Integer policy weights sum exactly to 10,000 basis points per active role.
- Infeasible problems return an explanation rather than silently weakening quality requirements.
- Policy diffs explain changes in evidence, demand, capacity, or constraints.

### Diagnostic and recommendation validity

- The standard workflow completes without reading OMP session logs.
- The completed default profile maps every canonical role to explicit required capabilities and versioned task packs.
- Each task is mapped to one explicit role contract and capability tags.
- Objective verifiers are used wherever practical.
- Task, environment, harness, verifier, and route digests are recorded.
- Infrastructure and provider failures are distinguished from model failures.
- A recommendation identifies its evidence, coverage, uncertainty, and qualification decision.
- Insufficient coverage or confidence produces an explicit refusal rather than a guessed assignment.
- Optional session-derived tasks remain non-authoritative until the normal independent reviews and calibration gates pass.
- Held-out validation measures recommendation and allocation regret.
- No unofficial result is labeled an official Terminal-Bench score.

## Security and integrity

- Never place API keys, OAuth tokens, credential IDs, raw account identifiers, or private prompts in policies or public evidence.
- Treat raw sessions, session-derived drafts, and OMP Gym imports as private authoring inputs. Session analysis must be explicit and local rather than a default history sweep; no derived task may leave the private boundary until independent privacy and redistribution reviews approve a versioned task.
- Keep public task assets and verifier-private assets in distinct content-addressed trees and prove that agent and runner images contain only the public tree and no verifier-private tree, while verifier images contain only the verifier-private tree and no public tree. Candidate artifacts never execute in the verifier container; passive verifiers process only host-sealed runner evidence envelopes and treat runner I/O as inert untrusted bytes.
- Runner stdout, events, and clocks are untrusted payloads; candidate execution semantics may contribute evidence only through an approved source-separated observer. The committed `cancel-async-tasks` anchor uses that authority and binds it through independent verifier review.
- Keep unreviewed local manifests, raw worker reports, journals, private verifier material, and holdout content out of the public repository. Only normalized evidence explicitly referenced by a reviewed public pack may be published.
- Execute untrusted benchmark tasks in isolated environments.
- Keep credentials host-side when using Harbor containers.
- Pin task images, datasets, OMP versions, verifier code, and policy checksums.
- Redact routing telemetry by default while retaining sufficient data for replay.
- Treat policy activation like configuration deployment: validate, write atomically, retain the previous valid version, and support immediate rollback.

## Open decisions for the team

1. Should `omp.route-policy/v1` be JSON-only for canonical signing, with YAML accepted only as authoring input?
2. Does OMP core implement the full policy provider, or should a minimal core seam allow a separately packaged official extension to do so?
3. What are the first exact candidate routes for the five currently populated role packs?
4. What minimum fixed task and capability coverage makes each canonical role recommendation-eligible?
5. How should expected role demand be combined from user forecasts and observed OMP telemetry?
6. Which unknown-capacity default is appropriate for each provider class?
7. Where should signed or approved policy artifacts be published, and how should activation approval work?
8. Which role contracts may be overridden locally, and which safety or reliability floors are non-negotiable?
9. For the optional local extension, how should users select session families and sampling windows, approve generated drafts, and detect workload drift without exporting raw sessions?

## Recommendation in one sentence

**Ship `omp-rolebench` first as an open-source offline toolkit that maps a fixed Terminal-Bench-style capability profile to OMP's canonical roles and produces evidence-backed model-route recommendations; add private session-derived tasks as an optional extension, and upstream deterministic policy execution separately.**

## Current OMP references

- [Built-in model role registry](https://github.com/can1357/oh-my-pi/blob/06aecdd51f07e689e970ceaa180abe2be0c14bbb/packages/coding-agent/src/config/model-roles.ts)
- [Model roles and alias behavior](https://github.com/can1357/oh-my-pi/blob/06aecdd51f07e689e970ceaa180abe2be0c14bbb/docs/models.md)
- [Structured subagent role identity preservation](https://github.com/can1357/oh-my-pi/blob/06aecdd51f07e689e970ceaa180abe2be0c14bbb/packages/coding-agent/src/task/structured-subagent.ts)
- [Metaharness and Harbor integration](https://github.com/can1357/oh-my-pi/blob/06aecdd51f07e689e970ceaa180abe2be0c14bbb/packages/metaharness/README.md)
- [Normalized provider usage model](https://github.com/can1357/oh-my-pi/blob/06aecdd51f07e689e970ceaa180abe2be0c14bbb/packages/ai/src/usage.ts)
- [Usage-aware retry and fallback policy](https://github.com/can1357/oh-my-pi/blob/06aecdd51f07e689e970ceaa180abe2be0c14bbb/docs/non-compaction-retry-policy.md)
- [Extension API and current model-selection surface](https://github.com/can1357/oh-my-pi/blob/06aecdd51f07e689e970ceaa180abe2be0c14bbb/docs/extensions.md)
- [OMP contribution requirements](https://github.com/can1357/oh-my-pi/blob/06aecdd51f07e689e970ceaa180abe2be0c14bbb/CONTRIBUTING.md)
