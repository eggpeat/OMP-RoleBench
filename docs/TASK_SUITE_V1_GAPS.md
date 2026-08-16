# OMP RoleBench Task Suite v1 Gap Report & Implementation Backlog

This document provides the definitive gap analysis and prioritized implementation roadmap for the remaining task lanes in OMP RoleBench v1. It bridges structural finalization with the lane-by-lane authoring phase required before the benchmark corpus can be frozen for cross-model calibration.

---

## 1. Executive Summary & Structural State

As audited by `rolebench tasks suite-audit`:

- **Canonical Roles:** 11/11 roles are structurally ready ($\ge 100\%$ anchor coverage and 100% required capability coverage across 16 qualified anchors).
- **Task Routing Lanes:** 0/6 specialized `@task` routing lanes meet the $\ge 2$ anchor freeze threshold.
- **Total Qualified Calibration Anchors:** 16
- **Routing Status:** All packs are `calibration-only` and `routing-ineligible`.

---

## 2. Lane-by-Lane Structural Gap Analysis & Backlog

### Gap 1: `task/implementation` (Priority: P0)
- **Role / Lane:** `task` / `task/implementation`
- **Missing Capabilities:** `code-generation`, `test-authoring`, `repository-navigation`, `targeted-verification`
- **Why Current Tasks Are Insufficient:** The current `@task` pack only contains `cancel-async-tasks` (a debugging/concurrency fix task). It does not evaluate end-to-end feature authoring from specification to greenfield module and accompanying test suite.
- **Recommended Task Family:**
  - Anchor 1A (OMP-Native): `omp-native.event-stream-multiplexer` (Asyncio event bus multiplexer with channel filtering and backpressure).
  - Anchor 1B (Terminal-Bench): `terminal-bench.feature-matrix-compiler` or `quant-bench.incremental-feature-materialization`.
- **Better Source:** OMP-Native for exact harness & tool fidelity; Terminal-Bench for broad repository baseline.
- **Proposed Verifier Strategy:** Rootless container running pre-baked test suite against candidate module; verifies interface contracts, edge cases, and unit test pass rate.
- **Target Difficulty:** Medium
- **Split Disposition:** Calibration (Public Anchor) + Family-Disjoint Holdout (Confidential).
- **Dependencies:** None.

### Gap 2: `task/debugging` (Priority: P0)
- **Role / Lane:** `task` / `task/debugging`
- **Missing Capabilities:** `defect-reproduction`, `root-cause-analysis`, `targeted-patching`, `regression-testing`
- **Why Current Tasks Are Insufficient:** `cancel-async-tasks` covers asyncio cancellation, but lane requires at least two distinct defect families (e.g. state corruptions, distributed race conditions, or memory/resource leak diagnosis).
- **Recommended Task Family:**
  - Anchor 2A (Terminal-Bench): `terminal-bench.cancel-async-tasks` (already qualified, bind to lane).
  - Anchor 2B (OMP-Native): `omp-native.distributed-lease-deadlock` (Diagnose and resolve split-brain lease expiration in distributed state manager).
- **Better Source:** OMP-Native for multi-threaded/async state debugging.
- **Proposed Verifier Strategy:** Execution verifier driving concurrent torture harness and asserting deterministic state invariants and regression-free pass.
- **Target Difficulty:** Hard
- **Split Disposition:** Calibration (Public Anchor) + Family-Disjoint Holdout (Confidential).
- **Dependencies:** Task-lane schema binding.

### Gap 3: `task/test-repair` (Priority: P1)
- **Role / Lane:** `task` / `task/test-repair`
- **Missing Capabilities:** `test-diagnosis`, `harness-repair`, `mock-adaptation`, `contract-preservation`
- **Why Current Tasks Are Insufficient:** Models frequently "fix" broken tests by deleting assertions or disabling checks. RoleBench requires tasks that measure repairing outdated test suites while preserving product invariants.
- **Recommended Task Family:**
  - Anchor 3A (OMP-Native): `omp-native.api-contract-test-modernization` (Update deprecated mock expectations and mock responses after upstream API payload change without deleting valid checks).
  - Anchor 3B (Terminal-Bench): `terminal-bench.pytest-fixture-migration` (Migrate legacy unittest setup to modern pytest fixtures with parameterization).
- **Better Source:** OMP-Native.
- **Proposed Verifier Strategy:** Verifier runs repaired test suite against both golden implementation and deliberately broken mutation implementations to ensure tests actually catch bugs.
- **Target Difficulty:** Medium
- **Split Disposition:** Calibration (Public Anchor) + Family-Disjoint Holdout.
- **Dependencies:** None.

### Gap 4: `task/refactor` (Priority: P1)
- **Role / Lane:** `task` / `task/refactor`
- **Missing Capabilities:** `ast-refactoring`, `callsite-migration`, `type-safety-preservation`, `behavior-equivalence`
- **Why Current Tasks Are Insufficient:** Refactoring requires changing internal architecture and migrating callsites across multiple files without breaking public API behavior.
- **Recommended Task Family:**
  - Anchor 4A (OMP-Native): `omp-native.sync-to-async-client-refactor` (Refactor synchronous HTTP client adapter into async/await architecture across 5 client modules).
  - Anchor 4B (Terminal-Bench): `terminal-bench.modernize-scientific-stack` (Migrate legacy NumPy matrix APIs to modern array syntax).
- **Better Source:** OMP-Native.
- **Proposed Verifier Strategy:** Full existing integration test suite execution + AST verification that legacy patterns were cleanly removed and callsites migrated.
- **Target Difficulty:** Hard
- **Split Disposition:** Calibration (Public Anchor) + Family-Disjoint Holdout.
- **Dependencies:** None.

### Gap 5: `task/repo-research` (Priority: P2)
- **Role / Lane:** `task` / `task/repo-research`
- **Missing Capabilities:** `codebase-scouting`, `cross-file-tracing`, `grounded-summarization`, `read-only-safety`
- **Why Current Tasks Are Insufficient:** Research tasks are read-only exploratory tasks where a subagent maps code dependencies, traces call graphs, or identifies affected components without modifying code.
- **Recommended Task Family:**
  - Anchor 5A (OMP-Native): `omp-native.architecture-dependency-audit` (Traverse a multi-package workspace and generate an exact JSON dependency and callsite graph for a targeted subsystem).
  - Anchor 5B (OMP-Native): `omp-native.vulnerability-impact-trace` (Trace all untrusted input vectors reaching a designated internal sink across 10 files).
- **Better Source:** OMP-Native.
- **Proposed Verifier Strategy:** Passive JSON schema and graph equality verifier comparing reported nodes, edges, and file citations against ground-truth static analysis.
- **Target Difficulty:** Medium
- **Split Disposition:** Calibration (Public Anchor) + Family-Disjoint Holdout.
- **Dependencies:** None.

### Gap 6: `task/mechanical-edit` (Priority: P2)
- **Role / Lane:** `task` / `task/mechanical-edit`
- **Missing Capabilities:** `batch-editing`, `pattern-application`, `strict-formatting`, `exhaustiveness`
- **Why Current Tasks Are Insufficient:** High-volume repetitive changes across 20+ files often suffer from dropped files, partial application, or hallucinated formatting changes.
- **Recommended Task Family:**
  - Anchor 6A (OMP-Native): `omp-native.schema-enum-sync` (Batch update 25 data model classes and schema definitions to add a new enum variant and serializing logic).
  - Anchor 6B (OMP-Native): `omp-native.deprecation-annotation-pass` (Apply strict `@deprecated` annotations and migration docstrings across 30 library functions).
- **Better Source:** OMP-Native.
- **Proposed Verifier Strategy:** Exact structural AST diff + compiler/linter check on all 25+ target files.
- **Target Difficulty:** Easy-to-Medium (high volume).
- **Split Disposition:** Calibration (Public Anchor) + Family-Disjoint Holdout.
- **Dependencies:** None.

---

## 3. Implementation Order & Milestone Plan

```text
Milestone 1: Freeze Criteria, Task-Lane Binding, & Reviewer Foundation (Current PR)
  ├── contracts/schemas/task-suite-profile-v1.schema.json
  ├── contracts/task-suite-profile-v1.json
  ├── docs/TASK_SUITE_V1_FINALIZATION.md & docs/TASK_SUITE_V1_GAPS.md
  ├── Task lane schema bindings in diagnostic-task & task-pack schemas
  ├── Reviewer role contract (reviewer.json) & role registry v4
  ├── Reviewer task pack (reviewer-v1.json) & 2 OMP-native anchors
  └── rolebench tasks suite-audit CLI

Milestone 2: Task-Lane Population (Follow-up Batch PRs)
  ├── PR A: task/implementation & task/debugging anchors
  ├── PR B: task/test-repair & task/refactor anchors
  └── PR C: task/repo-research & task/mechanical-edit anchors

Milestone 3: Corpus Structural Freeze & Multi-Model Calibration Gate
  ├── Promote task-suite-profile-v1.json status to "structurally-ready"
  ├── suite-audit --strict passes with exit code 0
  └── Execute multi-model calibration runs across candidate routes
```
