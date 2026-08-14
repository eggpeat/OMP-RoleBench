# OMP RoleBench

OMP RoleBench is the contract and tooling project for the evidence and policy-generation side of benchmark-informed model routing for [Oh My Pi](https://github.com/can1357/oh-my-pi). It is designed to evaluate exact model routes against OMP role contracts, estimate capability and operational uncertainty, and generate immutable allocation policies that OMP can execute deterministically.

> **Status:** contract foundation, the first provider-disabled runtime worker, and the diagnostic-task authoring/admission workflow. Role contracts, artifact validation, fair attempt accounting, the no-model fault gate, rootless Docker/`runsc` execution, private candidate ingestion, reviewed qualification, authoring pack manifests, content-bound run preparation, and a local non-authoritative experiment journal are implemented. The pilot packs intentionally contain no admitted tasks. Provider-proxy networking, real calibrated task packs, capability estimates, capacity-aware optimization, policy generation, and OMP runtime integration remain unimplemented.

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

[`contracts/scored-worker-policy.json`](contracts/scored-worker-policy.json) defines the required Docker/`runsc` isolation, provider-proxy boundary, resource limits, immutable handoff, networkless verifier, and fail-closed accounting behavior. `rolebench worker fault-check` validates that policy and sends 29 deterministic synthetic conditions through artifact validation and attempt accounting without launching Docker, using the network, or calling a model. The additional control proves that `admission-only` and `calibration-only` observations are classified as excluded evidence and never enter model-quality scoring.

`rolebench worker doctor` now checks an installed rootless Docker daemon, the registered RoleBench `runsc` wrapper, cgroup v2 delegation, and effective CPU/memory/PID enforcement. `rolebench worker run` executes exact digest-pinned, provider-disabled manifests: separate non-root agent and verifier images, read-only roots, bounded tmpfs scratch, no network, no capabilities or host resources, one-pass immutable artifact streaming, and observed failure accounting. This slice deliberately cannot run a scored provider request; provider-proxy integration and representative compatibility benchmarks remain gates before live scored benchmarks.

### Diagnostic-task workflow status

RoleBench treats task authoring as a gated workflow, not as benchmark evidence. The canonical [`task-v1`](contracts/task-packs/task-v1.json), [`smol-v1`](contracts/task-packs/smol-v1.json), and [`slow-v1`](contracts/task-packs/slow-v1.json) packs are empty `authoring` queues and mechanically routing-ineligible. They do not claim model quality or calibrated coverage.

Candidate creation is private and explicit. `rolebench tasks scan-session` scans one caller-supplied OMP JSONL session and emits only a fresh opaque candidate reference plus signal kinds and row ordinals; it never emits raw prompts, paths, entry IDs, model/provider/account data, or a stable session fingerprint. `rolebench tasks import-omp-gym` independently imports the ergonomic `task.toml` plus `workspace/` shape into an ignored private candidate directory. It does not depend on or copy `omp-gym` code, infer a license, or admit the result.

Admission requires a versioned diagnostic task and independent privacy, license, verifier, and split reviews. `rolebench tasks prepare-admission-run` prepares content-bound baseline, reference, and tamper manifests; each probe must produce at least two healthy, deterministic worker reports. `rolebench tasks qualify` reparses those reports, recomputes accounting, verifies runtime/isolation/task/image mappings, and emits a `calibration-required` qualification. V1 cannot claim `admitted` or freeze a routing-eligible pack because it has no reviewed cross-model discrimination-evidence contract. `rolebench tasks prepare-run` accepts valid non-holdout qualifications only for explicitly `calibration-only` runs. Both preparation paths re-inspect exact single-platform image manifests, config IDs, platforms, and fixed role/content labels; descriptor-safely hash the public and verifier-private in-image asset roots; prove each private/public root is absent from the opposite image; and emit manifests consumed only by the normal worker path.

The committed [`fixtures/diagnostic-task`](fixtures/diagnostic-task) context is a provider-disabled end-to-end smoke fixture: baseline and tamper probes are rejected, the reference and production probes are accepted, and no provider call occurs. Its source kind is `synthetic-fixture`; semantic validation prohibits that source kind from entering any task pack. Build outputs, exact image bindings, qualifications, worker reports, and journals belong under ignored `.rolebench/`, never in Git.

The experiment ledger is an append-only, hash-chained **local journal**, not admission or routing authority. Every admission and routing consumer must independently recapture and validate canonical source artifacts. Public repository validation rejects holdout material; confidential family-level holdouts require author-independent assignment outside the public tree.

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
rolebench worker fault-check contracts/scored-worker-policy.json --json
rolebench worker doctor contracts/scored-worker-policy.json --json
rolebench worker run path/to/worker-run-manifest.json --report .rolebench/run-report.json --json
rolebench tasks pack-verify contracts/task-packs/task-v1.json --json
rolebench tasks qualification-check path/to/task.json path/to/qualification.json --json
rolebench ledger verify .rolebench/experiments.jsonl --json
```

Artifact schema names are the filenames in [`contracts/schemas`](contracts/schemas) without `.schema.json`. Attempt accounting uses `attempt-observation` for facts collected from a run and `attempt-outcome` for the decision about whether that run counts. `scored-worker-policy` defines the fail-closed execution boundary used by the no-model conformance gate. Task workflow schemas are `task-candidate`, `diagnostic-task`, `task-qualification`, `task-pack`, and `experiment-ledger-entry`. Other routing schemas include `route`, `evidence-row`, `capability-snapshot`, `capacity-snapshot`, `demand-snapshot`, `route-policy`, and `routing-decision`.

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
rolebench worker doctor contracts/scored-worker-policy.json --json
```

`ready: true` requires every policy, local user-owned Unix-socket daemon, runtime, delegation, and resource-enforcement check to pass. The worker always supplies `--host unix:///run/user/$(id -u)/docker.sock`; Docker contexts and conflicting `DOCKER_HOST` values cannot redirect execution. Do not run a benchmark after a failed doctor. The wrapper creates one transient delegated cgroup scope per container, copies the OCI CPU/memory/PID limits into cgroup v2 controls, attaches `runsc`, and removes the scope after `runsc delete`.

Build the example agent and verifier from [`fixtures/docker-runsc`](fixtures/docker-runsc), publish them to a registry available to the rootless daemon, and copy the returned immutable repository digests into a private copy of `worker-run-manifest.json`. The committed manifest intentionally contains distinct sentinel digests; tags and sentinels are rejected before Docker is called. The worker streams the bounded agent artifact into a sealed private memory file, then streams the immutable bytes once to verifier stdin. Docker logging is disabled, and worker stdout contains no container stderr.

The example manifest is provider-disabled and must report `external_provider_calls: 0`. Local manifests, raw artifacts, wrapper logs, and benchmark outputs are runtime data and must not be committed.

## Repository layout

```text
contracts/
  role-registry.json     Canonical built-in role registry and OMP provenance
  scored-worker-policy.json
                          Canonical policy for future scored-worker enforcement
  roles/                 Versioned diagnostic contracts for all ten roles
  schemas/               Draft 2020-12 artifact schemas
  task-packs/             Empty routing-ineligible pilot authoring queues
docs/
  OMP_BENCHMARK_INFORMED_ROLE_ROUTING_SPEC.md
                        Architecture and benchmark methodology
fixtures/docker-runsc/
  Dockerfile.agent      Provider-disabled isolation probe image
  Dockerfile.verifier   Distinct networkless verifier image
  worker-run-manifest.json
                        Digest-placeholder example worker manifest
  agent.sh              In-sandbox isolation probe and artifact producer
  verifier.sh           One-pass stdin artifact verifier
fixtures/diagnostic-task/
  Dockerfile.agent      Synthetic baseline/reference/tamper/production image
  Dockerfile.verifier   Synthetic private objective verifier image
  public/                Public prompt and workspace content
  verifier-private/      Verifier-only expected result and executable
  probe.sh               Deterministic provider-disabled agent entrypoint
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

The canonical contract digest covers the registry, all ten role manifests in registry order, the registry-mapped pilot task packs, and the scored-worker policy. It is stable across JSON whitespace and object-key ordering. Empty authoring packs are workflow queues, not benchmark evidence.

## Roadmap

1. Freeze all ten v1 role contracts and cross-repository artifact contracts.
2. Curate, qualify, and calibrate real OMP-native objective diagnostics for every role, then pin applicable Terminal-Bench anchors. Keep the committed pilot packs routing-ineligible until those gates pass.
3. Extend the provider-disabled Docker/`runsc` worker with the credentialless provider-proxy boundary, then prove representative model-task compatibility and observed-fault parity with the no-model gate.
4. Estimate calibrated `role x route` capability, reliability, cost, latency, and quota consumption.
5. Add capacity and demand snapshots plus the constrained allocation optimizer.
6. Validate allocation regret on author-independent family-level held-out tasks and posterior draws.
7. Generate and explain immutable `omp.route-policy/v1` artifacts.
8. Integrate policy shadowing and enforcement through focused upstream OMP changes.

See the [full design specification](docs/OMP_BENCHMARK_INFORMED_ROLE_ROUTING_SPEC.md) for data contracts, statistical methodology, optimization constraints, runtime behavior, security requirements, and milestones.

## Contributing

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before changing role contracts, schemas, benchmark tasks, or policy behavior. Contract changes require focused tests and must preserve privacy, provenance, and reproducibility.

## Security and privacy

Never commit API keys, OAuth tokens, credential IDs, raw account identifiers, private prompts, or unredacted trajectories. Run untrusted benchmark tasks in isolated environments and keep provider credentials host-side. Please report a suspected leak privately to the repository owner rather than opening a public issue.
