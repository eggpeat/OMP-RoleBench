# Contributing to OMP RoleBench

RoleBench turns a fixed OMP role benchmark profile and exact candidate model routes into reviewable evidence and ranked model-to-role recommendations. Optional capacity inputs can extend qualified recommendations into allocation policies; optional session-derived tasks can extend the default benchmark profile. Contributions must preserve four properties: **measured quality**, **reproducibility**, **canonical defaults**, and **data minimization**.

The project is currently building its worker, calibration, and diagnostic-authoring layers. It ships 14 reviewed, routing-ineligible anchors across all ten fixed role packs, but not provider-backed evaluation, model-route recommendations, session-to-task synthesis, an optimizer, or OMP runtime integration. Read the [target architecture](docs/OMP_BENCHMARK_INFORMED_ROLE_ROUTING_SPEC.md) before proposing a schema, benchmark-profile, estimator, optimizer, or OMP integration change, and keep planned behavior distinct from implemented behavior in user-facing documentation.

## License of contributions

The repository is licensed under the [MIT License](LICENSE). Unless a separate written agreement or an explicitly identified third-party asset license applies, contributions are submitted under the same MIT terms. Benchmark tasks, datasets, and imported assets must retain their own source, license, and redistribution provenance; the repository license does not override those terms.

## Development setup

Requirements:

- Python 3.12 or newer
- Git
- Rootless Docker, gVisor `runsc`, RootlessKit, subordinate UID/GID mappings, and delegated cgroup v2 controllers only when changing the executable worker; they are not required for contract or accounting work

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

Worker contributors must also follow the [rootless Docker/`runsc` setup](README.md#rootless-dockerrunsc-worker-setup). Install the repository's `scripts/rolebench-runsc-wrapper` beside the real `runsc`, register that absolute wrapper path as Docker's `runsc` runtime, and verify the host before any runtime scenario:

```bash
rolebench worker doctor contracts/scored-worker-policy-v2.json
```

The doctor must report every prerequisite as `PASS`, including the local rootless Docker socket and `runsc resource enforcement`. The worker always targets `/run/user/$(id -u)/docker.sock` explicitly. Do not weaken the policy, omit OCI resource flags, switch to privileged/rootful or remote Docker, or use raw `runsc` to make a failing host pass. Runtime manifests must use immutable repository digests for distinct agent and verifier images; never commit local manifests or runtime artifacts.

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

- canonical role contracts and the fixed default benchmark profile;
- versioned task packs, benchmark orchestration, and normalized evidence;
- optional private session-to-task candidate generation;
- capability, reliability, latency, cost, and quota-consumption estimates;
- ranked model-route qualifications and recommendations;
- optional demand and aggregate capacity snapshots;
- constrained allocation and regret validation; and
- immutable policy generation and explanation.

OMP owns:

- the runtime role registry and aliases;
- model/provider resolution and authentication;
- credential selection, health, and recovery;
- policy loading, compatibility checks, and deterministic route selection; and
- runtime decision telemetry and explicit user overrides.

The offline recommendation workflow must not require OMP session history, capacity data, a generated allocation policy, or runtime integration. Do not make OMP import RoleBench's datasets, estimator, optimizer, Harbor integration, or statistical dependencies. Do not make a RoleBench policy select a credential or account. `capacity_pool` is an opaque aggregate accounting boundary; credential choice remains provider-managed inside OMP.

## Contract changes

Schemas live in [`contracts/schemas`](contracts/schemas) and use JSON Schema Draft 2020-12.

When changing a contract:

- keep `additionalProperties: false` unless extensibility is deliberate and documented;
- use a namespaced `schema_version`, such as `omp.evidence-row/v1`;
- retain explicit timestamps, source versions, and content digests needed for replay;
- enforce cross-field invariants in `src/rolebench/contracts.py` when JSON Schema cannot express them;
- add valid and invalid artifact fixtures that prove observable behavior;
- update the target architecture when semantics change;
- update the README's current-status and user-workflow claims only when the shipped behavior supports them; and
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

### Attempt accounting

`omp.attempt-observation/v1` records facts from one run. `omp.attempt-outcome/v1` is generated from those facts and says whether the run affects model quality. Do not infer an outcome directly from a container exit code, a missing reward, or the older `evidence-row` failure text.

Preserve these rules:

- only valid `accepted` and `rejected` outcomes count toward model quality;
- provider, worker, dependency, runtime, and grader failures remain visible but do not lower quality;
- a healthy model deadline or per-attempt resource limit is a scored rejection;
- operator cancellation does not count;
- missing or malformed grader results do not count; and
- integrity, sandbox, or suspected cheating concerns are quarantined.

The classifier in `src/rolebench/accounting.py` is deterministic. New failure signals require a contract update and a focused fixture proving both their classification and whether they count.

### Scored worker policy and runtime

`omp.scored-worker-policy/v2` is the current fail-closed contract for the scored worker; `v1` is preserved only for replaying legacy two-container manifests. Keep the canonical policy and schema synchronized. Changes must retain rootless Docker with `runsc`, non-root and non-privileged execution, dropped capabilities, no host namespaces/devices/mounts, a read-only root filesystem, ephemeral bounded scratch, provider-proxy-only networking without credentials, the three-container isolation pipeline (agent -> candidate runner -> passive verifier) with immutable artifact and evidence handoffs, candidate artifact execution prevention inside the verifier, an isolated networkless verifier, and accounting that excludes infrastructure, runner, and verifier failures from model quality. The artifact is supplied only to and may execute only in the runner. Runner output may reflect artifact bytes, but the verifier receives them only as bounded inert untrusted data inside host-framed evidence and may score them only as observable output under declared authority. Candidate execution semantics remain untrusted unless externally observable; runner stdout/events/clocks are untrusted payloads and internally self-reported semantics remain inadmissible without source-separated observation. Tasks requiring semantic observation like cancel-async remain inadmissible until such observation is available.

Run the deterministic no-model gate after changing the worker policy or accounting:

```bash
rolebench worker fault-check contracts/scored-worker-policy-v2.json
```

A clean result covers all 29 accounting reason codes with 4 scored controls, 18 retryable system failures, 5 quarantined controls, 1 cancellation, 1 excluded-evidence control, and zero external calls. The gate is synthetic: it validates policy and accounting behavior but does not prove installed runtime behavior.

Changes to `worker.py`, `scripts/rolebench-runsc-wrapper`, the manifest contract, or the fixture images must also run the focused worker tests, a clean doctor, the provider-disabled fixture manifest, and representative observed failure probes. Persist each exercised report with `rolebench worker run MANIFEST --report .rolebench/REPORT.json`; report creation is exclusive and never overwrites prior evidence. The runtime result must show `external_provider_calls: 0`, `resource_enforcement: true`, three distinct images, immutable agent-runner and runner-verifier handoffs, and the expected unscored accounting result. Provider-proxy integration and representative live model-task compatibility remain required before this worker can execute scored provider-backed benchmarks.

## Role contracts and the default profile

The built-in registry mirrors OMP's canonical role list. A registry update must:

- pin the exact OMP repository revision, source path, and symbol;
- preserve exact role order and complete manifest coverage;
- update the registry schema and validation tests together; and
- explain how the OMP change affects diagnostics, recommendations, and policy compatibility.

The completed default benchmark profile must map every built-in role to versioned task packs whose capability tags cover that role's required capabilities. A missing or uncalibrated pack makes the role recommendation-ineligible; it must not make session access mandatory or be hidden by an inferred fallback. Do not add empty packs merely to claim coverage.

Do not introduce agent-specific benchmark lanes. If a workload genuinely needs distinct thresholds or task composition, propose a custom role contract with explicit semantics. Optional session-derived tasks remain supplements to a built-in or custom contract, not a new implicit role taxonomy.

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

The default profile uses two kinds of scored task:

1. pinned Terminal-Bench anchors where the task directly exercises an OMP role capability; and
2. fixed OMP-native Terminal-Bench-style tasks for role capabilities the public benchmark does not cover.

Optional session-derived diagnostics are a third authoring source, not part of the standard workflow. Held-out task families are validation inputs and must remain outside task selection and estimator fitting.

Every current `omp.diagnostic-task/v2` artifact must declare:

- its role contract, task mix, capability tags, difficulty, and partition;
- task and source versions plus content and source digests;
- separately content-addressed public and verifier-private assets;
- the policy reference and exact agent, admission-agent, runner, and verifier image, config, platform, and asset-tree identities;
- objective criteria, scoring mode, and the fixed unscored failure classes; and
- authorship, independent reviews, license expression, and redistribution status.

For `source.kind: "terminal-bench"`, `source.version` is the immutable Harbor dataset package reference with a numeric revision, `source.task` is the exact Harbor task package name, `source.digest_sha256` is that task version's Harbor content hash, and `source.dataset_digest_sha256` is the dataset version's Harbor content hash. Store both hashes as lowercase hexadecimal without the `sha256:` prefix. Mutable references such as `@latest` are invalid.

The v2 diagnostic-task object binds the executable environment through exact agent, admission-agent, runner, and verifier image identities and config digests, while the public and verifier-private tree digests bind task content. Any future contract that changes these provenance semantics must version the schema and its consumers first.

### Diagnostic-task authoring and admission

Task discovery is not admission. Curated public anchors, purpose-built OMP-native tasks, OMP Gym imports, and explicitly selected local sessions all enter the same source-agnostic review path. Keep source sessions, imported workspaces, private repositories, candidate records, generated drafts, and every run artifact under ignored `.rolebench/` or another private artifact store.

The implemented `rolebench tasks scan-session` command returns a versioned privacy-minimized candidate. It may expose no source-derived detail beyond `entry_count`, which counts parsed v3 message entries, and signal objects whose `ordinal` indexes those message entries and whose `kind` is deterministic. It must not emit raw prompts, responses, tool payloads, paths, session IDs, model/provider/account identifiers, or stable source fingerprints.

`rolebench tasks import-omp-gym` is a one-way parser for the public `task.toml` plus `workspace/` format. It must not import OMP Gym code, follow links, infer redistribution rights, or treat successful parsing as approval.

The planned local session-to-task generator is optional and may inspect only sessions the operator explicitly selects. It may read selected content only inside the operator-controlled private authoring boundary, where it should identify recurring goals, tool patterns, constraints, and failure modes and synthesize minimal self-contained drafts with proposed canonical role and capability tags. It must remove user-specific text, paths, secrets, account data, and proprietary artifacts rather than replay or lightly paraphrase a session. Generated drafts supplement the fixed profile and remain private and non-authoritative until the normal independent reviews approve a versioned task. This generator is not implemented in v1.

A versioned task may proceed only after explicit, independent privacy, license, verifier, and split reviews. The author cannot perform those reviews. Redistribution must be permitted before a license review can approve the task. Public and verifier-private trees are separately content-addressed; agent and runner images must contain the exact public tree and no verifier-private root, while the verifier image must contain the exact private tree and no public root. Every image reference, OCI manifest/config digest, platform, fixed role/content/stage label, task digest, policy digest, and review binding is re-observed before run preparation.

Admission uses at least two healthy reports for each distinct baseline, reference, and tamper image. Baseline and tamper probes must reject with identical artifacts/rewards across repeats; reference probes must accept with identical artifacts/rewards. Qualification reparses every report, recomputes its outcome, and checks exact run, isolation, task, policy, and probe-image mappings, emitting `evaluation_provenance` and `runner_isolation`. V1 qualifications remain `calibration-required`; V1 deliberately cannot claim `admitted` or freeze a routing-eligible pack because no reviewed cross-model discrimination-evidence contract exists yet. They permit only `calibration-only` preparation, and those outcomes are excluded from normal model-quality scoring. Holdout tasks cannot be prepared through this path. `synthetic-fixture` sources remain smoke-only and cannot enter any pack.

The append-only experiment ledger is a local, hash-chained journal. It is never admission, calibration, or routing authority. Pack verification and every later evidence consumer must independently reload and validate task, qualification, image, and report artifacts. Do not commit local manifests, qualifications, reports, journals, image archives, or private verifier assets. A public pack may reference small normalized admission reports, qualifications, and verifier fixtures promoted under `contracts/tasks/` only when they are intentionally curated, independently reviewed, license-compatible, scrubbed of private content, content-addressed, and required for deterministic pack verification.

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

The local workflow has explicit trust boundaries:

- reviewer IDs and evidence digests are human-governed attestations, not authenticated identities or signatures;
- the user-owned rootless Docker daemon, selected Docker executable, installed RoleBench `runsc` wrapper/gVisor release, and Docker inspect/copy responses are trusted host components;
- OCI digest immutability and the registry/daemon's digest resolution are trusted;
- verifier-private images must be built by a controlled local/private builder; final-image separation does not make an untrusted remote build context safe;
- `.rolebench/` is a Git hygiene rule, not an access-control boundary; private stores still require restrictive permissions and publication review; and
- the ledger hash chain is unkeyed and owner-rewritable. It proves internal consistency only relative to a separately trusted head and cannot authorize any workflow state.

Report a suspected credential or private-data leak directly to the repository owner. Do not open a public issue containing sensitive details.

## Pull request checklist

A pull request should state:

- [ ] Problem and intended behavior
- [ ] Files and contract versions affected
- [ ] Whether the change affects the fixed default profile, an optional local extension, or advanced allocation/runtime behavior
- [ ] Compatibility or migration impact
- [ ] Security/privacy impact
- [ ] Exact validation commands and observed results
- [ ] Benchmark task, dataset, route, OMP, harness, runner, and verifier versions when applicable
- [ ] Whether outputs are measured evidence, synthetic fixtures, or inference
- [ ] For session-derived work, how sessions were explicitly selected, kept private, de-identified, and separated from published artifacts
- [ ] Whether README current-status claims still match implemented behavior
- [ ] Remaining limitations or unverified paths

Reviewers should be able to reproduce a contract, recommendation, or policy result from pinned inputs without access to another contributor's credentials or local session history.
