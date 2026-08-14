# Benchmark-Informed Role Routing for OMP

**Status:** Partner design proposal  
**Date:** August 12, 2026  
**Working name:** OMP RoleBench + Role Router

## Executive decision

Build this as two coordinated products:

1. **A separate `omp-rolebench` repository** owns role diagnostics, benchmark orchestration, evidence, capability estimation, capacity-aware optimization, validation, and policy generation.
2. **Focused upstream OMP pull requests** add the generic runtime seam required to load, explain, shadow, and enforce a versioned role-allocation policy.

Use a fork of OMP to develop and test those pull requests, but do **not** make a permanent product fork the intended distribution model. The benchmark changes more quickly than the OMP runtime, carries heavier datasets and statistical dependencies, and should be independently versioned. OMP should remain capable of routing without containing the benchmark corpus or optimizer.

OMP already contains several useful building blocks: canonical model roles, role-alias resolution, preserved role identity on structured subagent launches, normalized provider/account usage reporting, retry and fallback behavior, a credential-distribution diagnostic, and a Harbor-backed metaharness. The project should extend those capabilities rather than replace them.

## Product definition

> Given an installation's user-selected OMP sessions, fixed anchors, role contracts, candidate model routes, quality requirements, expected demand, and live capacity, derive a private local diagnostic mix, produce a versioned allocation policy, and execute it deterministically.

The complete product loop is:

```text
user-selected OMP sessions + fixed public anchors
    ↓ private offline task discovery
OMP role contracts + installation-specific diagnostics
    ↓
benchmark evidence
    ↓
role × route capability estimates
    ↓
capacity-constrained allocation policy
    ↓
deterministic OMP role routing
    ↓
agents and workflows consume @roles
```

The system replaces arbitrary role weights and model assignments with reproducible decisions backed by measured quality, operational performance, and available capacity.

## Problem

OMP can map roles to models and recover from provider failures, but a user still has to decide manually:

- which model/provider/effort route is qualified for each role;
- how much traffic each qualified route should receive;
- how to balance several subscription, API, account, and concurrency limits;
- when a cheaper or faster model is actually good enough;
- how to update those choices after a model, provider, OMP release, or quota state changes; and
- why a particular request was routed as it was.

General leaderboards are insufficient. Terminal-Bench is valuable evidence for terminal competence, but its aggregate score does not directly answer whether a route satisfies OMP's `task`, `plan`, `vision`, `commit`, or other role contracts. It also does not incorporate the user's live capacity and expected workload.

## Goals

1. Estimate role-specific capability and uncertainty for each exact model route.
2. Use Terminal-Bench-style executable tasks where they are diagnostically appropriate.
3. Run only enough diagnostics to make a stable allocation decision.
4. Enforce role-specific capability and reliability floors.
5. Allocate traffic across provider/account capacity pools and quota windows.
6. Produce immutable, inspectable, versioned policy artifacts.
7. Route deterministically and keep decisions sticky at the appropriate scope.
8. Reuse OMP's existing model resolution, authentication, usage health, and recovery behavior.
9. Support shadow evaluation before a policy controls production traffic.
10. Preserve explicit user model selection as an intentional override.
11. Derive and refresh privacy-reviewed diagnostics from opt-in user session samples so local policies reflect the workloads that installation actually runs.

## Non-goals

- Reproduce an official Terminal-Bench score from a tiny subset.
- Build another general model leaderboard.
- Benchmark or allocate by agent name.
- Let an online LLM choose a model at request time.
- Hide all quality, cost, latency, and capacity tradeoffs inside one arbitrary composite score.
- Put benchmark tasks, large run artifacts, statistical notebooks, or optimizer dependencies into OMP core.
- Dynamically mutate routing weights on every request without publishing a new policy version.

## Core principles

### Roles—not agents—are the control-plane key

Diagnostics, capability estimates, allocation policies, and route decisions are keyed by canonical OMP model role. Agents and workflows request roles and consume the resulting route:

```text
agent/workflow → @role → role router → concrete route
```

Agent identity may be retained in telemetry to diagnose interactions, but it is not a benchmark lane or allocation dimension. If a genuinely different workload requires different thresholds or task mixes, it should become a custom role contract rather than an agent-specific exception.

### User sessions—not a universal task suite—define local relevance

RoleBench must evaluate the work a particular user gives OMP instead of assuming every installation has the same role mix, task families, or quality requirements. The operator explicitly selects local sessions for an offline authoring run. Inside that private boundary, RoleBench should identify recurring goals, tool patterns, artifacts, constraints, and failure modes, then generate minimal self-contained diagnostic drafts with proposed role bindings and capability tags. Different users can therefore produce different local task mixes and demand forecasts even when they share the same canonical role contracts.

Raw session content may be read only inside the operator-controlled local authoring boundary. It must not enter candidate metadata, benchmark evidence, policies, routing telemetry, or public artifacts. A generated draft remains private by default, must remove user-specific content, and receives no scoring or routing authority until independent privacy, redistribution, verifier, and split reviews approve a versioned task. Fixed public anchors preserve cross-installation comparability; personalized diagnostics determine whether routes fit the local workload.

This generation loop is dynamic but not online: workload drift may trigger a new offline authoring and calibration cycle, which produces new immutable task and policy versions. It never changes weights during a request. The current `scan-session` command implements only the privacy-minimized discovery envelope for one selected session; executable session-to-task synthesis remains planned work.

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

OMP currently defines ten built-in roles. These IDs are the v1 diagnostic taxonomy.

| Role | Diagnostic contract | Primary decision signal |
| --- | --- | --- |
| `default` | Broad interactive coding, terminal work, and tool use | Accepted-solve rate across representative work |
| `task` | Self-contained delegated implementation | Reliable autonomous completion |
| `slow` | Hard diagnosis, reasoning, and recovery | Quality on difficult tasks |
| `plan` | Architecture, decomposition, and sequencing | Executability and correctness of the plan |
| `advisor` | Independent review and defect discovery | Defect/risk recall with controlled false positives |
| `smol` | Bounded mechanical and lightweight work | Success within strict latency/cost budgets |
| `tiny` | Titles, memory operations, classification, and metadata | Exact output, reliability, speed, and minimal consumption |
| `commit` | Commit-message generation | Semantic coverage, convention adherence, and no invention |
| `vision` | Image-grounded and multimodal work | Correct evidence grounding and required input support |
| `designer` | UI/UX judgment and implementation | Functional correctness plus visual quality |

The runtime and policy schema should support all built-in roles from the beginning. Diagnostic spending may prioritize roles with active demand and consequential allocation decisions.

Custom roles can be added later. A custom role must either:

- inherit a built-in diagnostic contract and override explicit thresholds/demand; or
- provide its own versioned contract and diagnostic task mix.

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

The separate project owns evidence production and policy generation:

- role-contract manifests and version history;
- private, opt-in user-session analysis and local session-to-task candidate generation;
- OMP-native diagnostic tasks and executable verifiers;
- pinned references to public Terminal-Bench datasets/tasks;
- Harbor dataset packaging and the OMP benchmark adapter/configuration;
- run manifests and normalized evidence ingestion;
- task selection and adaptive stopping;
- role × route capability estimation;
- latency, reliability, token, cost, and quota-consumption models;
- capacity snapshots and demand forecasts;
- constrained optimization;
- allocation-regret validation;
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

### Task sources

1. **User-session-derived OMP-native diagnostics:** private, opt-in tasks generated from recurring work in user-selected sessions. They determine local relevance but remain private until independently reviewed and explicitly approved for redistribution.
2. **Fixed OMP-native contract anchors:** small, purpose-built tasks that preserve comparable coverage across installations, especially for `smol`, `tiny`, `commit`, `vision`, and `designer`.
3. **Terminal-Bench anchors:** pinned public tasks useful for `default`, `task`, and `slow`, with selected evidence for `plan` and `advisor` where justified.
4. **Held-out routing tests:** task families and model routes excluded from task generation, task selection, and estimator fitting, used only to measure allocation regret.

Do not copy or relabel an official Terminal-Bench dataset in a way that implies an official score. Record the exact dataset release and task digest. Report results as RoleBench estimates.

### Diagnostic-task admission and execution

The task pipeline separates private workload discovery, generation, authoring, admission, calibration, and evidence use:

1. The operator explicitly selects one or more local OMP sessions. RoleBench must not sweep all history by default or upload a session for task generation.
2. The implemented `scan-session` command reads one selected OMP JSONL session and emits a fresh opaque candidate plus entry ordinals and signal kinds only. Raw text, paths, IDs, model/provider/account fields, and stable session fingerprints are prohibited.
3. A planned local generator evaluates the selected session content only inside the private authoring boundary, identifies recurring role/capability patterns, and synthesizes minimal self-contained task drafts. It must not copy user-specific prompts, repository paths, secrets, account data, or proprietary artifacts into its output. This synthesis stage is not implemented in v1.
4. As an alternative authoring input, `import-omp-gym` parses only the public `task.toml` and `workspace/` format into a private candidate directory. It does not import OMP Gym code, follow links, infer licensing, or grant admission.
5. An operator turns a private draft into `omp.diagnostic-task/v1` with a canonical role binding, public/private asset digests, exact single-platform agent/admission/verifier image identities, objective criteria, and independent privacy, license, verifier, and split approvals.
6. `prepare-admission-run` independently re-inspects the image manifest, config ID, platform, fixed role/content labels, and in-image asset trees. The agent image must expose the exact public tree and no verifier-private tree; the verifier image must expose the exact private tree and no public tree.
7. At least two healthy runs for each distinct baseline, reference, and tamper image establish deterministic failure, success, and tamper rejection. `qualify` reparses each worker report, recomputes accounting, requires exact run/envelope/isolation/task/policy/probe-image mappings, and requires identical artifact/reward results across repeats.
8. V1 qualifications are `calibration-required`, not proof of cross-model discrimination. V1 cannot claim `admitted` or freeze a routing-eligible pack until a later version defines independently reviewed discrimination evidence. `prepare-run` therefore permits only non-holdout `calibration-only` execution and preserves the observed task/image/policy bindings in `omp.worker-run-manifest/v1`.
9. The normal rootless Docker/`runsc` worker executes both admission and calibration manifests, freezes the bounded artifact after agent exit, streams it once to the distinct networkless verifier, and emits observation/outcome records with explicit `evidence_use`. Accounting classifies otherwise-scoreable admission/calibration attempts as excluded evidence, never normal model-quality evidence.
10. The append-only experiment ledger may journal immutable artifacts and worker reports locally, but it has no admission, calibration, or routing authority. Every consumer revalidates source artifacts and recomputes worker outcomes.

Public pilot packs remain empty and routing-ineligible. Public repository validation rejects holdout content; confidential holdouts require an author-independent split outside the public tree. The `synthetic-fixture` source kind exists only for provider-disabled smoke tests and is mechanically excluded from all task packs. Runtime tasks, qualifications, reports, journals, image archives, and imported candidates stay under ignored `.rolebench/` or an external private artifact store.

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
- weak public-benchmark priors that are updated by OMP-native evidence.

Public Terminal-Bench results are priors/calibration evidence, not direct OMP role scores. Model/harness settings that are not identical must not be treated as identical routes.

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

## RoleBench repository layout

```text
omp-rolebench/
├── contracts/
│   ├── roles/
│   └── schemas/
├── tasks/
│   ├── terminal/
│   ├── tiny/
│   ├── commit/
│   ├── vision/
│   └── designer/
├── datasets/
│   └── harbor/
├── adapters/
│   └── omp/
├── rolebench/
│   ├── evidence/
│   ├── estimate/
│   ├── capacity/
│   ├── demand/
│   ├── optimize/
│   ├── validate/
│   └── policy/
├── tests/
├── reports/
└── docs/
```

Suggested CLI:

```text
rolebench inventory
rolebench run --roles task,smol,slow --routes <manifest>
rolebench fit --evidence <snapshot>
rolebench optimize --capabilities <snapshot> --capacity <snapshot> --demand <snapshot>
rolebench validate --policy <policy>
rolebench publish --policy <policy>
```

## Delivery workstreams

### A. Role contracts and diagnostics

- Freeze v1 built-in role contracts.
- Build the private opt-in session-to-task generator and drift-triggered regeneration workflow.
- Select initial Terminal-Bench anchors.
- Build OMP-native microdiagnostics and objective verifiers.
- Maintain held-out tasks and trajectory-integrity checks.

### B. Evidence, estimation, and optimizer

- Define normalized schemas.
- Ingest OMP/metaharness/Harbor artifacts.
- Fit calibrated role × route posteriors.
- Implement adaptive task selection and stopping.
- Build the constrained optimizer and regret validation.

### C. OMP runtime integration

- Align with OMP maintainers.
- Add the central role-routing seam.
- Implement shadow decisions, policy validation, deterministic selection, health gating, telemetry, and explanation.
- Preserve explicit overrides and legacy behavior.

These workstreams can proceed in parallel once the v1 data contracts and role semantics are frozen.

## Milestones

### Milestone 0 — Contract freeze

- Agree on repository ownership.
- Freeze v1 role, route, evidence, capability, capacity, policy, and decision schemas.
- Agree on the OMP core seam with maintainers.
- Pin the first OMP and Terminal-Bench releases.

### Milestone 1 — Offline vertical slice

- Derive and independently review a small personalized diagnostic set from user-selected sessions alongside fixed anchors.
- Run several candidate routes on a small `task`/`smol`/`slow` diagnostic set.
- Produce normalized evidence and capability snapshots.
- Solve a synthetic multi-provider capacity scenario.
- Generate and explain a valid policy artifact.

### Milestone 2 — OMP shadow routing

- Load the policy into OMP.
- Produce deterministic shadow decisions for real role requests.
- Compare shadow choices with current static mappings.
- Verify logging, replay, explicit overrides, and safe fallback.

### Milestone 3 — Controlled enforcement

- Enable one or two noncritical roles first.
- Observe quality, availability, quota consumption, and policy churn.
- Roll back atomically to static role mappings on any policy/runtime failure.

### Milestone 4 — Adaptive proxy calibration

- Expand role packs and route families.
- Validate against fuller Terminal-Bench/RoleBench runs.
- Freeze the first task-selection model only after held-out allocation-regret gates pass.

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

### Diagnostic validity

- Each task is mapped to an explicit role contract and capability tag.
- Objective verifiers are used wherever practical.
- Task and verifier digests are recorded.
- Infrastructure failures are distinguished from model failures.
- Held-out validation measures allocation regret.
- No unofficial result is labeled an official Terminal-Bench score.

## Security and integrity

- Never place API keys, OAuth tokens, credential IDs, raw account identifiers, or private prompts in policies or public evidence.
- Treat raw sessions, session-derived drafts, and OMP Gym imports as private authoring inputs. Session analysis must be explicit and local rather than a default history sweep; no derived task may leave the private boundary until independent privacy and redistribution reviews approve a versioned task.
- Keep public task assets and verifier-private assets in distinct content-addressed trees and prove that neither image contains the other's tree before every prepared run.
- Keep local manifests, qualifications, worker reports, journals, private verifier material, and holdout content out of the public repository.
- Execute untrusted benchmark tasks in isolated environments.
- Keep credentials host-side when using Harbor containers.
- Pin task images, datasets, OMP versions, verifier code, and policy checksums.
- Preserve trajectory-integrity/reward-hacking review where applicable.
- Redact routing telemetry by default while retaining sufficient data for replay.
- Treat policy activation like configuration deployment: validate, write atomically, retain the previous valid version, and support immediate rollback.

## Open decisions for the team

1. Should `omp.route-policy/v1` be JSON-only for canonical signing, with YAML accepted only as authoring input?
2. Does OMP core implement the full policy provider, or should a minimal core seam allow a separately packaged official extension to do so?
3. What are the first three roles and candidate routes for the calibration pilot?
4. How should expected role demand be combined from user forecasts and observed OMP telemetry?
5. Which unknown-capacity default is appropriate for each provider class?
6. Where should signed/approved policy artifacts be published and how should activation approval work?
7. Which role contracts may be overridden locally, and which safety/reliability floors are non-negotiable?
8. How should users select session families and sampling windows, approve generated drafts, and detect workload drift without exporting raw sessions?

## Recommendation in one sentence

**Develop against an OMP fork, upstream the generic deterministic role-routing runtime in focused PRs, and keep the evolving diagnostic corpus, estimator, optimizer, and policy generator in a separate `omp-rolebench` repository.**

## Current OMP references

- [Built-in model role registry](https://github.com/can1357/oh-my-pi/blob/06aecdd51f07e689e970ceaa180abe2be0c14bbb/packages/coding-agent/src/config/model-roles.ts)
- [Model roles and alias behavior](https://github.com/can1357/oh-my-pi/blob/06aecdd51f07e689e970ceaa180abe2be0c14bbb/docs/models.md)
- [Structured subagent role identity preservation](https://github.com/can1357/oh-my-pi/blob/06aecdd51f07e689e970ceaa180abe2be0c14bbb/packages/coding-agent/src/task/structured-subagent.ts)
- [Metaharness and Harbor integration](https://github.com/can1357/oh-my-pi/blob/06aecdd51f07e689e970ceaa180abe2be0c14bbb/packages/metaharness/README.md)
- [Normalized provider usage model](https://github.com/can1357/oh-my-pi/blob/06aecdd51f07e689e970ceaa180abe2be0c14bbb/packages/ai/src/usage.ts)
- [Usage-aware retry and fallback policy](https://github.com/can1357/oh-my-pi/blob/06aecdd51f07e689e970ceaa180abe2be0c14bbb/docs/non-compaction-retry-policy.md)
- [Extension API and current model-selection surface](https://github.com/can1357/oh-my-pi/blob/06aecdd51f07e689e970ceaa180abe2be0c14bbb/docs/extensions.md)
- [OMP contribution requirements](https://github.com/can1357/oh-my-pi/blob/06aecdd51f07e689e970ceaa180abe2be0c14bbb/CONTRIBUTING.md)
