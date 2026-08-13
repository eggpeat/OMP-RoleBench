# OMP RoleBench

OMP RoleBench is the contract and tooling project for the evidence and policy-generation side of benchmark-informed model routing for [Oh My Pi](https://github.com/can1357/oh-my-pi). It is designed to evaluate exact model routes against OMP role contracts, estimate capability and operational uncertainty, and generate immutable allocation policies that OMP can execute deterministically.

> **Status:** contract foundation. Role contracts, artifact validation, and fair attempt accounting are implemented. The benchmark worker, diagnostic task packs, capability estimates, capacity-aware optimization, policy generation, and OMP runtime integration are not implemented yet.

## Why this exists

OMP roles such as `@task`, `@plan`, and `@vision` describe different workloads. A general leaderboard cannot determine whether a particular provider/model/effort route is qualified for each role, nor how traffic should be distributed across subscription and API capacity.

RoleBench is intended to replace arbitrary model assignments with a reproducible control loop:

```text
OMP role contracts
    -> role-specific diagnostics
    -> benchmark evidence
    -> role x route capability estimates
    -> capacity-constrained allocation policy
    -> deterministic OMP role routing
```

The benchmark and optimizer live here. The generic policy loader and runtime router belong upstream in OMP. OMP must not depend on RoleBench, its datasets, Harbor, or statistical tooling to serve a normal request.

## How scoring stays fair

A benchmark can go wrong for different reasons:

1. The model produced the wrong result.
2. The testing system broke.
3. The model provider was unavailable.

Only the first case lowers the model's quality score. A broken container, provider outage, missing result, or grader crash is reported as a system problem instead. Suspicious or incomplete runs are set aside for review.

RoleBench therefore reports two different things:

- **Quality:** how often the model succeeded when a test completed normally.
- **Run reliability:** how often the provider and testing system produced a usable result.

This prevents infrastructure trouble from looking like poor model quality without hiding that the trouble happened.

### Worker status

The isolated Docker/gVisor worker is still planned. Before it is used for scored benchmarks, it must pass fault tests proving that worker failures do not change model quality, plus speed and compatibility checks on representative tasks. Nothing in the current release launches containers or calls a model provider.

## Design principles

- **Roles, not agents.** Benchmark and allocation decisions use canonical OMP model roles as their control-plane key.
- **Exact routes, not model names.** Provider, model, thinking effort, transport/upstream, aggregate capacity pool, and OMP version are part of route identity.
- **Quality is a constraint.** Cost, latency, or unused quota cannot qualify a route that fails capability or reliability requirements.
- **Evidence retains uncertainty.** Estimates include coverage, freshness, and confidence rather than collapsing every tradeoff into one score.
- **Policies are immutable snapshots.** Runtime health can temporarily exclude a route; material evidence, demand, or capacity changes produce a new policy version.
- **Credentials remain provider-managed.** Policies may identify an opaque aggregate capacity pool, but never an account or credential.
- **Explicit user choices win.** Automatic routing must not override a concrete model selection.

## Built-in roles

V1 covers all ten roles from OMP's canonical role registry:

| Role | Diagnostic focus |
| --- | --- |
| `default` | Broad interactive coding, terminal work, and tool use |
| `smol` | Correct bounded work under strict latency and consumption budgets |
| `slow` | Difficult diagnosis, reasoning, and recovery |
| `vision` | Image-grounded multimodal work |
| `plan` | Executable architecture, decomposition, and sequencing |
| `designer` | Functional UI implementation and visual quality |
| `commit` | Semantic commit-message coverage without invention |
| `tiny` | Exact metadata, extraction, and classification |
| `task` | Autonomous delegated implementation |
| `advisor` | Defect and risk recall with controlled false positives |

The source-of-truth manifests are in [`contracts/roles`](contracts/roles), and their pinned OMP provenance is recorded in [`contracts/role-registry.json`](contracts/role-registry.json). Thresholds remain `calibration-required` until benchmark evidence supports freezing them.

## Quick start

RoleBench currently requires Python 3.12 or newer.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .

rolebench contracts validate
python -m unittest discover -s tests -v
```

The package exposes these implemented commands:

```text
rolebench [--root PATH] contracts validate [--json]
rolebench [--root PATH] contracts digest [--json]
rolebench [--root PATH] contracts show ROLE
rolebench [--root PATH] artifacts validate SCHEMA PATH [--json]
rolebench [--root PATH] accounting classify OBSERVATION
rolebench [--root PATH] accounting summarize OUTCOME... [--json]
```

Examples:

```bash
rolebench contracts show advisor
rolebench contracts digest --json
rolebench artifacts validate route-policy path/to/policy.json --json
rolebench accounting classify path/to/observation.json
rolebench accounting summarize path/to/outcome-*.json
```

Artifact schema names are the filenames in [`contracts/schemas`](contracts/schemas) without `.schema.json`. Attempt accounting uses `attempt-observation` for facts collected from a run and `attempt-outcome` for the decision about whether that run counts. Other schemas include `route`, `evidence-row`, `capability-snapshot`, `capacity-snapshot`, `demand-snapshot`, `route-policy`, and `routing-decision`.

## Repository layout

```text
contracts/
  role-registry.json     Canonical built-in role registry and OMP provenance
  roles/                 Versioned diagnostic contracts for all ten roles
  schemas/               Draft 2020-12 artifact schemas
docs/
  OMP_BENCHMARK_INFORMED_ROLE_ROUTING_SPEC.md
src/rolebench/
  cli.py                 Command-line interface
  accounting_rules.py    Shared reason and scoring invariants
  accounting.py          Fair attempt classification and quality summaries
  contracts.py           Loading, canonicalization, validation, and semantics
tests/
  test_accounting.py     Attempt fault, scoring, and CLI behavior tests
  test_contracts.py      Contract, artifact, CLI, and failure-path tests
```

Large benchmark outputs do not belong in Git. Keep raw run artifacts in an external run directory or artifact store. Commit only small, intentional, license-compatible normalized fixtures.

## Artifact contracts

Committed schemas use JSON Schema Draft 2020-12 and namespaced versions such as `omp.route-policy/v1`. Semantic validation supplements JSON Schema where cross-field rules are required. For example, each active role in a policy must have exactly 10,000 positive integer basis points, unique route IDs, ordered validity timestamps, and an explicit emergency fallback order independent of weighted allocation.

The canonical contract digest covers the registry and all ten role manifests in registry order. It is stable across JSON whitespace and object-key ordering.

## Roadmap

1. Freeze all ten v1 role contracts and cross-repository artifact contracts.
2. Build OMP-native objective diagnostics for every role and pin applicable Terminal-Bench anchors.
3. Build Harbor/OMP run manifests and feed their normalized observations through the implemented attempt-accounting checks.
4. Estimate calibrated `role x route` capability, reliability, cost, latency, and quota consumption.
5. Add capacity and demand snapshots plus the constrained allocation optimizer.
6. Validate allocation regret on held-out tasks and posterior draws.
7. Generate and explain immutable `omp.route-policy/v1` artifacts.
8. Integrate policy shadowing and enforcement through focused upstream OMP changes.

See the [full design specification](docs/OMP_BENCHMARK_INFORMED_ROLE_ROUTING_SPEC.md) for data contracts, statistical methodology, optimization constraints, runtime behavior, security requirements, and milestones.

## Contributing

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before changing role contracts, schemas, benchmark tasks, or policy behavior. Contract changes require focused tests and must preserve privacy, provenance, and reproducibility.

## Security and privacy

Never commit API keys, OAuth tokens, credential IDs, raw account identifiers, private prompts, or unredacted trajectories. Run untrusted benchmark tasks in isolated environments and keep provider credentials host-side. Please report a suspected leak privately to the repository owner rather than opening a public issue.
