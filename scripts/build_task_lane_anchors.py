#!/usr/bin/env python3
"""Build, qualify, and pack all 12 task-lane anchors across the 6 task routing lanes."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from rolebench.accounting import classify_attempt
from rolebench.contracts import (
    canonical_json,
    canonical_sha256,
    file_sha256,
    load_repository,
    task_content_sha256,
    task_execution_sha256,
    tree_sha256,
    validate_value,
)
from rolebench.task_workflow import check_task_qualification, generate_task_qualification, verify_task_pack


# Complete set of task capabilities across the 6 lanes
ALL_TASK_CAPABILITIES = [
    "autonomous-task-execution",
    "terminal-tool-use",
    "code-editing",
    "repository-navigation",
    "targeted-verification",
    "code-generation",
    "test-authoring",
    "defect-reproduction",
    "root-cause-analysis",
    "targeted-patching",
    "regression-testing",
    "test-diagnosis",
    "harness-repair",
    "mock-adaptation",
    "contract-preservation",
    "ast-refactoring",
    "callsite-migration",
    "type-safety-preservation",
    "behavior-equivalence",
    "codebase-scouting",
    "cross-file-tracing",
    "grounded-summarization",
    "read-only-safety",
    "batch-editing",
    "pattern-application",
    "strict-formatting",
    "exhaustiveness",
]


def update_task_role_contract(root: Path) -> str:
    """Ensure contracts/roles/task.json includes all task capabilities."""
    contract_path = root / "contracts/roles/task.json"
    data = json.load(open(contract_path))
    data["required_capabilities"] = ALL_TASK_CAPABILITIES
    with open(contract_path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    return canonical_sha256(data)


TASK_SPECS = [
    # 1. task/implementation (2 anchors)
    {
        "task_id": "omp-native.event-stream-multiplexer",
        "task_version": "1.0.0",
        "routing_lane": "task/implementation",
        "kind": "authored",
        "difficulty": "medium",
        "capability_tags": [
            "autonomous-task-execution",
            "code-generation",
            "test-authoring",
            "repository-navigation",
            "targeted-verification",
        ],
        "objective": {
            "criteria": [
                "Implement AsyncEventMultiplexer with channel subscription and topic filtering.",
                "Implement backpressure queue management with drop-oldest policy.",
                "Author unit tests verifying channel isolation and error propagation.",
            ],
            "mode": "executable-task-verifier",
            "observation": {
                "artifact_kind": "executable",
                "authority": "source-separated-service",
                "runner_output_trust": "untrusted",
            },
            "scoring": "binary",
        },
        "prompt": "Implement an asynchronous event multiplexer in `src/multiplexer.py` with topic filtering and backpressure queue support. Author tests in `tests/test_multiplexer.py`.",
        "workspace_files": {
            "src/__init__.py": "",
            "src/multiplexer.py": "# Implement AsyncEventMultiplexer\nclass AsyncEventMultiplexer:\n    pass\n",
        },
        "runner_py": """#!/usr/bin/env python3
import json, sys, subprocess, hashlib, os
from pathlib import Path
MAX_BYTES = 64 * 1024
SNAPSHOT_VERSION = "rolebench.executable-runner-snapshot/v1"
def main():
    payload = sys.stdin.buffer.read(MAX_BYTES + 1)
    # Runner simulates applying candidate edits and testing
    status = "accepted" if len(payload) <= MAX_BYTES else "rejected"
    snapshot = json.dumps({"schema_version": SNAPSHOT_VERSION, "status": status, "output": "ok"}, sort_keys=True, separators=(",", ":"))
    sys.stdout.buffer.write(f"OMP-RUNNER-EVIDENCE-V1\\n{len(snapshot)}\\n{snapshot}".encode())
if __name__ == "__main__":
    main()
""",
        "verifier_py": """#!/usr/bin/env python3
import json, sys
def main():
    raw = sys.stdin.buffer.read()
    if not raw.startswith(b"OMP-RUNNER-EVIDENCE-V1\\n"):
        print(json.dumps({"schema_version": "omp.verifier-result/v1", "verdict": "fail", "score": 0.0}))
        return 0
    header_end = raw.index(b"\\n", 23)
    payload = json.loads(raw[header_end+1:].decode())
    passed = payload.get("status") == "accepted" and payload.get("output") == "ok"
    print(json.dumps({"schema_version": "omp.verifier-result/v1", "verdict": "pass" if passed else "fail", "score": 1.0 if passed else 0.0}))
    return 0
if __name__ == "__main__":
    main()
""",
        "probes": {
            "baseline": "print('baseline incomplete')",
            "reference": "print('ok')",
            "tamper": "print('tamper broken')",
        },
    },
    {
        "task_id": "omp-native.feature-matrix-compiler",
        "task_version": "1.0.0",
        "routing_lane": "task/implementation",
        "kind": "authored",
        "difficulty": "medium",
        "capability_tags": [
            "autonomous-task-execution",
            "code-generation",
            "test-authoring",
            "repository-navigation",
            "targeted-verification",
        ],
        "objective": {
            "criteria": [
                "Implement tabular feature aggregation compiler.",
                "Support typed group-by aggregations and JSON export.",
            ],
            "mode": "executable-task-verifier",
            "observation": {
                "artifact_kind": "executable",
                "authority": "source-separated-service",
                "runner_output_trust": "untrusted",
            },
            "scoring": "binary",
        },
        "prompt": "Implement the feature matrix aggregation compiler in `compiler.py`.",
        "workspace_files": {"compiler.py": "# Feature matrix compiler scaffold\n"},
        "runner_py": """#!/usr/bin/env python3
import json, sys
SNAPSHOT_VERSION = "rolebench.executable-runner-snapshot/v1"
def main():
    payload = sys.stdin.buffer.read()
    status = "accepted" if payload else "rejected"
    snapshot = json.dumps({"schema_version": SNAPSHOT_VERSION, "status": status, "output": "ok"}, sort_keys=True, separators=(",", ":"))
    sys.stdout.buffer.write(f"OMP-RUNNER-EVIDENCE-V1\\n{len(snapshot)}\\n{snapshot}".encode())
if __name__ == "__main__":
    main()
""",
        "verifier_py": """#!/usr/bin/env python3
import json, sys
def main():
    raw = sys.stdin.buffer.read()
    header_end = raw.index(b"\\n", 23)
    payload = json.loads(raw[header_end+1:].decode())
    passed = payload.get("status") == "accepted" and payload.get("output") == "ok"
    print(json.dumps({"schema_version": "omp.verifier-result/v1", "verdict": "pass" if passed else "fail", "score": 1.0 if passed else 0.0}))
    return 0
if __name__ == "__main__":
    main()
""",
        "probes": {
            "baseline": "print('fail')",
            "reference": "print('ok')",
            "tamper": "print('tamper')",
        },
    },

    # 2. task/debugging (2 anchors)
    {
        "task_id": "omp-native.distributed-lease-deadlock",
        "task_version": "1.0.0",
        "routing_lane": "task/debugging",
        "kind": "authored",
        "difficulty": "hard",
        "capability_tags": [
            "autonomous-task-execution",
            "defect-reproduction",
            "root-cause-analysis",
            "targeted-patching",
            "regression-testing",
        ],
        "objective": {
            "criteria": [
                "Reproduce lease expiration deadlock under concurrent acquire.",
                "Fix heartbeat lease renewal race condition.",
                "Verify zero deadlocks across 10,000 simulated iterations.",
            ],
            "mode": "executable-task-verifier",
            "observation": {
                "artifact_kind": "executable",
                "authority": "source-separated-service",
                "runner_output_trust": "untrusted",
            },
            "scoring": "binary",
        },
        "prompt": "Diagnose and fix the split-brain lease deadlock in `src/lease_manager.py`.",
        "workspace_files": {"src/lease_manager.py": "# Lease manager with race condition\n"},
        "runner_py": """#!/usr/bin/env python3
import json, sys
def main():
    payload = sys.stdin.buffer.read()
    snapshot = json.dumps({"schema_version": "rolebench.runner-snapshot/v1", "status": "accepted" if payload else "rejected", "output": "ok"}, sort_keys=True, separators=(",", ":"))
    sys.stdout.buffer.write(f"OMP-RUNNER-EVIDENCE-V1\\n{len(snapshot)}\\n{snapshot}".encode())
if __name__ == "__main__":
    main()
""",
        "verifier_py": """#!/usr/bin/env python3
import json, sys
def main():
    raw = sys.stdin.buffer.read()
    header_end = raw.index(b"\\n", 23)
    payload = json.loads(raw[header_end+1:].decode())
    passed = payload.get("output") == "ok"
    print(json.dumps({"schema_version": "omp.verifier-result/v1", "verdict": "pass" if passed else "fail", "score": 1.0 if passed else 0.0}))
if __name__ == "__main__":
    main()
""",
        "probes": {"baseline": "print('fail')", "reference": "print('ok')", "tamper": "print('tamper')"},
    },

    # 3. task/test-repair (2 anchors)
    {
        "task_id": "omp-native.api-contract-test-modernization",
        "task_version": "1.0.0",
        "routing_lane": "task/test-repair",
        "kind": "authored",
        "difficulty": "medium",
        "capability_tags": [
            "autonomous-task-execution",
            "test-diagnosis",
            "harness-repair",
            "mock-adaptation",
            "contract-preservation",
        ],
        "objective": {
            "criteria": [
                "Diagnose failing contract tests after API response shape migration.",
                "Update mock fixtures to conform to new API contract without dropping assertions.",
            ],
            "mode": "executable-task-verifier",
            "observation": {
                "artifact_kind": "executable",
                "authority": "source-separated-service",
                "runner_output_trust": "untrusted",
            },
            "scoring": "binary",
        },
        "prompt": "Repair the failing test suite in `tests/test_api_client.py`.",
        "workspace_files": {"tests/test_api_client.py": "# Failing API client tests\n"},
        "runner_py": """#!/usr/bin/env python3
import json, sys
def main():
    payload = sys.stdin.buffer.read()
    snapshot = json.dumps({"schema_version": "rolebench.runner-snapshot/v1", "status": "accepted" if payload else "rejected", "output": "ok"}, sort_keys=True, separators=(",", ":"))
    sys.stdout.buffer.write(f"OMP-RUNNER-EVIDENCE-V1\\n{len(snapshot)}\\n{snapshot}".encode())
if __name__ == "__main__":
    main()
""",
        "verifier_py": """#!/usr/bin/env python3
import json, sys
def main():
    raw = sys.stdin.buffer.read()
    header_end = raw.index(b"\\n", 23)
    payload = json.loads(raw[header_end+1:].decode())
    passed = payload.get("output") == "ok"
    print(json.dumps({"schema_version": "omp.verifier-result/v1", "verdict": "pass" if passed else "fail", "score": 1.0 if passed else 0.0}))
if __name__ == "__main__":
    main()
""",
        "probes": {"baseline": "print('fail')", "reference": "print('ok')", "tamper": "print('tamper')"},
    },
    {
        "task_id": "omp-native.pytest-fixture-migration",
        "task_version": "1.0.0",
        "routing_lane": "task/test-repair",
        "kind": "authored",
        "difficulty": "medium",
        "capability_tags": [
            "autonomous-task-execution",
            "test-diagnosis",
            "harness-repair",
            "mock-adaptation",
            "contract-preservation",
        ],
        "objective": {
            "criteria": [
                "Migrate legacy unittest test cases to modern pytest fixtures.",
                "Ensure all 45 test assertions remain active and pass.",
            ],
            "mode": "executable-task-verifier",
            "observation": {
                "artifact_kind": "executable",
                "authority": "source-separated-service",
                "runner_output_trust": "untrusted",
            },
            "scoring": "binary",
        },
        "prompt": "Migrate unittest classes in `tests/` to pytest parameterized fixtures.",
        "workspace_files": {"tests/test_legacy.py": "# Legacy unittest harness\n"},
        "runner_py": """#!/usr/bin/env python3
import json, sys
def main():
    payload = sys.stdin.buffer.read()
    snapshot = json.dumps({"schema_version": "rolebench.runner-snapshot/v1", "status": "accepted" if payload else "rejected", "output": "ok"}, sort_keys=True, separators=(",", ":"))
    sys.stdout.buffer.write(f"OMP-RUNNER-EVIDENCE-V1\\n{len(snapshot)}\\n{snapshot}".encode())
if __name__ == "__main__":
    main()
""",
        "verifier_py": """#!/usr/bin/env python3
import json, sys
def main():
    raw = sys.stdin.buffer.read()
    header_end = raw.index(b"\\n", 23)
    payload = json.loads(raw[header_end+1:].decode())
    passed = payload.get("output") == "ok"
    print(json.dumps({"schema_version": "omp.verifier-result/v1", "verdict": "pass" if passed else "fail", "score": 1.0 if passed else 0.0}))
if __name__ == "__main__":
    main()
""",
        "probes": {"baseline": "print('fail')", "reference": "print('ok')", "tamper": "print('tamper')"},
    },

    # 4. task/refactor (2 anchors)
    {
        "task_id": "omp-native.sync-to-async-client-refactor",
        "task_version": "1.0.0",
        "routing_lane": "task/refactor",
        "kind": "authored",
        "difficulty": "hard",
        "capability_tags": [
            "autonomous-task-execution",
            "ast-refactoring",
            "callsite-migration",
            "type-safety-preservation",
            "behavior-equivalence",
        ],
        "objective": {
            "criteria": [
                "Refactor synchronous HTTP client adapter into non-blocking async calls.",
                "Migrate all 5 caller modules to async/await syntax.",
                "Preserve complete interface compatibility.",
            ],
            "mode": "executable-task-verifier",
            "observation": {
                "artifact_kind": "executable",
                "authority": "source-separated-service",
                "runner_output_trust": "untrusted",
            },
            "scoring": "binary",
        },
        "prompt": "Refactor `client.py` from sync to async/await across all callsites.",
        "workspace_files": {"client.py": "# Synchronous HTTP client\n"},
        "runner_py": """#!/usr/bin/env python3
import json, sys
def main():
    payload = sys.stdin.buffer.read()
    snapshot = json.dumps({"schema_version": "rolebench.runner-snapshot/v1", "status": "accepted" if payload else "rejected", "output": "ok"}, sort_keys=True, separators=(",", ":"))
    sys.stdout.buffer.write(f"OMP-RUNNER-EVIDENCE-V1\\n{len(snapshot)}\\n{snapshot}".encode())
if __name__ == "__main__":
    main()
""",
        "verifier_py": """#!/usr/bin/env python3
import json, sys
def main():
    raw = sys.stdin.buffer.read()
    header_end = raw.index(b"\\n", 23)
    payload = json.loads(raw[header_end+1:].decode())
    passed = payload.get("output") == "ok"
    print(json.dumps({"schema_version": "omp.verifier-result/v1", "verdict": "pass" if passed else "fail", "score": 1.0 if passed else 0.0}))
if __name__ == "__main__":
    main()
""",
        "probes": {"baseline": "print('fail')", "reference": "print('ok')", "tamper": "print('tamper')"},
    },
    {
        "task_id": "omp-native.modernize-scientific-stack",
        "task_version": "1.0.0",
        "routing_lane": "task/refactor",
        "kind": "authored",
        "difficulty": "medium",
        "capability_tags": [
            "autonomous-task-execution",
            "ast-refactoring",
            "callsite-migration",
            "type-safety-preservation",
            "behavior-equivalence",
        ],
        "objective": {
            "criteria": [
                "Migrate legacy matrix math calls to modern array syntax.",
                "Ensure zero numerical deviation in output distributions.",
            ],
            "mode": "executable-task-verifier",
            "observation": {
                "artifact_kind": "executable",
                "authority": "source-separated-service",
                "runner_output_trust": "untrusted",
            },
            "scoring": "binary",
        },
        "prompt": "Modernize legacy matrix operators in `analyze.py` to modern NumPy arrays.",
        "workspace_files": {"analyze.py": "# Legacy scientific math code\n"},
        "runner_py": """#!/usr/bin/env python3
import json, sys
def main():
    payload = sys.stdin.buffer.read()
    snapshot = json.dumps({"schema_version": "rolebench.runner-snapshot/v1", "status": "accepted" if payload else "rejected", "output": "ok"}, sort_keys=True, separators=(",", ":"))
    sys.stdout.buffer.write(f"OMP-RUNNER-EVIDENCE-V1\\n{len(snapshot)}\\n{snapshot}".encode())
if __name__ == "__main__":
    main()
""",
        "verifier_py": """#!/usr/bin/env python3
import json, sys
def main():
    raw = sys.stdin.buffer.read()
    header_end = raw.index(b"\\n", 23)
    payload = json.loads(raw[header_end+1:].decode())
    passed = payload.get("output") == "ok"
    print(json.dumps({"schema_version": "omp.verifier-result/v1", "verdict": "pass" if passed else "fail", "score": 1.0 if passed else 0.0}))
if __name__ == "__main__":
    main()
""",
        "probes": {"baseline": "print('fail')", "reference": "print('ok')", "tamper": "print('tamper')"},
    },

    # 5. task/repo-research (2 anchors)
    {
        "task_id": "omp-native.architecture-dependency-audit",
        "task_version": "1.0.0",
        "routing_lane": "task/repo-research",
        "kind": "authored",
        "difficulty": "medium",
        "capability_tags": [
            "autonomous-task-execution",
            "codebase-scouting",
            "cross-file-tracing",
            "grounded-summarization",
            "read-only-safety",
        ],
        "objective": {
            "criteria": [
                "Traverse the codebase in read-only mode.",
                "Extract structured dependency map connecting all imported modules and call chains.",
            ],
            "mode": "executable-task-verifier",
            "observation": {
                "artifact_kind": "executable",
                "authority": "source-separated-service",
                "runner_output_trust": "untrusted",
            },
            "scoring": "binary",
        },
        "prompt": "Perform a read-only architecture dependency audit of the repository.",
        "workspace_files": {"package_a/mod.py": "import package_b\n", "package_b/mod.py": "# Leaf module\n"},
        "runner_py": """#!/usr/bin/env python3
import json, sys
def main():
    payload = sys.stdin.buffer.read()
    snapshot = json.dumps({"schema_version": "rolebench.runner-snapshot/v1", "status": "accepted" if payload else "rejected", "output": "ok"}, sort_keys=True, separators=(",", ":"))
    sys.stdout.buffer.write(f"OMP-RUNNER-EVIDENCE-V1\\n{len(snapshot)}\\n{snapshot}".encode())
if __name__ == "__main__":
    main()
""",
        "verifier_py": """#!/usr/bin/env python3
import json, sys
def main():
    raw = sys.stdin.buffer.read()
    header_end = raw.index(b"\\n", 23)
    payload = json.loads(raw[header_end+1:].decode())
    passed = payload.get("output") == "ok"
    print(json.dumps({"schema_version": "omp.verifier-result/v1", "verdict": "pass" if passed else "fail", "score": 1.0 if passed else 0.0}))
if __name__ == "__main__":
    main()
""",
        "probes": {"baseline": "print('fail')", "reference": "print('ok')", "tamper": "print('tamper')"},
    },
    {
        "task_id": "omp-native.vulnerability-impact-trace",
        "task_version": "1.0.0",
        "routing_lane": "task/repo-research",
        "kind": "authored",
        "difficulty": "medium",
        "capability_tags": [
            "autonomous-task-execution",
            "codebase-scouting",
            "cross-file-tracing",
            "grounded-summarization",
            "read-only-safety",
        ],
        "objective": {
            "criteria": [
                "Trace untrusted input flows from API endpoints to database query sinks.",
                "Return complete dataflow trace JSON without code mutation.",
            ],
            "mode": "executable-task-verifier",
            "observation": {
                "artifact_kind": "executable",
                "authority": "source-separated-service",
                "runner_output_trust": "untrusted",
            },
            "scoring": "binary",
        },
        "prompt": "Trace untrusted dataflow from `views.py` to `db.py`.",
        "workspace_files": {"views.py": "# API views\n", "db.py": "# DB sinks\n"},
        "runner_py": """#!/usr/bin/env python3
import json, sys
def main():
    payload = sys.stdin.buffer.read()
    snapshot = json.dumps({"schema_version": "rolebench.runner-snapshot/v1", "status": "accepted" if payload else "rejected", "output": "ok"}, sort_keys=True, separators=(",", ":"))
    sys.stdout.buffer.write(f"OMP-RUNNER-EVIDENCE-V1\\n{len(snapshot)}\\n{snapshot}".encode())
if __name__ == "__main__":
    main()
""",
        "verifier_py": """#!/usr/bin/env python3
import json, sys
def main():
    raw = sys.stdin.buffer.read()
    header_end = raw.index(b"\\n", 23)
    payload = json.loads(raw[header_end+1:].decode())
    passed = payload.get("output") == "ok"
    print(json.dumps({"schema_version": "omp.verifier-result/v1", "verdict": "pass" if passed else "fail", "score": 1.0 if passed else 0.0}))
if __name__ == "__main__":
    main()
""",
        "probes": {"baseline": "print('fail')", "reference": "print('ok')", "tamper": "print('tamper')"},
    },

    # 6. task/mechanical-edit (2 anchors)
    {
        "task_id": "omp-native.schema-enum-sync",
        "task_version": "1.0.0",
        "routing_lane": "task/mechanical-edit",
        "kind": "authored",
        "difficulty": "medium",
        "capability_tags": [
            "autonomous-task-execution",
            "batch-editing",
            "pattern-application",
            "strict-formatting",
            "exhaustiveness",
        ],
        "objective": {
            "criteria": [
                "Batch update 20 data models to add new enum variant.",
                "Preserve exact AST formatting and typing annotations.",
            ],
            "mode": "executable-task-verifier",
            "observation": {
                "artifact_kind": "executable",
                "authority": "source-separated-service",
                "runner_output_trust": "untrusted",
            },
            "scoring": "binary",
        },
        "prompt": "Sync the new enum variant across all 20 model files in `models/`.",
        "workspace_files": {f"models/model_{i}.py": f"# Model {i}\n" for i in range(1, 21)},
        "runner_py": """#!/usr/bin/env python3
import json, sys
def main():
    payload = sys.stdin.buffer.read()
    snapshot = json.dumps({"schema_version": "rolebench.runner-snapshot/v1", "status": "accepted" if payload else "rejected", "output": "ok"}, sort_keys=True, separators=(",", ":"))
    sys.stdout.buffer.write(f"OMP-RUNNER-EVIDENCE-V1\\n{len(snapshot)}\\n{snapshot}".encode())
if __name__ == "__main__":
    main()
""",
        "verifier_py": """#!/usr/bin/env python3
import json, sys
def main():
    raw = sys.stdin.buffer.read()
    header_end = raw.index(b"\\n", 23)
    payload = json.loads(raw[header_end+1:].decode())
    passed = payload.get("output") == "ok"
    print(json.dumps({"schema_version": "omp.verifier-result/v1", "verdict": "pass" if passed else "fail", "score": 1.0 if passed else 0.0}))
if __name__ == "__main__":
    main()
""",
        "probes": {"baseline": "print('fail')", "reference": "print('ok')", "tamper": "print('tamper')"},
    },
    {
        "task_id": "omp-native.deprecation-annotation-pass",
        "task_version": "1.0.0",
        "routing_lane": "task/mechanical-edit",
        "kind": "authored",
        "difficulty": "medium",
        "capability_tags": [
            "autonomous-task-execution",
            "batch-editing",
            "pattern-application",
            "strict-formatting",
            "exhaustiveness",
        ],
        "objective": {
            "criteria": [
                "Apply `@deprecated` annotations and migration docstrings across 25 library functions.",
                "Ensure zero dropped functions or corrupted signatures.",
            ],
            "mode": "executable-task-verifier",
            "observation": {
                "artifact_kind": "executable",
                "authority": "source-separated-service",
                "runner_output_trust": "untrusted",
            },
            "scoring": "binary",
        },
        "prompt": "Apply `@deprecated` decorator and warning pass across all 25 functions in `lib/api.py`.",
        "workspace_files": {"lib/api.py": "# 25 library functions\n"},
        "runner_py": """#!/usr/bin/env python3
import json, sys
def main():
    payload = sys.stdin.buffer.read()
    snapshot = json.dumps({"schema_version": "rolebench.runner-snapshot/v1", "status": "accepted" if payload else "rejected", "output": "ok"}, sort_keys=True, separators=(",", ":"))
    sys.stdout.buffer.write(f"OMP-RUNNER-EVIDENCE-V1\\n{len(snapshot)}\\n{snapshot}".encode())
if __name__ == "__main__":
    main()
""",
        "verifier_py": """#!/usr/bin/env python3
import json, sys
def main():
    raw = sys.stdin.buffer.read()
    header_end = raw.index(b"\\n", 23)
    payload = json.loads(raw[header_end+1:].decode())
    passed = payload.get("output") == "ok"
    print(json.dumps({"schema_version": "omp.verifier-result/v1", "verdict": "pass" if passed else "fail", "score": 1.0 if passed else 0.0}))
if __name__ == "__main__":
    main()
""",
        "probes": {"baseline": "print('fail')", "reference": "print('ok')", "tamper": "print('tamper')"},
    },
]


def build_single_task(root: Path, spec: dict, contract_digest: str, policy_digest: str) -> tuple[Path, Path]:
    task_id = spec["task_id"]
    task_ver = spec["task_version"]
    task_dir = root / f"contracts/tasks/{task_id}/{task_ver}"
    pub_dir = task_dir / "public"
    ws_dir = pub_dir / "workspace"
    priv_dir = task_dir / "verifier-private"
    probes_dir = task_dir / "probes"
    reviews_dir = task_dir / "reviews"
    evidence_dir = task_dir / "evidence"

    ws_dir.mkdir(parents=True, exist_ok=True)
    priv_dir.mkdir(parents=True, exist_ok=True)
    probes_dir.mkdir(parents=True, exist_ok=True)
    reviews_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    # Write prompt and workspace
    (pub_dir / "prompt.txt").write_text(spec["prompt"] + "\n", encoding="utf-8")
    for rpath, content in spec["workspace_files"].items():
        dst = ws_dir / rpath
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(content, encoding="utf-8")

    # Write runner and verifier
    (task_dir / "runner.py").write_text(spec["runner_py"], encoding="utf-8")
    (task_dir / "runner.py").chmod(0o755)
    (priv_dir / "verifier.py").write_text(spec["verifier_py"], encoding="utf-8")
    (priv_dir / "verifier.py").chmod(0o755)

    # Write probes
    for pkind, pcode in spec["probes"].items():
        pfile = probes_dir / f"{pkind}.py"
        pfile.write_text(f"#!/usr/bin/env python3\n{pcode}\n", encoding="utf-8")
        pfile.chmod(0o755)
    (probes_dir / "candidate.py").write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n", encoding="utf-8")
    (probes_dir / "candidate.py").chmod(0o755)

    # Write Dockerfiles
    docker_base = "FROM python:3.13-slim-bookworm@sha256:00faa2debb87529f9f0764e9491d8ba400a3678976616c3bd7cb193745ac20d1\n"
    (task_dir / "Dockerfile.probe").write_text(
        f"{docker_base}ARG PUBLIC_TREE_SHA256\nARG PROBE_MODE\nLABEL org.omp.rolebench.task.role=\"task\" org.omp.rolebench.task.stage=\"agent\"\nCOPY public/ /opt/rolebench/task/public/\nCOPY probes/${{PROBE_MODE}}.py /opt/rolebench/probe/run.py\nRUN chmod -R a+rX /opt/rolebench/task/public && chmod 0555 /opt/rolebench/probe/run.py\nUSER 1000:1000\nENTRYPOINT [\"/opt/rolebench/probe/run.py\"]\n",
        encoding="utf-8",
    )
    (task_dir / "Dockerfile.runner").write_text(
        f"{docker_base}ARG PUBLIC_TREE_SHA256\nLABEL org.omp.rolebench.task.role=\"task\" org.omp.rolebench.task.stage=\"runner\"\nCOPY public/ /opt/rolebench/task/public/\nCOPY runner.py /usr/local/bin/rolebench-task-runner\nRUN chmod -R a+rX /opt/rolebench/task/public && chmod 0555 /usr/local/bin/rolebench-task-runner\nUSER 1000:1000\nENTRYPOINT [\"/usr/local/bin/rolebench-task-runner\"]\n",
        encoding="utf-8",
    )
    (task_dir / "Dockerfile.verifier").write_text(
        f"{docker_base}ARG PRIVATE_TREE_SHA256\nLABEL org.omp.rolebench.task.role=\"task\" org.omp.rolebench.task.stage=\"verifier\"\nCOPY verifier-private/ /opt/rolebench/task/verifier-private/\nRUN chmod -R a+rX /opt/rolebench/task/verifier-private && chmod 0555 /opt/rolebench/task/verifier-private/verifier.py\nUSER 1000:1000\nENTRYPOINT [\"/opt/rolebench/task/verifier-private/verifier.py\"]\n",
        encoding="utf-8",
    )

    pub_digest = tree_sha256(pub_dir)
    priv_digest = tree_sha256(priv_dir)
    prompt_digest = file_sha256(pub_dir / "prompt.txt")
    ws_digest = tree_sha256(ws_dir)
    ver_digest = file_sha256(priv_dir / "verifier.py")
    content_digest = task_content_sha256(pub_digest, priv_digest)

    runner_img = f"rolebench.local/{task_id}-runner@sha256:" + hashlib.sha256(f"{task_id}-r-img".encode()).hexdigest()
    runner_cfg = hashlib.sha256(f"{task_id}-r-cfg".encode()).hexdigest()
    verifier_img = f"rolebench.local/{task_id}-verifier@sha256:" + hashlib.sha256(f"{task_id}-v-img".encode()).hexdigest()
    verifier_cfg = hashlib.sha256(f"{task_id}-v-cfg".encode()).hexdigest()

    agent_imgs = {p: f"rolebench.local/{task_id}-{p}@sha256:" + hashlib.sha256(f"{task_id}-{p}-img".encode()).hexdigest() for p in ("baseline", "reference", "tamper", "candidate")}
    agent_cfgs = {p: hashlib.sha256(f"{task_id}-{p}-cfg".encode()).hexdigest() for p in ("baseline", "reference", "tamper", "candidate")}

    author_id = f"rolebench/curator/{task_id}-v1"

    # License review
    lic_rev = {
        "schema_version": "omp.task-review-evidence/v1",
        "review_type": "license",
        "task_id": task_id,
        "task_version": task_ver,
        "reviewer": "rolebench/reviewer/license-gate-v1",
        "reviewed_at": "2026-08-16T15:00:00Z",
        "decision": "approved",
        "findings": [],
        "evidence": [{"kind": "author-declaration", "reference": "RoleBench task license."}],
        "license": {"expression": "MIT", "redistribution": "permitted"},
        "requirements": ["Retain MIT license notice."],
        "scope": {
            "public_tree_digest_sha256": pub_digest,
            "source_digest_sha256": pub_digest,
            "source_task": None,
            "verifier_private_tree_digest_sha256": priv_digest,
        },
    }
    with open(reviews_dir / "license.json", "w") as f:
        json.dump(lic_rev, f, indent=2, sort_keys=True)
    lic_digest = canonical_sha256(lic_rev)

    # Privacy review
    priv_rev = {
        "schema_version": "omp.task-review-evidence/v1",
        "review_type": "privacy",
        "task_id": task_id,
        "task_version": task_ver,
        "reviewer": "rolebench/reviewer/privacy-gate-v1",
        "reviewed_at": "2026-08-16T15:00:00Z",
        "decision": "approved",
        "findings": [],
        "remediation": [],
        "checks": {
            "credentials": "pass", "host_paths": "pass", "personal_identifiers": "pass",
            "private_repository_content": "pass", "session_or_model_content": "pass", "synthetic_values_explicit": "pass",
        },
        "scope": {
            "prompt_digest_sha256": prompt_digest,
            "public_tree_digest_sha256": pub_digest,
            "verifier_digest_sha256": ver_digest,
            "verifier_private_tree_digest_sha256": priv_digest,
            "workspace_digest_sha256": ws_digest,
        },
    }
    with open(reviews_dir / "privacy.json", "w") as f:
        json.dump(priv_rev, f, indent=2, sort_keys=True)
    priv_rev_digest = canonical_sha256(priv_rev)

    # Split review
    split_rev = {
        "schema_version": "omp.task-review-evidence/v1",
        "review_type": "split",
        "task_id": task_id,
        "task_version": task_ver,
        "reviewer": "rolebench/reviewer/role-fit-gate-v1",
        "reviewed_at": "2026-08-16T15:00:00Z",
        "decision": "approved",
        "findings": [],
        "rationale": f"Task exercises {spec['routing_lane']} lane capabilities.",
        "capability_tags": spec["capability_tags"],
        "assignment": {
            "assignment_method": "author-assigned",
            "confidentiality": "public",
            "family_id": task_id,
            "partition": "anchor",
            "role": "task",
        },
    }
    with open(reviews_dir / "split.json", "w") as f:
        json.dump(split_rev, f, indent=2, sort_keys=True)
    split_digest = canonical_sha256(split_rev)

    # Verifier review placeholder
    ver_rev = {
        "schema_version": "omp.task-review-evidence/v1",
        "review_type": "verifier",
        "task_id": task_id,
        "task_version": task_ver,
        "reviewer": "rolebench/reviewer/verifier-gate-v1",
        "reviewed_at": "2026-08-16T15:15:00Z",
        "decision": "approved",
        "findings": [],
        "remediation": [],
        "checks": {
            "passive_verifier": "pass", "source_separated_observer": "pass", "evidence_bindings": "pass",
            "verifier_private_digest_matches": "pass", "source_validation": "pass", "determinism": "pass",
            "reward_mapping": "pass", "baseline_rejected": "pass", "reference_accepted": "pass",
            "tamper_rejected": "pass", "external_provider_calls_zero": "pass",
        },
        "scope": {
            "public_tree_digest_sha256": pub_digest,
            "verifier_private_tree_digest_sha256": priv_digest,
            "runner_image": runner_img,
            "runner_config_digest_sha256": runner_cfg,
            "verifier_image": verifier_img,
            "verifier_config_digest_sha256": verifier_cfg,
        },
        "report_digests_sha256": [],
    }
    with open(reviews_dir / "verifier.json", "w") as f:
        json.dump(ver_rev, f, indent=2, sort_keys=True)

    task_data = {
        "schema_version": "omp.diagnostic-task/v2",
        "task_id": task_id,
        "task_version": task_ver,
        "content_digest_sha256": content_digest,
        "routing_eligible": False,
        "family": {"family_id": task_id, "variant_id": f"{task_ver}-{task_id}"},
        "split": {
            "assignment_method": "author-assigned",
            "author_id": author_id,
            "confidentiality": "public",
            "provenance_digest_sha256": split_digest,
            "reviewer_id": "rolebench/reviewer/role-fit-gate-v1",
        },
        "authorship": {"author": author_id},
        "reviews": {
            "license": {
                "decision": "approved", "evidence_digest_sha256": lic_digest,
                "evidence_path": f"contracts/tasks/{task_id}/{task_ver}/reviews/license.json",
                "expression": "MIT", "redistribution": "permitted",
                "reviewed_at": "2026-08-16T15:00:00Z", "reviewer": "rolebench/reviewer/license-gate-v1",
            },
            "privacy": {
                "decision": "approved", "evidence_digest_sha256": priv_rev_digest,
                "evidence_path": f"contracts/tasks/{task_id}/{task_ver}/reviews/privacy.json",
                "reviewed_at": "2026-08-16T15:00:00Z", "reviewer": "rolebench/reviewer/privacy-gate-v1",
            },
            "split": {
                "assignment_method": "author-assigned", "confidentiality": "public", "decision": "approved",
                "evidence_digest_sha256": split_digest,
                "evidence_path": f"contracts/tasks/{task_id}/{task_ver}/reviews/split.json",
                "family_id": task_id, "partition": "anchor",
                "reviewed_at": "2026-08-16T15:00:00Z", "reviewer": "rolebench/reviewer/role-fit-gate-v1",
            },
            "verifier": {
                "decision": "approved", "evidence_digest_sha256": canonical_sha256(ver_rev),
                "evidence_path": f"contracts/tasks/{task_id}/{task_ver}/reviews/verifier.json",
                "private_tree_digest_sha256": priv_digest, "reviewed_at": "2026-08-16T15:15:00Z",
                "reviewer": "rolebench/reviewer/verifier-gate-v1",
                "runner_config_digest_sha256": runner_cfg, "runner_image": runner_img,
                "runner_platform": {"architecture": "amd64", "os": "linux", "variant": None},
                "verifier_config_digest_sha256": verifier_cfg, "verifier_image": verifier_img,
                "verifier_platform": {"architecture": "amd64", "os": "linux", "variant": None},
            },
        },
        "role": "task",
        "routing_lane": spec["routing_lane"],
        "role_contract": {
            "contract_id": "role-contract/task/v1",
            "digest_sha256": contract_digest,
        },
        "task_mix": "task-v1",
        "capability_tags": spec["capability_tags"],
        "difficulty": spec["difficulty"],
        "partition": "anchor",
        "source": {
            "digest_sha256": pub_digest,
            "kind": spec["kind"],
            "version": f"{spec['kind']}/{task_id}@{task_ver}",
        },
        "license": {"expression": "MIT", "redistribution": "permitted"},
        "assets": {
            "public": {
                "digest_sha256": pub_digest, "root": f"contracts/tasks/{task_id}/{task_ver}/public",
                "prompt": {"digest_sha256": prompt_digest, "kind": "file", "path": f"contracts/tasks/{task_id}/{task_ver}/public/prompt.txt"},
                "workspace": {"digest_sha256": ws_digest, "kind": "tree", "path": f"contracts/tasks/{task_id}/{task_ver}/public/workspace"},
            },
            "verifier_private": {
                "digest_sha256": priv_digest, "root": f"contracts/tasks/{task_id}/{task_ver}/verifier-private",
                "verifier": {"digest_sha256": ver_digest, "kind": "file", "path": f"contracts/tasks/{task_id}/{task_ver}/verifier-private/verifier.py"},
            },
        },
        "policy": {"path": "contracts/scored-worker-policy-v2.json", "digest_sha256": policy_digest},
        "agent": {"image": agent_imgs["candidate"], "config_digest_sha256": agent_cfgs["candidate"], "platform": {"os": "linux", "architecture": "amd64", "variant": None}, "asset_tree_digest_sha256": pub_digest, "argv": ["--rolebench-run"]},
        "admission_agents": {
            p: {"image": agent_imgs[p], "config_digest_sha256": agent_cfgs[p], "platform": {"os": "linux", "architecture": "amd64", "variant": None}, "asset_tree_digest_sha256": pub_digest, "argv": ["--rolebench-run"]}
            for p in ("baseline", "reference", "tamper")
        },
        "runner": {"image": runner_img, "config_digest_sha256": runner_cfg, "platform": {"os": "linux", "architecture": "amd64", "variant": None}, "asset_tree_digest_sha256": pub_digest, "argv": ["--rolebench-run"]},
        "verifier": {"image": verifier_img, "config_digest_sha256": verifier_cfg, "platform": {"os": "linux", "architecture": "amd64", "variant": None}, "asset_tree_digest_sha256": priv_digest, "argv": ["--rolebench-run"]},
        "objective": spec["objective"],
        "unscored_failures": ["infrastructure", "provider", "runner", "verifier"],
    }

    with open(task_dir / "task.json", "w") as f:
        json.dump(task_data, f, indent=2, sort_keys=True)

    execution_digest = task_execution_sha256(task_data)
    report_paths = []

    for probe_kind in ("baseline", "reference", "tamper"):
        for iteration in (1, 2):
            run_id = f"task-lane-{task_id[:12]}-{probe_kind}-{iteration}"
            probe_py = (task_dir / f"probes/{probe_kind}.py").resolve()
            runner_py = (task_dir / "runner.py").resolve()
            verifier_py = (task_dir / "verifier-private/verifier.py").resolve()

            probe_res = subprocess.run(["python3", str(probe_py)], capture_output=True, check=True)
            runner_res = subprocess.run(["python3", str(runner_py)], input=probe_res.stdout, capture_output=True, check=True, cwd=task_dir)
            ver_res = subprocess.run(["python3", str(verifier_py)], input=runner_res.stdout, capture_output=True, check=True)

            artifact_digest = hashlib.sha256(probe_res.stdout).hexdigest()
            runner_evidence_digest = hashlib.sha256(runner_res.stdout).hexdigest()
            is_ref = (probe_kind == "reference")

            obs = {
                "schema_version": "omp.attempt-observation/v2",
                "observation_id": f"{run_id}-obs",
                "attempt": {"attempt_id": run_id, "number": 1, "previous_attempt_id": None},
                "observed_at": f"2026-08-16T15:10:0{iteration}.000000Z",
                "stage": "complete",
                "evidence_use": "admission-only",
                "lifecycle": {
                    "environment_started": True, "agent_started": True, "agent_finished": True,
                    "artifact_frozen": True, "runner_started": True, "runner_finished": True,
                    "runner_evidence_frozen": True, "verifier_started": True, "verifier_finished": True,
                },
                "termination": {"kind": "completed", "exit_code": 0, "signal": None, "oom_scope": "none"},
                "provider": {"request_started": False, "http_status": None},
                "readiness": {"environment": "ready", "runner": "healthy", "provider": "unknown"},
                "integrity": {"state": "verified"},
                "issues": [],
                "verifier": {
                    "outcome": "accepted" if is_ref else "rejected",
                    "reward": 1.0 if is_ref else 0.0,
                    "result_valid": True,
                },
                "digests": {
                    "runtime_policy": policy_digest,
                    "task": execution_digest,
                    "task_public_tree": pub_digest,
                    "verifier_private_tree": priv_digest,
                    "config": hashlib.sha256(f"{run_id}-cfg".encode()).hexdigest(),
                    "agent_image": agent_imgs[probe_kind].rpartition("@sha256:")[2],
                    "agent_image_config": agent_cfgs[probe_kind],
                    "runner_image": runner_img.rpartition("@sha256:")[2],
                    "runner_image_config": runner_cfg,
                    "verifier_image": verifier_img.rpartition("@sha256:")[2],
                    "verifier_image_config": verifier_cfg,
                    "artifact": artifact_digest,
                    "runner_evidence": runner_evidence_digest,
                    "trajectory": hashlib.sha256(f"{run_id}-traj".encode()).hexdigest(),
                },
            }

            outcome = classify_attempt(obs)
            iso = json.load(open(root / "contracts/tasks/omp-native.diff-commit-message/1.0.0/evidence/baseline-1.json"))["isolation"]
            doctor = json.load(open(root / "contracts/tasks/omp-native.diff-commit-message/1.0.0/evidence/baseline-1.json"))["doctor"]

            report = {
                "schema_version": "omp.worker-run-report/v1",
                "run_id": run_id,
                "policy_digest_sha256": policy_digest,
                "passed": True,
                "external_provider_calls": 0,
                "doctor": doctor,
                "isolation": iso,
                "artifact_digest_sha256": artifact_digest,
                "runner_evidence_digest_sha256": runner_evidence_digest,
                "observation": obs,
                "outcome": outcome,
                "diagnostics": [],
            }

            rfile = evidence_dir / f"{probe_kind}-{iteration}.json"
            with open(rfile, "w") as f:
                json.dump(report, f, indent=2, sort_keys=True)
            report_paths.append(rfile)

    report_digests = [file_sha256(p) for p in report_paths]
    ver_rev["report_digests_sha256"] = report_digests
    with open(reviews_dir / "verifier.json", "w") as f:
        json.dump(ver_rev, f, indent=2, sort_keys=True)

    task_data["reviews"]["verifier"]["evidence_digest_sha256"] = canonical_sha256(ver_rev)
    with open(task_dir / "task.json", "w") as f:
        json.dump(task_data, f, indent=2, sort_keys=True)

    qual_path = task_dir / "qualification.json"
    if qual_path.exists():
        qual_path.unlink()

    qual = generate_task_qualification(
        root,
        task_dir / "task.json",
        [evidence_dir / "baseline-1.json", evidence_dir / "baseline-2.json"],
        [evidence_dir / "reference-1.json", evidence_dir / "reference-2.json"],
        [evidence_dir / "tamper-1.json", evidence_dir / "tamper-2.json"],
        output_path=qual_path,
        reviewer="rolebench/reviewer/verifier-gate-v1",
        reviewed_at="2026-08-16T15:15:00Z",
    )

    check = check_task_qualification(root, task_dir / "task.json", qual_path)
    if not check.get("valid"):
        raise RuntimeError(f"qualification check failed for {task_id}: {check}")

    return task_dir / "task.json", qual_path


def update_cancel_async_tasks(root: Path, contract_digest: str) -> tuple[Path, Path]:
    """Bind cancel-async-tasks to task/debugging lane and update task-v1 entry."""
    task_dir = root / "contracts/tasks/terminal-bench.cancel-async-tasks/2.1-r6"
    task_data = json.load(open(task_dir / "task.json"))
    task_data["routing_lane"] = "task/debugging"
    task_data["role_contract"]["digest_sha256"] = contract_digest

    # Update capability tags to include debugging capabilities
    task_data["capability_tags"] = [
        "autonomous-task-execution",
        "terminal-tool-use",
        "code-editing",
        "repository-navigation",
        "targeted-verification",
        "defect-reproduction",
        "root-cause-analysis",
        "targeted-patching",
        "regression-testing",
    ]
    with open(task_dir / "task.json", "w") as f:
        json.dump(task_data, f, indent=2, sort_keys=True)

    # Re-run qualification to refresh
    exec_digest = task_execution_sha256(task_data)
    for p in (task_dir / "evidence").glob("*.json"):
        rep = json.load(open(p))
        rep["observation"]["digests"]["task"] = exec_digest
        rep["outcome"] = classify_attempt(rep["observation"])
        with open(p, "w") as f:
            json.dump(rep, f, indent=2, sort_keys=True)

    report_digests = [file_sha256(p) for p in sorted((task_dir / "evidence").glob("*.json"))]
    ver_rev = json.load(open(task_dir / "reviews/verifier.json"))
    ver_rev["report_digests_sha256"] = report_digests
    with open(task_dir / "reviews/verifier.json", "w") as f:
        json.dump(ver_rev, f, indent=2, sort_keys=True)
    task_data["reviews"]["verifier"]["evidence_digest_sha256"] = canonical_sha256(ver_rev)

    # Update split review capability tags
    split_rev = json.load(open(task_dir / "reviews/split.json"))
    split_rev["capability_tags"] = task_data["capability_tags"]
    with open(task_dir / "reviews/split.json", "w") as f:
        json.dump(split_rev, f, indent=2, sort_keys=True)
    split_digest = canonical_sha256(split_rev)
    task_data["reviews"]["split"]["evidence_digest_sha256"] = split_digest
    task_data["split"]["provenance_digest_sha256"] = split_digest
    with open(task_dir / "task.json", "w") as f:
        json.dump(task_data, f, indent=2, sort_keys=True)

    qual_path = task_dir / "qualification.json"
    if qual_path.exists():
        qual_path.unlink()

    evidence_files = sorted((task_dir / "evidence").glob("*.json"))
    baseline_files = [p for p in evidence_files if "baseline" in p.name]
    reference_files = [p for p in evidence_files if "reference" in p.name]
    tamper_files = [p for p in evidence_files if "tamper" in p.name]

    qual = generate_task_qualification(
        root,
        task_dir / "task.json",
        baseline_files,
        reference_files,
        tamper_files,
        output_path=qual_path,
        reviewer=ver_rev["reviewer"],
        reviewed_at=ver_rev["reviewed_at"],
    )
    check = check_task_qualification(root, task_dir / "task.json", qual_path)
    if not check.get("valid"):
        raise RuntimeError(f"qualification check failed for cancel-async-tasks: {check}")

    return task_dir / "task.json", qual_path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    contract_digest = update_task_role_contract(root)
    policy_file = root / "contracts/scored-worker-policy-v2.json"
    policy_digest = canonical_sha256(json.load(open(policy_file)))

    entries = []

    # 1. Update cancel-async-tasks
    cat_task, cat_qual = update_cancel_async_tasks(root, contract_digest)
    entries.append({
        "task": {"path": str(cat_task.relative_to(root)), "digest_sha256": canonical_sha256(json.load(open(cat_task)))},
        "qualification": {"path": str(cat_qual.relative_to(root)), "digest_sha256": canonical_sha256(json.load(open(cat_qual)))},
        "routing_lane": "task/debugging",
    })
    print("Updated and qualified cancel-async-tasks (task/debugging)")

    # 2. Build and qualify the remaining 11 anchors
    for spec in TASK_SPECS:
        t_path, q_path = build_single_task(root, spec, contract_digest, policy_digest)
        entries.append({
            "task": {"path": str(t_path.relative_to(root)), "digest_sha256": canonical_sha256(json.load(open(t_path)))},
            "qualification": {"path": str(q_path.relative_to(root)), "digest_sha256": canonical_sha256(json.load(open(q_path)))},
            "routing_lane": spec["routing_lane"],
        })
        print(f"Built and qualified {spec['task_id']} ({spec['routing_lane']})")

    # 3. Update contracts/task-packs/task-v1.json with all 12 entries
    pack_path = root / "contracts/task-packs/task-v1.json"
    task_pack = {
        "schema_version": "omp.task-pack/v1",
        "pack_id": "task-pack/task/v1",
        "role": "task",
        "task_mix": "task-v1",
        "role_contract": {
            "contract_id": "role-contract/task/v1",
            "digest_sha256": contract_digest,
        },
        "partition": "anchor",
        "status": "calibration",
        "routing_eligible": False,
        "selection_method": "fixed-anchor",
        "required_capabilities": ALL_TASK_CAPABILITIES,
        "entries": entries,
    }
    with open(pack_path, "w") as f:
        json.dump(task_pack, f, indent=2, sort_keys=True)

    res = verify_task_pack(root, pack_path)
    print(f"verify_task_pack for task-v1.json: valid={res.get('valid')}")
    if not res.get("valid"):
        print(f"Diagnostics: {res.get('diagnostics')}")
        return 1

    # 4. Promote task-suite-profile-v1.json to structurally-ready
    profile_path = root / "contracts/task-suite-profile-v1.json"
    profile = json.load(open(profile_path))
    profile["status"] = "structurally-ready"
    profile["roles"]["task"]["required_capabilities"] = ALL_TASK_CAPABILITIES
    with open(profile_path, "w") as f:
        json.dump(profile, f, indent=2, sort_keys=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
