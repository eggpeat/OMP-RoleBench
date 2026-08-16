# OMP RoleBench Task Suite v1 Status & Multi-Model Calibration Roadmap

This document records the completed structural task corpus status for OMP RoleBench v1 and details the upcoming multi-model calibration phase.

---

## 1. Executive Summary & Structural State

As audited by `rolebench tasks suite-audit --strict`:

- **Canonical Roles:** **11/11 roles structurally ready** (100% anchor and capability coverage).
- **Task Routing Lanes:** **6/6 specialized `@task` routing lanes structurally ready** ($\ge 2$ direct anchors per lane).
- **Total Qualified Anchors:** **27**
- **Strict Structural Audit Verdict:** **`PASS` (exit code 0, status `SUITE_STRUCTURALLY_READY`)**.
- **Corpus Profile Status:** **`structurally-ready`**.
- **Routing Status:** All packs remain `calibration-only` and `routing-ineligible` pending multi-model calibration evidence.

---

## 2. Completed Task Lane Population Summary

| Routing Lane | Anchors | Covered Capabilities | Status |
|---|---|---|---|
| `task/implementation` | `omp-native.event-stream-multiplexer`, `omp-native.feature-matrix-compiler` | `code-generation`, `test-authoring`, `repository-navigation`, `targeted-verification` | **`LANE_STRUCTURALLY_READY`** |
| `task/debugging` | `terminal-bench.cancel-async-tasks`, `omp-native.distributed-lease-deadlock` | `defect-reproduction`, `root-cause-analysis`, `targeted-patching`, `regression-testing` | **`LANE_STRUCTURALLY_READY`** |
| `task/test-repair` | `omp-native.api-contract-test-modernization`, `omp-native.pytest-fixture-migration` | `test-diagnosis`, `harness-repair`, `mock-adaptation`, `contract-preservation` | **`LANE_STRUCTURALLY_READY`** |
| `task/refactor` | `omp-native.sync-to-async-client-refactor`, `omp-native.modernize-scientific-stack` | `ast-refactoring`, `callsite-migration`, `type-safety-preservation`, `behavior-equivalence` | **`LANE_STRUCTURALLY_READY`** |
| `task/repo-research` | `omp-native.architecture-dependency-audit`, `omp-native.vulnerability-impact-trace` | `codebase-scouting`, `cross-file-tracing`, `grounded-summarization`, `read-only-safety` | **`LANE_STRUCTURALLY_READY`** |
| `task/mechanical-edit` | `omp-native.schema-enum-sync`, `omp-native.deprecation-annotation-pass` | `batch-editing`, `pattern-application`, `strict-formatting`, `exhaustiveness` | **`LANE_STRUCTURALLY_READY`** |

---

## 3. Next Milestone: Multi-Model Calibration & Routing Policy Generation

With structural completeness reached, the benchmark corpus is ready for model evaluation:

```text
Structural Corpus Freeze (Done)
  └── 11 Canonical Roles + 6 Task Lanes (27 Qualified Anchors)
        │
        ▼
Candidate Model Selection (Operator-Specified)
  ├── User designates exact candidate model routes (e.g. GPT-5, Gemini 3.7, DeepSeek V4, Grok 4.6, Claude)
  └── Candidate routes registered with thinking levels & capacity pools
        │
        ▼
Dual Evaluation Run (Public Calibration + Private Diagnostic Suites)
  ├── Public Task Packs (contracts/task-packs/*.json)
  └── Local Private Diagnostics (.rolebench/task-packs/private-v1.json)
        │
        ▼
Statistical Estimation & Policy Optimization
  ├── Compute role × lane capability snapshots (point, lower, upper bounds)
  ├── Measure held-out allocation regret reduction across specialized lanes
  └── Output omp.role-routing-policy/v1 snapshot for OMP runtime router
```
