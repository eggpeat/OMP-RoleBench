# OMP RoleBench

OMP RoleBench is an open-source toolkit for [Oh My Pi](https://github.com/can1357/oh-my-pi) users to make objective, reproducible model-to-role assignments. It evaluates exact model routes—provider, model, thinking effort, transport/upstream, capacity pool, and OMP version—against versioned task capabilities mapped to OMP's canonical roles.

By default, RoleBench is designed around a fixed benchmark profile built from Terminal-Bench-style executable tasks: pinned Terminal-Bench anchors where they fit a role, plus OMP-native objective diagnostics for capabilities the public benchmark does not cover. Users may optionally supplement that profile with private diagnostic tasks derived from explicitly selected OMP session logs. Session history is never required and is not benchmark evidence by itself.

> **Current status:** pre-release contract and execution foundation. Role contracts, artifact validation, fair attempt accounting, the no-model fault gate, a provider-disabled rootless Docker/`runsc` worker, private candidate ingestion, reviewed task qualification, a 14-anchor calibration profile covering all ten role packs, content-bound run preparation, and a local non-authoritative experiment journal are implemented. The fixed profile contains 11 pinned public Terminal-Bench anchors and three OMP-native objective diagnostics. Every pack remains routing-ineligible pending cross-model discrimination evidence and calibration. RoleBench cannot yet run provider-backed benchmark suites or recommend model assignments. Provider-backed execution, calibrated capability estimates, session-to-task synthesis, capacity-aware optimization, policy generation, and OMP runtime integration remain unimplemented.

## What RoleBench decides

OMP roles such as `@task`, `@plan`, and `@vision` describe different workloads. A general leaderboard cannot determine whether a particular route is qualified for each role, because aggregate scores erase role-specific capabilities, harness behavior, uncertainty, and operational failures.

The core user outcome is a reproducible qualification and ranking of candidate routes for every active OMP role. Capacity-aware traffic allocation and immutable runtime policies are later layers built only from routes that first clear the role's capability and reliability requirements.

```text
OMP canonical roles + fixed default benchmark profile
    -> candidate exact model routes
    -> objective, sandboxed trials
    -> role x route capability and reliability estimates
    -> ranked model-route recommendations per role
    -> optional capacity-aware allocation policy
    -> deterministic OMP role routing
```

RoleBench owns the offline benchmark, evidence, recommendation, and policy-generation layers. The generic policy loader and runtime router belong upstream in OMP. OMP must remain able to serve normal requests without RoleBench, its datasets, Harbor, or statistical tooling.

## Default benchmark profile

Every installation should start from the same versioned mapping of canonical roles to required capabilities and fixed task packs:

1. **Terminal-Bench anchors** provide pinned public, objectively verified tasks where a benchmark workload directly exercises a role contract. The fixed profile uses them for `default`, `task`, `slow`, `plan`, `advisor`, `smol`, and `vision`.
2. **OMP-native anchors** use the same self-contained, executable, objectively verified style for capabilities the public benchmark does not robustly cover. The fixed profile uses them for `tiny`, `commit`, and `designer`.
3. **Held-out routing tests** validate whether the resulting recommendations generalize without participating in task selection or estimator fitting.

“Terminal-Bench-style” describes the task contract, not an unofficial Terminal-Bench score: a pinned environment, explicit success criteria, executable verification where practical, exact task and harness provenance, and separate accounting for model failures versus provider or infrastructure failures. Each task binds to one canonical role contract and explicit capability tags. Missing coverage makes a role unavailable for recommendation; it does not make session access mandatory.

The committed fixed profile contains 14 independently reviewed, provider-disabled anchors: 11 pinned public Terminal-Bench tasks and three OMP-native objective diagnostics. Every qualification remains `calibration-required`; all ten packs are routing-ineligible and provide no model-quality evidence.

| Role pack | Fixed anchors |
| --- | --- |
| [`default-v1`](contracts/task-packs/default-v1.json) | [`sanitize-git-repo`](contracts/tasks/terminal-bench.sanitize-git-repo/2.1-r6), [`multi-source-data-merger`](contracts/tasks/terminal-bench.multi-source-data-merger/2.1-r6) |
| [`task-v1`](contracts/task-packs/task-v1.json) | [`cancel-async-tasks`](contracts/tasks/terminal-bench.cancel-async-tasks/2.1-r6) |
| [`slow-v1`](contracts/task-packs/slow-v1.json) | [`custom-memory-heap-crash`](contracts/tasks/terminal-bench.custom-memory-heap-crash/2.1-r6), [`db-wal-recovery`](contracts/tasks/terminal-bench.db-wal-recovery/2.1-r6) |
| [`plan-v1`](contracts/task-packs/plan-v1.json) | [`llm-inference-batching-scheduler`](contracts/tasks/terminal-bench.llm-inference-batching-scheduler/2.1-r6) |
| [`advisor-v1`](contracts/task-packs/advisor-v1.json) | [`fix-code-vulnerability`](contracts/tasks/terminal-bench.fix-code-vulnerability/2.1-r6), [`memcached-backdoor`](contracts/tasks/terminal-bench.memcached-backdoor/3.0-r1) |
| [`smol-v1`](contracts/task-packs/smol-v1.json) | [`large-scale-text-editing`](contracts/tasks/terminal-bench.large-scale-text-editing/2.1-r6) |
| [`vision-v1`](contracts/task-packs/vision-v1.json) | [`code-from-image`](contracts/tasks/terminal-bench.code-from-image/2.1-r6), [`cad-model`](contracts/tasks/terminal-bench.cad-model/3.0-r1) |
| [`tiny-v1`](contracts/task-packs/tiny-v1.json) | [`metadata-normalization`](contracts/tasks/omp-native.metadata-normalization/1.0.0) |
| [`commit-v1`](contracts/task-packs/commit-v1.json) | [`diff-commit-message`](contracts/tasks/omp-native.diff-commit-message/1.0.0) |
| [`designer-v1`](contracts/task-packs/designer-v1.json) | [`responsive-incident-console`](contracts/tasks/omp-native.responsive-incident-console/1.0.0) |

## Optional session-derived diagnostics

Users may explicitly select local OMP sessions to generate supplemental task candidates for recurring work that the fixed profile does not represent. Those tasks extend the default profile; they do not replace it, change the canonical role taxonomy, or become a prerequisite for model assignment.

The implemented `rolebench tasks scan-session` command returns a versioned privacy-minimized candidate with a fresh opaque reference, an `entry_count` equal to the number of parsed v3 message entries, and signals whose `ordinal` indexes those message entries and whose `kind` identifies the signal. An optional caller-supplied role hint may also be present. The command does not emit raw prompts, responses, paths, entry IDs, model/provider/account data, or a stable session fingerprint, and it does not synthesize an executable task. Automatic local session-to-task generation remains planned.

Raw session content and generated drafts stay inside the operator-controlled private authoring boundary. A derived task must remove user-specific material and pass the same independent privacy, redistribution, verifier, split, qualification, and calibration gates as any other task before it can contribute evidence. Regeneration is offline and produces new immutable task and policy versions; it never changes routing during a request.

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

[`contracts/scored-worker-policy-v2.json`](contracts/scored-worker-policy-v2.json) defines the required Docker/`runsc` isolation, provider-proxy boundary, resource limits, immutable handoff, networkless verifier, and fail-closed accounting behavior. `rolebench worker fault-check` validates that policy and sends 29 deterministic synthetic conditions through artifact validation and attempt accounting without launching Docker, using the network, or calling a model. The additional control proves that `admission-only` and `calibration-only` observations are classified as excluded evidence and never enter model-quality scoring.

`rolebench worker doctor` now checks an installed rootless Docker daemon, the registered RoleBench `runsc` wrapper, cgroup v2 delegation, and effective CPU/memory/PID enforcement. `rolebench worker run` executes exact digest-pinned, provider-disabled manifests across a three-container isolation pipeline: separate non-root agent, candidate runner, and passive verifier images, read-only roots, bounded tmpfs scratch, no network, no capabilities or host resources. The candidate artifact is supplied only to and may execute only in the candidate runner; it never executes in or becomes instructions for the verifier container. Because runner output may reflect artifact bytes, the verifier receives those bytes only as bounded inert untrusted data inside a host-framed evidence envelope. The host seals that evidence with a per-attempt nonce, `run_id`, stream lengths and digests, and bound request digests. Verifier verdicts echo exact bindings and emit strict `omp.verifier-result/v1`. Candidate execution semantics remain untrusted unless externally observable; runner stdout/events/clocks are untrusted payloads and internally self-reported semantics remain inadmissible without source-separated observation. The committed `cancel-async-tasks` anchor uses approved source-separated observation and binds its evidence through independent passive-verifier review; its qualification remains `calibration-required` and excluded from routing evidence. This slice deliberately cannot run a scored provider request; provider-proxy integration and representative compatibility benchmarks remain gates before live scored benchmarks.

### Diagnostic-task workflow status

RoleBench treats task authoring as a gated workflow, not as benchmark evidence. The seven anchors listed above populate the `default`, `task`, `slow`, `plan`, and `advisor` packs; all remain mechanically routing-ineligible and claim neither model quality nor calibrated route coverage. The `smol` pack remains an empty `authoring` queue.

Current candidate discovery is private and explicit. `rolebench tasks scan-session` scans one caller-supplied OMP JSONL session and returns a versioned candidate with a fresh opaque reference, parsed-message `entry_count`, and signal objects containing a message-entry `ordinal` and deterministic `kind`; it never emits raw prompts, paths, entry IDs, model/provider/account data, or a stable session fingerprint. This is the safe discovery boundary for the planned local session-to-task generator, not automatic task synthesis or admission. `rolebench tasks import-omp-gym` independently imports the ergonomic `task.toml` plus `workspace/` shape into an ignored private candidate directory. It does not depend on or copy `omp-gym` code, infer a license, or admit the result.

Admission requires a versioned diagnostic task and independent privacy, license, verifier, and split reviews. `rolebench tasks prepare-admission-run` prepares content-bound baseline, reference, and tamper manifests with distinct agent, runner, and verifier containers; each probe must produce at least two healthy, deterministic worker reports. `rolebench tasks qualify` reparses those reports, recomputes accounting, verifies runtime/isolation/task/image mappings, and emits a `calibration-required` qualification with `evaluation_provenance` and `runner_isolation`. V1 cannot claim `admitted` or freeze a routing-eligible pack because it has no reviewed cross-model discrimination-evidence contract. `rolebench tasks prepare-run` accepts valid non-holdout qualifications only for explicitly `calibration-only` runs. Both preparation paths re-inspect exact single-platform agent, runner, and verifier image manifests, config digests, platforms, fixed role/content/stage labels, and container file tree content.

The committed [`fixtures/diagnostic-task`](fixtures/diagnostic-task) context is a provider-disabled end-to-end smoke fixture: baseline and tamper probes are rejected, the reference and production probes are accepted, and no provider call occurs. Its source kind is `synthetic-fixture`; semantic validation prohibits that source kind from entering any task pack. Raw build outputs, image archives, manifests, reports, and journals belong under ignored `.rolebench/`. The only repository exception is the small, normalized, independently reviewed admission evidence and verifier fixtures explicitly referenced by a public pack under `contracts/tasks/`.

The experiment ledger is an append-only, hash-chained **local journal**, not admission or routing authority. Every admission and routing consumer must independently recapture and validate canonical source artifacts. Public repository validation rejects holdout material; confidential family-level holdouts require author-independent assignment outside the public tree.

## Design principles

- **Canonical defaults, optional personalization.** Every installation starts from a fixed, versioned role benchmark profile; opt-in session-derived diagnostics may supplement it.
- **Roles, not agents.** Benchmark, recommendation, and allocation decisions use canonical OMP model roles as their control-plane key.
- **Exact routes, not model names.** Provider, model, thinking effort, transport/upstream, aggregate capacity pool, and OMP version are part of route identity.
- **Recommendations before allocation.** Role-specific quality and reliability decide which routes qualify before cost, latency, quota, or traffic weights are considered.
- **Evidence retains uncertainty.** Estimates include coverage, freshness, and confidence rather than collapsing every tradeoff into one score.
- **Policies are immutable snapshots.** Runtime health can temporarily exclude a route; material evidence, demand, or capacity changes produce a new policy version.
- **Credentials remain provider-managed.** Policies may identify an opaque aggregate capacity pool, but never an account or credential.
- **Explicit user choices win.** Automatic routing must not override a concrete model selection.

## Built-in roles

V1 uses all ten roles from OMP's canonical role registry. The final column describes the planned fixed evidence mix; no routing-eligible default packs are shipped yet.

| Role | Capability focus | Planned default evidence |
| --- | --- | --- |
| `default` | Broad interactive coding, terminal work, and tool use | Terminal-Bench anchors plus representative OMP-native repository tasks |
| `smol` | Correct bounded work under strict latency and consumption budgets | Small deterministic OMP-native tasks with explicit budgets |
| `slow` | Difficult diagnosis, reasoning, and recovery | Hard Terminal-Bench anchors plus OMP-native recovery tasks |
| `vision` | Image-grounded multimodal work | Objective image-input and visual-grounding tasks |
| `plan` | Executable architecture, decomposition, and sequencing | Executability-checked plans plus selected terminal-task evidence |
| `designer` | Functional UI implementation and visual quality | Browser/DOM verification plus separately identified visual evaluation |
| `commit` | Semantic commit-message coverage without invention | Diff-grounded exactness and repository-convention tasks |
| `tiny` | Exact metadata, extraction, and classification | Deterministic structured-output tasks |
| `task` | Autonomous delegated implementation | Terminal-Bench anchors plus autonomous OMP-native repository tasks |
| `advisor` | Defect and risk recall with controlled false positives | Seeded review tasks plus selected terminal-task evidence |

The source-of-truth manifests are in [`contracts/roles`](contracts/roles), and their pinned OMP provenance is recorded in [`contracts/role-registry.json`](contracts/role-registry.json). Thresholds remain `calibration-required` until benchmark and held-out evidence supports freezing them.

## Developer quick start

RoleBench currently requires Python 3.12 or newer. There is not yet an end-user `evaluate` or `recommend` command; this setup exercises the implemented contract, accounting, worker, and task-authoring foundation.

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
rolebench [--root PATH] worker fault-check POLICY [--json]
rolebench [--root PATH] worker doctor POLICY [--docker PATH] [--json]
rolebench [--root PATH] worker run MANIFEST [--docker PATH] [--report PATH] [--json]
rolebench [--root PATH] tasks scan-session SESSION [--role-hint ROLE]
rolebench [--root PATH] tasks import-omp-gym SOURCE DESTINATION --source-version VERSION --license EXPRESSION [--role-hint ROLE] [--capability TAG ...]
rolebench [--root PATH] tasks qualification-check TASK QUALIFICATION [--json]
rolebench [--root PATH] tasks qualify TASK --baseline-report REPORT --baseline-report REPORT --reference-report REPORT --reference-report REPORT --tamper-report REPORT --tamper-report REPORT --reviewer REVIEWER --output PATH [--json]
rolebench [--root PATH] tasks pack-verify PACK [--json]
rolebench [--root PATH] tasks prepare-admission-run TASK --probe baseline|reference|tamper --run-id ID --output PATH [--docker PATH]
rolebench [--root PATH] tasks prepare-run TASK QUALIFICATION --run-id ID --output PATH [--docker PATH]
rolebench [--root PATH] ledger verify LEDGER [--json]
rolebench [--root PATH] ledger append LEDGER SCHEMA ARTIFACT [--json]
rolebench [--root PATH] ledger append-worker-report LEDGER REPORT [--json]
```

Examples:

```bash
rolebench contracts show advisor
rolebench contracts digest --json
rolebench artifacts validate route-policy path/to/policy.json --json
rolebench accounting classify path/to/observation.json
rolebench accounting summarize path/to/outcome-*.json
rolebench worker fault-check contracts/scored-worker-policy-v2.json --json
rolebench worker doctor contracts/scored-worker-policy-v2.json --json
rolebench worker run path/to/worker-run-manifest.json --report .rolebench/run-report.json --json
rolebench tasks pack-verify contracts/task-packs/task-v1.json --json
rolebench tasks qualification-check path/to/task.json path/to/qualification.json --json
rolebench ledger verify .rolebench/experiments.jsonl --json
```

Artifact schema names are the filenames in [`contracts/schemas`](contracts/schemas) without `.schema.json`. Attempt accounting uses `attempt-observation` for facts collected from a run and `attempt-outcome` for the decision about whether that run counts. `scored-worker-policy` defines the fail-closed execution boundary used by the no-model conformance gate. Task workflow schemas are `task-candidate`, `diagnostic-task`, `task-qualification`, `task-pack`, and `experiment-ledger-entry`. Other routing schemas include `route`, `evidence-row`, `capability-snapshot`, `capacity-snapshot`, `demand-snapshot`, `route-policy`, and `routing-decision`.

The target architecture also defines a future `omp.role-recommendation/v1` artifact that ranks qualified routes independently of capacity and policy generation. No corresponding schema or producer is implemented yet.

## Rootless Docker/`runsc` worker setup

The runtime worker is optional. Contract validation and the synthetic fault gate do not require Docker. A worker host requires:

- Linux with cgroup v2 and a running `systemd --user` manager that delegates the `cpu`, `memory`, and `pids` controllers;
- a recent rootless Docker daemon using the `systemd` cgroup driver;
- RootlessKit, `newuidmap`/`newgidmap`, and subordinate UID/GID ranges for the worker user;
- an official gVisor `runsc` release, including its sibling binaries, installed outside the repository; and
- Python 3.12 or newer for the RoleBench wrapper and CLI.

The first tested host used Docker 29.1.3, RootlessKit 2.3.5, and gVisor `runsc` release `20260810.0`. Newer versions are not assumed compatible until the doctor and smoke path pass.

Install the complete gVisor release directory and the repository wrapper beside `runsc`. The wrapper requires that exact adjacent executable and does not consult `PATH` or environment overrides.

```bash
install -d "$HOME/.local/lib/gvisor"
# Extract the official gVisor release archive into $HOME/.local/lib/gvisor.
install -m 0755 scripts/rolebench-runsc-wrapper \
  "$HOME/.local/lib/gvisor/rolebench-runsc-wrapper"
```

Register the wrapper, not the raw `runsc` binary, in the rootless daemon's `$HOME/.config/docker/daemon.json`. JSON paths must be absolute; merge this key with existing daemon settings rather than overwriting them.

```json
{
  "runtimes": {
    "runsc": {
      "path": "/home/WORKER/.local/lib/gvisor/rolebench-runsc-wrapper"
    }
  }
}
```

Restart the user daemon and run the fail-closed doctor:

```bash
systemctl --user restart docker.service
rolebench worker doctor contracts/scored-worker-policy-v2.json --json
```

`ready: true` requires every policy, local user-owned Unix-socket daemon, runtime, delegation, and resource-enforcement check to pass. The worker always supplies `--host unix:///run/user/$(id -u)/docker.sock`; Docker contexts and conflicting `DOCKER_HOST` values cannot redirect execution. Do not run a benchmark after a failed doctor. The wrapper creates one transient delegated cgroup scope per container, copies the OCI CPU/memory/PID limits into cgroup v2 controls, attaches `runsc`, and removes the scope after `runsc delete`.

Build the example agent, runner, and verifier from [`fixtures/docker-runsc`](fixtures/docker-runsc), publish them to a registry available to the rootless daemon, and copy the returned immutable repository digests into a private copy of `worker-run-manifest.json`. The committed manifest intentionally contains distinct sentinel digests; tags and sentinels are rejected before Docker is called. The worker streams the bounded agent artifact into a sealed private memory file, streams the immutable artifact to runner stdin, collects sealed runner evidence after runner exit, and streams the immutable runner evidence once to verifier stdin. Docker logging is disabled, and worker stdout contains no container stderr.

The example manifest is provider-disabled and must report `external_provider_calls: 0`. Local manifests, raw artifacts, wrapper logs, and benchmark outputs are runtime data and must not be committed.

## Repository layout

```text
contracts/
  role-registry.json     Canonical built-in role registry and OMP provenance
  scored-worker-policy-v2.json
                          Canonical policy for future scored-worker enforcement
  scored-worker-policy.json
                          Legacy v1 policy preserved for replaying v1 manifests
  roles/                 Versioned diagnostic contracts for all ten roles
  schemas/               Draft 2020-12 artifact schemas
  task-packs/            Reviewed fixed calibration packs for all ten roles
  tasks/                 Versioned public assets, reviews, admission evidence, and verifiers
docs/
  OMP_BENCHMARK_INFORMED_ROLE_ROUTING_SPEC.md
                        Architecture and benchmark methodology
fixtures/docker-runsc/
  Dockerfile.agent      Provider-disabled isolation probe image
  Dockerfile.runner     Distinct runner container executing candidate artifact
  Dockerfile.verifier   Distinct passive networkless verifier image
  worker-run-manifest.json
                        Digest-placeholder example worker manifest
  agent.sh              In-sandbox isolation probe and artifact producer
  runner.sh             In-sandbox candidate runner and observable payload producer
  verifier.py           Passive Python evidence envelope and verdict verifier
fixtures/diagnostic-task/
  Dockerfile.agent      Synthetic baseline/reference/tamper/production image
  Dockerfile.runner     Synthetic candidate runner image
  Dockerfile.verifier   Synthetic private objective passive verifier image
  public/                Public prompt and workspace content
  verifier-private/      Verifier-only expected result and executable (verifier.py)
  probe.sh               Deterministic provider-disabled agent entrypoint
  runner.sh              Deterministic candidate runner entrypoint
scripts/
  rolebench-runsc-wrapper
                        Delegated cgroup v2 wrapper around the real runsc
src/rolebench/
  cli.py                 Command-line interface
  accounting_rules.py    Shared reason and scoring invariants
  accounting.py          Fair attempt classification and quality summaries
  fault_harness.py       Deterministic no-model accounting conformance gate
  contracts.py           Loading, canonicalization, validation, and semantics
  worker.py              Fail-closed Docker/runsc orchestration and accounting
  task_workflow.py        Private ingestion, admission, pack, and run preparation
  ledger.py               Local non-authoritative hash-chained journal
tests/
  test_accounting.py     Attempt fault, scoring, and CLI behavior tests
  test_fault_harness.py  Exact accounting reason-code fault matrix
  test_runsc_wrapper.py  OCI limit and delegated-scope wrapper tests
  test_worker_cli.py     Scored-worker conformance CLI behavior
  test_worker_fixtures.py
                        Image, manifest, and handoff contract tests
  test_worker_policy.py  Isolation policy schema and semantic invariants
  test_worker_run_cli.py Worker doctor/run CLI behavior
  test_worker_runtime.py Docker adapter, isolation, and failure-path tests
  test_task_workflow.py   Candidate, import, qualification, pack, and preparation gates
  test_ledger.py          Local journal integrity and normalization
  test_task_workflow_cli.py
                          Diagnostic-task command behavior
  test_ledger_cli.py      Journal command behavior
  test_contracts.py      Contract, artifact, CLI, and failure-path tests
```

Large benchmark outputs do not belong in Git. Keep raw run artifacts in an external run directory or artifact store. Commit only small, intentional, license-compatible normalized fixtures.

## Artifact contracts

Committed schemas use JSON Schema Draft 2020-12 and namespaced versions such as `omp.route-policy/v1`. Semantic validation supplements JSON Schema where cross-field rules are required. For example, each active role in a policy must have exactly 10,000 positive integer basis points, unique route IDs, ordered validity timestamps, and an explicit emergency fallback order independent of weighted allocation.

The canonical contract digest covers the registry, all ten role manifests in registry order, the registry-mapped fixed task packs, and the scored-worker policy. It is stable across JSON whitespace and object-key ordering. Authoring queues are workflow state, not benchmark evidence.

## Roadmap

1. Freeze all ten v1 role contracts and cross-repository artifact contracts.
2. Preserve complete fixed-profile coverage as role contracts or public benchmark inventories change, and add or replace independently reviewed anchors only when they improve role-specific discrimination without redundant evidence.
3. Extend the provider-disabled Docker/`runsc` worker with the credentialless provider-proxy boundary, then prove representative model-task compatibility and observed-fault parity with the no-model gate.
4. Execute candidate exact routes, collect cross-model discrimination evidence, calibrate task packs and role thresholds, and produce normalized quality, reliability, latency, cost, and consumption evidence.
5. Estimate calibrated `role x route` capability and emit reproducible ranked model-route recommendations. This is the core toolkit milestone.
6. Validate recommendation quality and regret on author-independent family-level held-out tasks and posterior draws.
7. Add opt-in local session-to-task generation as a supplemental task source; keep raw sessions and drafts private and apply the normal review and discrimination gates.
8. Add demand and capacity snapshots, constrained allocation, and immutable policy generation for users who want multi-route traffic distribution; then validate allocation regret.
9. Integrate policy shadowing and enforcement through focused upstream OMP changes.

See the [full design specification](docs/OMP_BENCHMARK_INFORMED_ROLE_ROUTING_SPEC.md) for data contracts, statistical methodology, optimization constraints, runtime behavior, security requirements, and milestones.

## Contributing

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before changing role contracts, schemas, benchmark tasks, recommendation semantics, or policy behavior. Contract changes require focused tests and must preserve privacy, provenance, and reproducibility.

## License

This repository is licensed under the [MIT License](LICENSE). Third-party benchmark tasks, datasets, and imported assets may carry separate terms; their explicit source, license, and redistribution reviews govern whether they may be included or published.

## Security and privacy

Never commit API keys, OAuth tokens, credential IDs, raw account identifiers, private prompts, or unredacted trajectories. Run untrusted benchmark tasks in isolated environments and keep provider credentials host-side. Please report a suspected leak privately to the repository owner rather than opening a public issue.
