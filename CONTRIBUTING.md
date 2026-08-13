# Contributing to OMP RoleBench

RoleBench turns benchmark evidence, capability requirements, demand, and capacity into reviewable OMP role-allocation policies. Contributions must preserve three properties: **measured quality**, **reproducibility**, and **data minimization**.

The project is currently building its contract and diagnostic layers. Read the [design specification](docs/OMP_BENCHMARK_INFORMED_ROLE_ROUTING_SPEC.md) before proposing a schema, estimator, optimizer, or OMP integration change.

## Development setup

Requirements:

- Python 3.12 or newer
- Git
- Docker/Harbor only when working on executable benchmark tasks; they are not required for contract work

Install an editable checkout:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Run the current required checks:

```bash
rolebench contracts validate
python -m unittest discover -s tests -v
```

Validate a standalone artifact with:

```bash
rolebench artifacts validate route-policy path/to/policy.json --json
```

## Workflow

1. Start from the current remote `main`.
2. Create a focused branch such as `feat/task-diagnostics`, `fix/policy-validation`, or `docs/evidence-format`.
3. Keep each commit coherent. Do not mix contract changes, benchmark-result updates, and unrelated refactors.
4. Add or update behavior-focused tests with the implementation.
5. Run the focused command or benchmark scenario that exercises the changed path.
6. For contributor branches or changes that benefit from independent approval, open a pull request against `main` using the checklist below. Maintainers may fast-forward a reviewed branch directly for small, low-risk repository maintenance.

`main` requires linear history and blocks force pushes and deletion. Direct maintainer integration is allowed; substantial contract, benchmark-methodology, security, or OMP runtime-boundary changes should still use a pull request. Prefer additive, reviewable contract changes over large cross-layer rewrites.

For a substantial design change, open an issue or draft proposal before implementation. Include the affected artifact versions, compatibility impact, validation plan, and ownership boundary between RoleBench and OMP.

## Architecture boundary

RoleBench owns:

- role contracts and diagnostic packs;
- benchmark orchestration and normalized evidence;
- capability, reliability, latency, cost, and quota-consumption estimates;
- demand and aggregate capacity snapshots;
- constrained allocation and regret validation; and
- immutable policy generation and explanation.

OMP owns:

- the runtime role registry and aliases;
- model/provider resolution and authentication;
- credential selection, health, and recovery;
- policy loading, compatibility checks, and deterministic route selection; and
- runtime decision telemetry and explicit user overrides.

Do not make OMP import RoleBench's datasets, estimator, optimizer, Harbor integration, or statistical dependencies. Do not make a RoleBench policy select a credential or account. `capacity_pool` is an opaque aggregate accounting boundary; credential choice remains provider-managed inside OMP.

## Contract changes

Schemas live in [`contracts/schemas`](contracts/schemas) and use JSON Schema Draft 2020-12.

When changing a contract:

- keep `additionalProperties: false` unless extensibility is deliberate and documented;
- use a namespaced `schema_version`, such as `omp.evidence-row/v1`;
- retain explicit timestamps, source versions, and content digests needed for replay;
- enforce cross-field invariants in `src/rolebench/contracts.py` when JSON Schema cannot express them;
- add valid and invalid artifact fixtures that prove observable behavior;
- update the design documentation when semantics change; and
- explain compatibility and migration in the pull request.

A change that removes a field, changes its meaning, tightens accepted values incompatibly, or adds a required field needs a new schema version. Do not silently redefine a published `v1` artifact. Additive compatible changes still require a deliberate review because canonical checksums and consumers may be affected.

JSON is the canonical committed format for contracts and policy artifacts. Authoring conveniences may be introduced later, but published artifacts must have an unambiguous canonical JSON representation.

### Route policies

For every active role, semantic validation must preserve:

- a nonempty set of qualified routes;
- positive integer `weight_bps` values totaling exactly 10,000;
- unique route IDs;
- an explicit emergency fallback order independent of weighted allocation;
- `created_at <= valid_from < valid_until`; and
- references to immutable role, evidence, capability, capacity, and demand snapshots.

Weights are optimizer output, not hand-authored quality scores. A nonzero allocation must clear the role's frozen capability and reliability requirements.

### Routing decisions

Decision artifacts must retain enough information for deterministic explanation and replay without retaining sensitive identity data. Candidate eligibility, reason codes, rank, policy checksum, role, health/capacity epoch, explicit override state, selection, fallback ranking, and recovery outcome belong in the record. Raw routing keys, prompts, account labels, credential IDs, tokens, and error bodies do not.

## Role contracts

The built-in registry mirrors OMP's canonical role list. A registry update must:

- pin the exact OMP repository revision, source path, and symbol;
- preserve exact role order and complete manifest coverage;
- update the registry schema and validation tests together; and
- explain how the OMP change affects diagnostics and policy compatibility.

Do not introduce agent-specific benchmark lanes. If a workload genuinely needs distinct thresholds or task composition, propose a custom role contract with explicit semantics.

New or revised numerical thresholds require calibration evidence. Until then, use:

```json
{
  "status": "calibration-required",
  "quality_floor": null,
  "reliability_floor": null,
  "confidence": null,
  "latency_slo_seconds": null
}
```

A `frozen` threshold change must cite the capability snapshot, held-out validation, uncertainty analysis, and expected policy impact.

## Benchmark tasks and verifiers

Every diagnostic task must declare:

- the role contract and capability tags it exercises;
- task source, version, and content digest;
- environment/image and runner versions;
- objective success criteria where practical;
- infrastructure-failure classification; and
- any license or redistribution constraints.

Verifier guidance by role:

- `default`, `task`, and `slow`: executable repository/task verification;
- `plan`: structural checks plus downstream executability;
- `advisor`: seeded defects and risks, measuring recall and false positives;
- `smol`: objective correctness within explicit latency and consumption budgets;
- `tiny`: exact structured outputs;
- `commit`: changed-file facts and repository conventions, penalizing invention;
- `vision`: objective image-grounded evidence and modality support;
- `designer`: functional browser/DOM checks plus separately identified visual evaluation.

LLM judges may be supplemental when a contract permits them, but they must not be the only verifier for core coding correctness. Preserve trajectory-integrity and reward-hacking review. Distinguish model failures from runner, provider, image, and verifier failures.

Terminal-Bench tasks are pinned external evidence. Do not label RoleBench results as official Terminal-Bench scores unless the official harness, dataset, configuration, and reporting requirements are satisfied exactly.

## Evidence and generated artifacts

Raw run outputs should live outside Git in a configured run directory or artifact store. Do not commit large trajectories, container filesystems, databases, or provider logs.

Small normalized fixtures may be committed when they are:

- necessary for deterministic tests;
- intentionally curated and reviewed;
- license-compatible;
- scrubbed of secrets and private content; and
- accompanied by source/version/digest provenance.

Do not manually edit generated capability, capacity, demand, or policy snapshots and present them as reproducible output. Record the producing command, code version, input digests, and seed.

## Security and privacy

Never commit or publish:

- API keys, bearer tokens, OAuth access or refresh tokens;
- credential IDs or credential hashes;
- email addresses, account labels, or raw account identifiers;
- private prompts, responses, or unredacted trajectories;
- private repository paths or tool output not intentionally included in a task fixture; or
- live provider error bodies that may contain sensitive request data.

Run untrusted benchmark tasks in isolated containers. Keep credentials host-side when using Harbor and route provider traffic through the approved gateway. Pin task images and verifier code. Treat policy activation and publishing like configuration deployment: validate checksums, write atomically, retain the previous valid version, and support rollback.

Report a suspected credential or private-data leak directly to the repository owner. Do not open a public issue containing sensitive details.

## Pull request checklist

A pull request should state:

- [ ] Problem and intended behavior
- [ ] Files and contract versions affected
- [ ] Compatibility or migration impact
- [ ] Security/privacy impact
- [ ] Exact validation commands and observed results
- [ ] Benchmark task, dataset, route, OMP, runner, and verifier versions when applicable
- [ ] Whether outputs are measured evidence, synthetic fixtures, or inference
- [ ] Remaining limitations or unverified paths

Reviewers should be able to reproduce a contract or policy result from pinned inputs without access to another contributor's credentials or local session history.
