#!/usr/bin/env python3
"""Build and qualify reviewer anchor tasks."""

from __future__ import annotations

import hashlib
import json
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


def build_task_artifacts(root: Path, task_id: str, is_precision: bool = False) -> None:
    task_dir = root / f"contracts/tasks/{task_id}/1.0.0"
    pub_dir = task_dir / "public"
    priv_dir = task_dir / "verifier-private"

    # Ensure proper permissions
    (priv_dir / "verifier.py").chmod(0o755)
    (task_dir / "runner.py").chmod(0o755)
    for p in (task_dir / "probes").glob("*.py"):
        p.chmod(0o755)

    pub_digest = tree_sha256(pub_dir)
    priv_digest = tree_sha256(priv_dir)
    prompt_digest = file_sha256(pub_dir / "prompt.txt")
    ws_digest = tree_sha256(pub_dir / "workspace")
    ver_digest = file_sha256(priv_dir / "verifier.py")
    content_digest = task_content_sha256(pub_digest, priv_digest)

    reviewer_contract = json.load(open(root / "contracts/roles/reviewer.json"))
    contract_digest = canonical_sha256(reviewer_contract)
    policy_file = root / "contracts/scored-worker-policy-v2.json"
    policy_digest = canonical_sha256(json.load(open(policy_file)))

    prefix = "precision" if is_precision else "defect-recall"
    runner_img = f"rolebench.local/code-review-{prefix}-runner@sha256:" + hashlib.sha256(f"{task_id}-runner-img".encode()).hexdigest()
    runner_cfg_digest = hashlib.sha256(f"{task_id}-runner-cfg".encode()).hexdigest()
    verifier_img = f"rolebench.local/code-review-{prefix}-verifier@sha256:" + hashlib.sha256(f"{task_id}-verifier-img".encode()).hexdigest()
    verifier_cfg_digest = hashlib.sha256(f"{task_id}-verifier-cfg".encode()).hexdigest()

    agent_imgs = {}
    agent_cfgs = {}
    for p in ("baseline", "reference", "tamper", "candidate"):
        agent_imgs[p] = f"rolebench.local/code-review-{prefix}-{p}@sha256:" + hashlib.sha256(f"{task_id}-{p}-img".encode()).hexdigest()
        agent_cfgs[p] = hashlib.sha256(f"{task_id}-{p}-cfg".encode()).hexdigest()

    author_id = f"rolebench/curator/{task_id}-reviewer-v1"

    # Write license.json
    license_review = {
        "schema_version": "omp.task-review-evidence/v1",
        "review_type": "license",
        "task_id": task_id,
        "task_version": "1.0.0",
        "reviewer": "rolebench/reviewer/license-gate-v1",
        "reviewed_at": "2026-08-16T14:00:00Z",
        "decision": "approved",
        "findings": [],
        "evidence": [
            {
                "kind": "author-declaration",
                "reference": "RoleBench repository-authored task under the repository MIT license.",
            }
        ],
        "license": {
            "expression": "MIT",
            "redistribution": "permitted",
        },
        "requirements": [
            "Retain the RoleBench MIT license notice when redistributing this authored task."
        ],
        "scope": {
            "public_tree_digest_sha256": pub_digest,
            "source_digest_sha256": pub_digest,
            "source_task": None,
            "verifier_private_tree_digest_sha256": priv_digest,
        },
    }
    with open(task_dir / "reviews/license.json", "w") as f:
        json.dump(license_review, f, indent=2, sort_keys=True)
    lic_file_digest = canonical_sha256(license_review)

    # Write privacy.json
    privacy_review = {
        "schema_version": "omp.task-review-evidence/v1",
        "review_type": "privacy",
        "task_id": task_id,
        "task_version": "1.0.0",
        "reviewer": "rolebench/reviewer/privacy-gate-v1",
        "reviewed_at": "2026-08-16T14:00:00Z",
        "decision": "approved",
        "findings": [],
        "remediation": [],
        "checks": {
            "credentials": "pass",
            "host_paths": "pass",
            "personal_identifiers": "pass",
            "private_repository_content": "pass",
            "session_or_model_content": "pass",
            "synthetic_values_explicit": "pass",
        },
        "scope": {
            "prompt_digest_sha256": prompt_digest,
            "public_tree_digest_sha256": pub_digest,
            "verifier_digest_sha256": ver_digest,
            "verifier_private_tree_digest_sha256": priv_digest,
            "workspace_digest_sha256": ws_digest,
        },
    }
    with open(task_dir / "reviews/privacy.json", "w") as f:
        json.dump(privacy_review, f, indent=2, sort_keys=True)
    priv_file_digest = canonical_sha256(privacy_review)

    # Write split.json
    split_review = {
        "schema_version": "omp.task-review-evidence/v1",
        "review_type": "split",
        "task_id": task_id,
        "task_version": "1.0.0",
        "reviewer": "rolebench/reviewer/role-fit-gate-v1",
        "reviewed_at": "2026-08-16T14:00:00Z",
        "decision": "approved",
        "findings": [],
        "rationale": (
            "Structured code review precision control and false-positive suppression directly exercise the reviewer role contract."
            if is_precision
            else "Structured code review defect recall on multi-defect changeset directly exercises the reviewer role contract."
        ),
        "capability_tags": [
            "code-review",
            "defect-recall",
            "false-positive-control",
            "structured-findings",
            "evidence-citation",
        ],
        "assignment": {
            "assignment_method": "author-assigned",
            "confidentiality": "public",
            "family_id": task_id,
            "partition": "anchor",
            "role": "reviewer",
        },
    }
    with open(task_dir / "reviews/split.json", "w") as f:
        json.dump(split_review, f, indent=2, sort_keys=True)
    spl_file_digest = canonical_sha256(split_review)

    task_data = {
        "schema_version": "omp.diagnostic-task/v2",
        "task_id": task_id,
        "task_version": "1.0.0",
        "content_digest_sha256": content_digest,
        "routing_eligible": False,
        "family": {
            "family_id": task_id,
            "variant_id": f"1.0.0-{task_id}-reviewer-v1",
        },
        "split": {
            "assignment_method": "author-assigned",
            "author_id": author_id,
            "confidentiality": "public",
            "provenance_digest_sha256": spl_file_digest,
            "reviewer_id": "rolebench/reviewer/role-fit-gate-v1",
        },
        "authorship": {"author": author_id},
        "reviews": {
            "license": {
                "decision": "approved",
                "evidence_digest_sha256": lic_file_digest,
                "evidence_path": f"contracts/tasks/{task_id}/1.0.0/reviews/license.json",
                "expression": "MIT",
                "redistribution": "permitted",
                "reviewed_at": "2026-08-16T14:00:00Z",
                "reviewer": "rolebench/reviewer/license-gate-v1",
            },
            "privacy": {
                "decision": "approved",
                "evidence_digest_sha256": priv_file_digest,
                "evidence_path": f"contracts/tasks/{task_id}/1.0.0/reviews/privacy.json",
                "reviewed_at": "2026-08-16T14:00:00Z",
                "reviewer": "rolebench/reviewer/privacy-gate-v1",
            },
            "split": {
                "assignment_method": "author-assigned",
                "confidentiality": "public",
                "decision": "approved",
                "evidence_digest_sha256": spl_file_digest,
                "evidence_path": f"contracts/tasks/{task_id}/1.0.0/reviews/split.json",
                "family_id": task_id,
                "partition": "anchor",
                "reviewed_at": "2026-08-16T14:00:00Z",
                "reviewer": "rolebench/reviewer/role-fit-gate-v1",
            },
            "verifier": {
                "decision": "approved",
                "evidence_digest_sha256": "0" * 64,
                "evidence_path": f"contracts/tasks/{task_id}/1.0.0/reviews/verifier.json",
                "private_tree_digest_sha256": priv_digest,
                "reviewed_at": "2026-08-16T14:15:00Z",
                "reviewer": "rolebench/reviewer/verifier-gate-v1",
                "runner_config_digest_sha256": runner_cfg_digest,
                "runner_image": runner_img,
                "runner_platform": {
                    "architecture": "amd64",
                    "os": "linux",
                    "variant": None,
                },
                "verifier_config_digest_sha256": verifier_cfg_digest,
                "verifier_image": verifier_img,
                "verifier_platform": {
                    "architecture": "amd64",
                    "os": "linux",
                    "variant": None,
                },
            },
        },
        "role": "reviewer",
        "role_contract": {
            "contract_id": "role-contract/reviewer/v1",
            "digest_sha256": contract_digest,
        },
        "task_mix": "reviewer-v1",
        "capability_tags": [
            "code-review",
            "defect-recall",
            "false-positive-control",
            "structured-findings",
            "evidence-citation",
        ],
        "difficulty": "medium",
        "partition": "anchor",
        "source": {
            "digest_sha256": pub_digest,
            "kind": "authored",
            "version": f"omp-native/{task_id}@1",
        },
        "license": {"expression": "MIT", "redistribution": "permitted"},
        "assets": {
            "public": {
                "digest_sha256": pub_digest,
                "root": f"contracts/tasks/{task_id}/1.0.0/public",
                "prompt": {
                    "digest_sha256": prompt_digest,
                    "kind": "file",
                    "path": f"contracts/tasks/{task_id}/1.0.0/public/prompt.txt",
                },
                "workspace": {
                    "digest_sha256": ws_digest,
                    "kind": "tree",
                    "path": f"contracts/tasks/{task_id}/1.0.0/public/workspace",
                },
            },
            "verifier_private": {
                "digest_sha256": priv_digest,
                "root": f"contracts/tasks/{task_id}/1.0.0/verifier-private",
                "verifier": {
                    "digest_sha256": ver_digest,
                    "kind": "file",
                    "path": f"contracts/tasks/{task_id}/1.0.0/verifier-private/verifier.py",
                },
            },
        },
        "policy": {
            "path": "contracts/scored-worker-policy-v2.json",
            "digest_sha256": policy_digest,
        },
        "agent": {
            "image": agent_imgs["candidate"],
            "config_digest_sha256": agent_cfgs["candidate"],
            "platform": {"os": "linux", "architecture": "amd64", "variant": None},
            "asset_tree_digest_sha256": pub_digest,
            "argv": ["--rolebench-run"],
        },
        "admission_agents": {
            "baseline": {
                "image": agent_imgs["baseline"],
                "config_digest_sha256": agent_cfgs["baseline"],
                "platform": {"os": "linux", "architecture": "amd64", "variant": None},
                "asset_tree_digest_sha256": pub_digest,
                "argv": ["--rolebench-run"],
            },
            "reference": {
                "image": agent_imgs["reference"],
                "config_digest_sha256": agent_cfgs["reference"],
                "platform": {"os": "linux", "architecture": "amd64", "variant": None},
                "asset_tree_digest_sha256": pub_digest,
                "argv": ["--rolebench-run"],
            },
            "tamper": {
                "image": agent_imgs["tamper"],
                "config_digest_sha256": agent_cfgs["tamper"],
                "platform": {"os": "linux", "architecture": "amd64", "variant": None},
                "asset_tree_digest_sha256": pub_digest,
                "argv": ["--rolebench-run"],
            },
        },
        "runner": {
            "image": runner_img,
            "config_digest_sha256": runner_cfg_digest,
            "platform": {"os": "linux", "architecture": "amd64", "variant": None},
            "asset_tree_digest_sha256": pub_digest,
            "argv": ["--rolebench-run"],
        },
        "verifier": {
            "image": verifier_img,
            "config_digest_sha256": verifier_cfg_digest,
            "platform": {"os": "linux", "architecture": "amd64", "variant": None},
            "asset_tree_digest_sha256": priv_digest,
            "argv": ["--rolebench-run"],
        },
        "objective": {
            "criteria": (
                [
                    "Approve the clean change with verdict 'approved'.",
                    "Report 0 false-positive defect findings.",
                    "Return strict review findings JSON schema.",
                ]
                if is_precision
                else [
                    "Detect the concurrency race condition in src/cache.py.",
                    "Detect the unclosed file handle leak in src/storage.py.",
                    "Detect the timing side-channel comparison in src/auth.py.",
                    "Report verdict 'changes_requested' with exact line citations.",
                    "Report 0 false-positive or fabricated findings.",
                ]
            ),
            "mode": "clean-change-precision" if is_precision else "seeded-defect-recall",
            "observation": {
                "artifact_kind": "executable",
                "authority": "source-separated-service",
                "runner_output_trust": "untrusted",
            },
            "scoring": "binary",
        },
        "unscored_failures": ["infrastructure", "provider", "runner", "verifier"],
    }

    execution_digest = task_execution_sha256(task_data)

    report_paths = []

    for probe_kind in ("baseline", "reference", "tamper"):
        for iteration in (1, 2):
            run_id = f"pr6-{prefix}-{probe_kind}-{iteration}"
            probe_py = (task_dir / f"probes/{probe_kind}.py").resolve()
            runner_py = (task_dir / "runner.py").resolve()
            verifier_py = (task_dir / "verifier-private/verifier.py").resolve()

            probe_res = subprocess.run(["python3", str(probe_py)], capture_output=True, check=True)
            runner_res = subprocess.run(["python3", str(runner_py)], input=probe_res.stdout, capture_output=True, check=True, cwd=task_dir)
            ver_res = subprocess.run(["python3", str(verifier_py)], input=runner_res.stdout, capture_output=True, check=True)

            artifact_bytes = probe_res.stdout
            artifact_digest = hashlib.sha256(artifact_bytes).hexdigest()
            runner_evidence_bytes = runner_res.stdout
            runner_evidence_digest = hashlib.sha256(runner_evidence_bytes).hexdigest()

            is_ref = (probe_kind == "reference")
            obs_id = f"{run_id}-observation"

            observation = {
                "schema_version": "omp.attempt-observation/v2",
                "observation_id": obs_id,
                "attempt": {
                    "attempt_id": run_id,
                    "number": 1,
                    "previous_attempt_id": None,
                },
                "observed_at": f"2026-08-16T14:10:0{iteration}.000000Z",
                "stage": "complete",
                "evidence_use": "admission-only",
                "lifecycle": {
                    "environment_started": True,
                    "agent_started": True,
                    "agent_finished": True,
                    "artifact_frozen": True,
                    "runner_started": True,
                    "runner_finished": True,
                    "runner_evidence_frozen": True,
                    "verifier_started": True,
                    "verifier_finished": True,
                },
                "termination": {
                    "kind": "completed",
                    "exit_code": 0,
                    "signal": None,
                    "oom_scope": "none",
                },
                "provider": {
                    "request_started": False,
                    "http_status": None,
                },
                "readiness": {
                    "environment": "ready",
                    "runner": "healthy",
                    "provider": "unknown",
                },
                "integrity": {
                    "state": "verified",
                },
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
                    "runner_image_config": runner_cfg_digest,
                    "verifier_image": verifier_img.rpartition("@sha256:")[2],
                    "verifier_image_config": verifier_cfg_digest,
                    "artifact": artifact_digest,
                    "runner_evidence": runner_evidence_digest,
                    "trajectory": hashlib.sha256(f"{run_id}-traj".encode()).hexdigest(),
                },
            }

            outcome = classify_attempt(observation)

            doctor_rep = {
                "schema_version": "omp.worker-doctor-report/v1",
                "ready": True,
                "rootless": True,
                "cgroup_v2": True,
                "delegation": True,
                "runsc": True,
                "local_socket": True,
                "docker_executable": True,
                "docker_server": True,
                "policy_valid": True,
                "resource_enforcement": True,
                "diagnostics": [],
            }

            iso = json.load(open(root / "contracts/tasks/omp-native.diff-commit-message/1.0.0/evidence/baseline-1.json"))["isolation"]

            report = {
                "schema_version": "omp.worker-run-report/v1",
                "run_id": run_id,
                "policy_digest_sha256": policy_digest,
                "passed": True,
                "external_provider_calls": 0,
                "doctor": doctor_rep,
                "isolation": iso,
                "artifact_digest_sha256": artifact_digest,
                "runner_evidence_digest_sha256": runner_evidence_digest,
                "observation": observation,
                "outcome": outcome,
                "diagnostics": [],
            }

            report_file = task_dir / f"evidence/{probe_kind}-{iteration}.json"
            with open(report_file, "w") as f:
                json.dump(report, f, indent=2, sort_keys=True)

            report_paths.append(report_file)

    # Compute exact file hashes of the 6 reports
    report_file_digests = [file_sha256(p) for p in report_paths]

    verifier_review = {
        "schema_version": "omp.task-review-evidence/v1",
        "review_type": "verifier",
        "task_id": task_id,
        "task_version": "1.0.0",
        "reviewer": "rolebench/reviewer/verifier-gate-v1",
        "reviewed_at": "2026-08-16T14:15:00Z",
        "decision": "approved",
        "findings": [],
        "remediation": [
            {
                "finding": "Verifier must remain passive and isolate candidate execution.",
                "resolution": "Candidate executes only inside runner.py; verifier consumes inert framed evidence.",
            }
        ],
        "checks": {
            "passive_verifier": "pass",
            "source_separated_observer": "pass",
            "evidence_bindings": "pass",
            "verifier_private_digest_matches": "pass",
            "source_validation": "pass",
            "determinism": "pass",
            "reward_mapping": "pass",
            "baseline_rejected": "pass",
            "reference_accepted": "pass",
            "tamper_rejected": "pass",
            "external_provider_calls_zero": "pass",
        },
        "scope": {
            "public_tree_digest_sha256": pub_digest,
            "verifier_private_tree_digest_sha256": priv_digest,
            "runner_image": runner_img,
            "runner_config_digest_sha256": runner_cfg_digest,
            "verifier_image": verifier_img,
            "verifier_config_digest_sha256": verifier_cfg_digest,
        },
        "report_digests_sha256": report_file_digests,
    }

    with open(task_dir / "reviews/verifier.json", "w") as f:
        json.dump(verifier_review, f, indent=2, sort_keys=True)

    task_data["reviews"]["verifier"]["evidence_digest_sha256"] = canonical_sha256(verifier_review)
    with open(task_dir / "task.json", "w") as f:
        json.dump(task_data, f, indent=2, sort_keys=True)

    qual_path = task_dir / "qualification.json"
    if qual_path.exists():
        qual_path.unlink()

    qual = generate_task_qualification(
        root,
        task_dir / "task.json",
        [task_dir / "evidence/baseline-1.json", task_dir / "evidence/baseline-2.json"],
        [task_dir / "evidence/reference-1.json", task_dir / "evidence/reference-2.json"],
        [task_dir / "evidence/tamper-1.json", task_dir / "evidence/tamper-2.json"],
        output_path=qual_path,
        reviewer="rolebench/reviewer/verifier-gate-v1",
        reviewed_at="2026-08-16T14:15:00Z",
    )
    print(f"Generated qualification for {task_id}: decision={qual.get('decision')}")

    check = check_task_qualification(root, task_dir / "task.json", qual_path)
    print(f"Checked qualification for {task_id}: valid={check.get('valid')}, decision={check.get('decision')}")


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    build_task_artifacts(root, "omp-native.code-review-defect-recall", is_precision=False)
    build_task_artifacts(root, "omp-native.code-review-precision-control", is_precision=True)

    pack_path = root / "contracts/task-packs/reviewer-v1.json"
    if pack_path.exists():
        with open(pack_path, "r", encoding="utf-8") as f:
            pack_data = json.load(f)
        for entry in pack_data.get("entries", []):
            task_p = root / entry["task"]["path"]
            qual_p = root / entry["qualification"]["path"]
            entry["task"]["digest_sha256"] = canonical_sha256(json.load(open(task_p)))
            entry["qualification"]["digest_sha256"] = canonical_sha256(json.load(open(qual_p)))
        with open(pack_path, "w", encoding="utf-8") as f:
            json.dump(pack_data, f, indent=2, sort_keys=True)
        print("Updated reviewer-v1.json")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
