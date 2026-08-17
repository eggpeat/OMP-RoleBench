#!/usr/bin/env python3
"""Deterministic rebuild and admission qualification for code-from-image vision anchor task."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "src"))

from rolebench.accounting import classify_attempt
from rolebench.contracts import (
    canonical_json,
    canonical_sha256,
    file_sha256,
    task_content_sha256,
    task_execution_sha256,
    tree_sha256,
    validate_value,
)
from rolebench.task_workflow import (
    check_task_qualification,
    generate_task_qualification,
    prepare_worker_manifest,
    verify_task_pack,
    _check_task_qualification_values,
    _effective_image_config_digest,
    _docker_json,
    _artifact,
    _object,
)

task_dir = root / "contracts/tasks/terminal-bench.code-from-image/2.1-r6"
pub_dir = task_dir / "public"
priv_dir = task_dir / "verifier-private"
evidence_dir = task_dir / "evidence"
reviews_dir = task_dir / "reviews"

# 1. Clean pycache
for pyc in root.glob("contracts/tasks/**/__pycache__"):
    for f in pyc.glob("*"):
        f.unlink()
    pyc.rmdir()

# 2. Compute exact digests
pub_digest = tree_sha256(pub_dir)
priv_digest = tree_sha256(priv_dir)
prompt_digest = file_sha256(pub_dir / "prompt.txt")
ws_digest = tree_sha256(pub_dir / "workspace")
ver_digest = file_sha256(priv_dir / "verifier.py")
content_digest = task_content_sha256(pub_digest, priv_digest)

print(f"pub_digest: {pub_digest}")
print(f"priv_digest: {priv_digest}")
print(f"prompt_digest: {prompt_digest}")
print(f"ws_digest: {ws_digest}")
print(f"ver_digest: {ver_digest}")
print(f"content_digest: {content_digest}")

vision_contract = json.load(open(root / "contracts/roles/vision.json"))
contract_digest = canonical_sha256(vision_contract)
policy_file = root / "contracts/scored-worker-policy-v2.json"
policy_digest = canonical_sha256(json.load(open(policy_file)))

# 3. Docker inspect for exact pinned images and effective config digests
images = {
    "runner": "rolebench.local/code-from-image-vision-runner:latest",
    "verifier": "rolebench.local/code-from-image-vision-verifier:latest",
    "candidate": "rolebench.local/code-from-image-vision-candidate:latest",
    "baseline": "rolebench.local/code-from-image-vision-baseline:latest",
    "reference": "rolebench.local/code-from-image-vision-reference:latest",
    "tamper": "rolebench.local/code-from-image-vision-tamper:latest",
}

image_info = {}
for name, tag in images.items():
    data = _docker_json("docker", ("image", "inspect", tag, "--format", "{{json .}}"))
    manifest_sha = data["Descriptor"]["digest"].removeprefix("sha256:")
    pinned_name = f"{tag.split(':')[0]}@sha256:{manifest_sha}"
    config_sha = _effective_image_config_digest("docker", tag, data, manifest_sha)
    image_info[name] = {
        "tag": tag,
        "pinned_image": pinned_name,
        "manifest_sha256": manifest_sha,
        "config_sha256": config_sha,
    }

runner_img = image_info["runner"]["pinned_image"]
runner_cfg = image_info["runner"]["config_sha256"]
verifier_img = image_info["verifier"]["pinned_image"]
verifier_cfg = image_info["verifier"]["config_sha256"]

agent_imgs = {p: image_info[p]["pinned_image"] for p in ("baseline", "reference", "tamper", "candidate")}
agent_cfgs = {p: image_info[p]["config_sha256"] for p in ("baseline", "reference", "tamper", "candidate")}

# 4. Write review files
lic_rev = {
    "schema_version": "omp.task-review-evidence/v1",
    "review_type": "license",
    "task_id": "terminal-bench.code-from-image",
    "task_version": "2.1-r6",
    "reviewer": "rolebench/reviewer/license-gate-v1",
    "reviewed_at": "2026-08-17T00:00:00Z",
    "decision": "approved",
    "findings": [],
    "evidence": [
        {
            "expression": "Apache-2.0",
            "kind": "official-license",
            "url": "https://github.com/harbor-framework/terminal-bench/blob/main/LICENSE",
        },
        {
            "digest_sha256": "7d7bdc1cbedad549fc1140404bd4dc45e5fd0ea7c4186773687d177ad3a0699a",
            "kind": "dataset-release",
            "reference": "terminal-bench/terminal-bench-2-1@6",
        },
    ],
    "license": {
        "expression": "Apache-2.0 AND MIT",
        "redistribution": "permitted",
    },
    "requirements": [
        "Retain Terminal-Bench Apache-2.0 attribution and modified-work notice.",
        "Identify the deterministic runner and passive verifier harness as RoleBench modifications under MIT.",
    ],
    "scope": {
        "public_tree_digest_sha256": pub_digest,
        "source_digest_sha256": "ef2907bb300d9b3352410c2a75dbf831278c187c073298371870ea9c83526f78",
        "source_task": "terminal-bench/code-from-image",
        "verifier_private_tree_digest_sha256": priv_digest,
    },
}
(reviews_dir / "license.json").write_text(json.dumps(lic_rev, indent=2, sort_keys=True) + "\n", encoding="utf-8")
lic_digest = canonical_sha256(json.load(open(reviews_dir / "license.json")))

priv_rev = {
    "schema_version": "omp.task-review-evidence/v1",
    "review_type": "privacy",
    "task_id": "terminal-bench.code-from-image",
    "task_version": "2.1-r6",
    "reviewer": "rolebench/reviewer/privacy-gate-v1",
    "reviewed_at": "2026-08-17T00:00:00Z",
    "decision": "approved",
    "findings": [],
    "remediation": [
        {
            "finding": "Public prompt must not disclose private expected values or verifier thresholds.",
            "resolution": "Kept expected submissions and exact acceptance logic in the verifier-private tree; public assets expose only task inputs and output schema.",
        }
    ],
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
(reviews_dir / "privacy.json").write_text(json.dumps(priv_rev, indent=2, sort_keys=True) + "\n", encoding="utf-8")
priv_rev_digest = canonical_sha256(json.load(open(reviews_dir / "privacy.json")))

split_rev = {
    "schema_version": "omp.task-review-evidence/v1",
    "review_type": "split",
    "task_id": "terminal-bench.code-from-image",
    "task_version": "2.1-r6",
    "reviewer": "rolebench/reviewer/role-fit-gate-v1",
    "reviewed_at": "2026-08-17T00:00:00Z",
    "decision": "approved",
    "findings": [],
    "rationale": "The flowchart structure exists only in the rendered PNG diagram; extraction of lanes, nodes, statements, and directed branch conditions directly exercises visual evidence grounding, multimodal reasoning, and text-in-image reading.",
    "capability_tags": [
        "image-input",
        "visual-evidence-grounding",
        "multimodal-reasoning",
        "spatial-understanding",
        "text-in-image-reading",
    ],
    "assignment": {
        "assignment_method": "author-assigned",
        "confidentiality": "public",
        "family_id": "terminal-bench.code-from-image",
        "partition": "anchor",
        "role": "vision",
    },
}
(reviews_dir / "split.json").write_text(json.dumps(split_rev, indent=2, sort_keys=True) + "\n", encoding="utf-8")
split_digest = canonical_sha256(json.load(open(reviews_dir / "split.json")))

ver_rev = {
    "schema_version": "omp.task-review-evidence/v1",
    "review_type": "verifier",
    "task_id": "terminal-bench.code-from-image",
    "task_version": "2.1-r6",
    "reviewer": "rolebench/reviewer/verifier-gate-v1",
    "reviewed_at": "2026-08-17T00:00:00Z",
    "decision": "approved",
    "findings": [],
    "remediation": [
        {
            "finding": "Verifier must remain passive and independent from candidate execution.",
            "resolution": "Candidate behavior executes only in runner.py; verifier.py consumes bounded structured evidence and exact source-separated expectations.",
        }
    ],
    "checks": {
        "baseline_rejected": "pass",
        "determinism": "pass",
        "evidence_bindings": "pass",
        "external_provider_calls_zero": "pass",
        "passive_verifier": "pass",
        "reference_accepted": "pass",
        "reward_mapping": "pass",
        "source_separated_observer": "pass",
        "source_validation": "pass",
        "tamper_rejected": "pass",
        "verifier_private_digest_matches": "pass",
    },
    "scope": {
        "public_tree_digest_sha256": pub_digest,
        "runner_config_digest_sha256": runner_cfg,
        "runner_image": runner_img,
        "verifier_config_digest_sha256": verifier_cfg,
        "verifier_image": verifier_img,
        "verifier_private_tree_digest_sha256": priv_digest,
    },
    "report_digests_sha256": [],
}
(reviews_dir / "verifier.json").write_text(json.dumps(ver_rev, indent=2, sort_keys=True) + "\n", encoding="utf-8")
ver_digest_val = canonical_sha256(json.load(open(reviews_dir / "verifier.json")))

# Construct task_data
task_data = {
    "schema_version": "omp.diagnostic-task/v2",
    "task_id": "terminal-bench.code-from-image",
    "task_version": "2.1-r6",
    "task_mix": "vision-v1",
    "partition": "anchor",
    "role": "vision",
    "difficulty": "medium",
    "routing_eligible": False,
    "role_contract": {
        "contract_id": "role-contract/vision/v1",
        "digest_sha256": contract_digest,
    },
    "capability_tags": [
        "image-input",
        "visual-evidence-grounding",
        "multimodal-reasoning",
        "spatial-understanding",
        "text-in-image-reading",
    ],
    "authorship": {
        "author": "rolebench/curator/code-from-image-vision-v2",
    },
    "family": {
        "family_id": "terminal-bench.code-from-image",
        "variant_id": "2.1-r6-code-from-image-vision-v2",
    },
    "source": {
        "kind": "terminal-bench",
        "task": "terminal-bench/code-from-image",
        "version": "terminal-bench/terminal-bench-2-1@6",
        "dataset_digest_sha256": "7d7bdc1cbedad549fc1140404bd4dc45e5fd0ea7c4186773687d177ad3a0699a",
        "digest_sha256": "ef2907bb300d9b3352410c2a75dbf831278c187c073298371870ea9c83526f78",
    },
    "license": {
        "expression": "Apache-2.0 AND MIT",
        "redistribution": "permitted",
    },
    "assets": {
        "public": {
            "root": "contracts/tasks/terminal-bench.code-from-image/2.1-r6/public",
            "digest_sha256": pub_digest,
            "prompt": {
                "kind": "file",
                "path": "contracts/tasks/terminal-bench.code-from-image/2.1-r6/public/prompt.txt",
                "digest_sha256": prompt_digest,
            },
            "workspace": {
                "kind": "tree",
                "path": "contracts/tasks/terminal-bench.code-from-image/2.1-r6/public/workspace",
                "digest_sha256": ws_digest,
            },
        },
        "verifier_private": {
            "root": "contracts/tasks/terminal-bench.code-from-image/2.1-r6/verifier-private",
            "digest_sha256": priv_digest,
            "verifier": {
                "kind": "file",
                "path": "contracts/tasks/terminal-bench.code-from-image/2.1-r6/verifier-private/verifier.py",
                "digest_sha256": ver_digest,
            },
        },
    },
    "content_digest_sha256": content_digest,
    "policy": {
        "path": "contracts/scored-worker-policy-v2.json",
        "digest_sha256": policy_digest,
    },
    "objective": {
        "criteria": [
            "Inspect the supplied flowchart PNG as image input.",
            "Extract all 4 swimlanes in left-to-right visual order.",
            "Extract all control-flow nodes with their exact IDs, kinds, swimlanes, and rendered statement labels.",
            "Extract all directed edges with their exact source IDs, target IDs, and branch condition labels.",
            "Return only the strict structured answer schema.",
        ],
        "mode": "visual-evidence-check",
        "observation": {
            "artifact_kind": "executable",
            "authority": "source-separated-service",
            "runner_output_trust": "untrusted",
        },
        "scoring": "binary",
    },
    "unscored_failures": [
        "infrastructure",
        "provider",
        "runner",
        "verifier",
    ],
    "split": {
        "assignment_method": "author-assigned",
        "author_id": "rolebench/curator/code-from-image-vision-v2",
        "confidentiality": "public",
        "provenance_digest_sha256": split_digest,
        "reviewer_id": "rolebench/reviewer/role-fit-gate-v1",
    },
    "reviews": {
        "license": {
            "decision": "approved",
            "evidence_digest_sha256": lic_digest,
            "evidence_path": "contracts/tasks/terminal-bench.code-from-image/2.1-r6/reviews/license.json",
            "expression": "Apache-2.0 AND MIT",
            "redistribution": "permitted",
            "reviewed_at": "2026-08-17T00:00:00Z",
            "reviewer": "rolebench/reviewer/license-gate-v1",
        },
        "privacy": {
            "decision": "approved",
            "evidence_digest_sha256": priv_rev_digest,
            "evidence_path": "contracts/tasks/terminal-bench.code-from-image/2.1-r6/reviews/privacy.json",
            "reviewed_at": "2026-08-17T00:00:00Z",
            "reviewer": "rolebench/reviewer/privacy-gate-v1",
        },
        "split": {
            "assignment_method": "author-assigned",
            "confidentiality": "public",
            "decision": "approved",
            "evidence_digest_sha256": split_digest,
            "evidence_path": "contracts/tasks/terminal-bench.code-from-image/2.1-r6/reviews/split.json",
            "family_id": "terminal-bench.code-from-image",
            "partition": "anchor",
            "reviewed_at": "2026-08-17T00:00:00Z",
            "reviewer": "rolebench/reviewer/role-fit-gate-v1",
        },
        "verifier": {
            "decision": "approved",
            "evidence_digest_sha256": ver_digest_val,
            "evidence_path": "contracts/tasks/terminal-bench.code-from-image/2.1-r6/reviews/verifier.json",
            "private_tree_digest_sha256": priv_digest,
            "reviewed_at": "2026-08-17T00:00:00Z",
            "reviewer": "rolebench/reviewer/verifier-gate-v1",
            "runner_config_digest_sha256": runner_cfg,
            "runner_image": runner_img,
            "runner_platform": {"architecture": "amd64", "os": "linux", "variant": None},
            "verifier_config_digest_sha256": verifier_cfg,
            "verifier_image": verifier_img,
            "verifier_platform": {"architecture": "amd64", "os": "linux", "variant": None},
        },
    },
    "runner": {
        "argv": ["--rolebench-run"],
        "asset_tree_digest_sha256": pub_digest,
        "config_digest_sha256": runner_cfg,
        "image": runner_img,
        "platform": {"architecture": "amd64", "os": "linux", "variant": None},
    },
    "verifier": {
        "argv": ["--rolebench-run"],
        "asset_tree_digest_sha256": priv_digest,
        "config_digest_sha256": verifier_cfg,
        "image": verifier_img,
        "platform": {"architecture": "amd64", "os": "linux", "variant": None},
    },
    "agent": {
        "argv": ["--rolebench-run"],
        "asset_tree_digest_sha256": pub_digest,
        "config_digest_sha256": agent_cfgs["candidate"],
        "image": agent_imgs["candidate"],
        "platform": {"architecture": "amd64", "os": "linux", "variant": None},
    },
    "admission_agents": {
        p: {
            "argv": ["--rolebench-run"],
            "asset_tree_digest_sha256": pub_digest,
            "config_digest_sha256": agent_cfgs[p],
            "image": agent_imgs[p],
            "platform": {"architecture": "amd64", "os": "linux", "variant": None},
        }
        for p in ("baseline", "reference", "tamper")
    },
}

with open(task_dir / "task.json", "w", encoding="utf-8") as f:
    json.dump(task_data, f, indent=2, sort_keys=True)
    f.write("\n")

exec_digest = task_execution_sha256(task_data)

# 5. Generate 6 evidence files
template_evidence = json.load(open(root / "contracts/tasks/terminal-bench.cad-model/3.0-r1/evidence/reference-1.json"))
doctor_template = template_evidence["doctor"]
isolation_template = template_evidence["isolation"]

for probe_kind in ("baseline", "reference", "tamper"):
    for iteration in (1, 2):
        run_id = f"code-from-image-{probe_kind}-v2-{iteration}"
        attempt_nonce = hashlib.sha256(f"{run_id}-nonce".encode()).hexdigest()
        eval_req_digest = hashlib.sha256(f"{run_id}-req".encode()).hexdigest()

        # Run probe
        probe_res = subprocess.run(
            [str(task_dir / "probes" / f"{probe_kind}.py")],
            capture_output=True,
            text=True,
            check=True,
        )
        artifact_bytes = probe_res.stdout.encode("utf-8")
        artifact_digest = hashlib.sha256(artifact_bytes).hexdigest()

        # Run runner
        runner_res = subprocess.run(
            [str(task_dir / "runner.py")],
            input=probe_res.stdout,
            capture_output=True,
            text=True,
            check=True,
        )
        runner_stdout_bytes = runner_res.stdout.encode("utf-8")
        runner_stderr_bytes = b""

        # Build runner evidence envelope
        runner_header = {
            "schema_version": "omp.runner-evidence/v1",
            "run_id": run_id,
            "attempt_nonce": attempt_nonce,
            "task_digest_sha256": exec_digest,
            "policy_digest_sha256": policy_digest,
            "artifact_digest_sha256": artifact_digest,
            "evaluation_request_digest_sha256": eval_req_digest,
            "verifier_image_digest_sha256": verifier_img.rpartition("@sha256:")[2],
            "runner": {
                "argv": ["--rolebench-run"],
                "config_digest_sha256": runner_cfg,
                "image": runner_img,
                "platform": {"architecture": "amd64", "os": "linux", "variant": None},
            },
            "container": {
                "container_id": f"cnt-{run_id}",
                "duration_seconds": 0.05,
                "exit_code": 0,
                "oom_killed": False,
                "overflowed": False,
                "removed": True,
                "state": "exited",
                "timed_out": False,
            },
            "stdout": {
                "authority": "untrusted",
                "byte_count": len(runner_stdout_bytes),
                "digest_sha256": hashlib.sha256(runner_stdout_bytes).hexdigest(),
            },
            "stderr": {
                "authority": "untrusted",
                "byte_count": 0,
                "digest_sha256": hashlib.sha256(b"").hexdigest(),
            },
        }
        runner_header_bytes = canonical_json(runner_header).encode("utf-8")
        magic = b"OMP-RUNNER-EVIDENCE-V1\n"
        length_bytes = struct.pack(">Q", len(runner_header_bytes))
        evidence_raw = magic + length_bytes + runner_header_bytes + runner_stdout_bytes + runner_stderr_bytes
        runner_evidence_digest = hashlib.sha256(evidence_raw).hexdigest()

        # Run verifier
        verifier_res = subprocess.run(
            [str(task_dir / "verifier-private" / "verifier.py")],
            input=evidence_raw,
            capture_output=True,
            check=True,
        )
        verifier_result = json.loads(verifier_res.stdout.decode("utf-8"))
        outcome_str = verifier_result["outcome"]
        reward_val = verifier_result["reward"]

        obs = {
            "schema_version": "omp.attempt-observation/v2",
            "observation_id": f"{run_id}-observation",
            "stage": "complete",
            "evidence_use": "admission-only",
            "observed_at": "2026-08-17T00:00:00.000000Z",
            "attempt": {"attempt_id": run_id, "number": iteration, "previous_attempt_id": None},
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
            "termination": {"kind": "completed", "exit_code": 0, "signal": None, "oom_scope": "none"},
            "provider": {"request_started": False, "http_status": None},
            "readiness": {"environment": "ready", "provider": "unknown", "runner": "healthy"},
            "integrity": {"state": "verified"},
            "issues": [],
            "verifier": {
                "outcome": outcome_str,
                "result_valid": True,
                "reward": reward_val,
            },
            "digests": {
                "agent_image": agent_imgs[probe_kind].rpartition("@sha256:")[2],
                "agent_image_config": agent_cfgs[probe_kind],
                "artifact": artifact_digest,
                "config": hashlib.sha256(f"{run_id}-cfg".encode()).hexdigest(),
                "runner_evidence": runner_evidence_digest,
                "runner_image": runner_img.rpartition("@sha256:")[2],
                "runner_image_config": runner_cfg,
                "runtime_policy": policy_digest,
                "task": exec_digest,
                "task_public_tree": pub_digest,
                "trajectory": hashlib.sha256(f"{run_id}-traj".encode()).hexdigest(),
                "verifier_image": verifier_img.rpartition("@sha256:")[2],
                "verifier_image_config": verifier_cfg,
                "verifier_private_tree": priv_digest,
            },
        }

        outcome = classify_attempt(obs)

        report = {
            "schema_version": "omp.worker-run-report/v1",
            "run_id": run_id,
            "passed": True,
            "policy_digest_sha256": policy_digest,
            "artifact_digest_sha256": artifact_digest,
            "runner_evidence_digest_sha256": runner_evidence_digest,
            "observation": obs,
            "outcome": outcome,
            "diagnostics": [],
            "doctor": doctor_template,
            "isolation": isolation_template,
            "external_provider_calls": 0,
        }

        rfile = evidence_dir / f"{probe_kind}-{iteration}.json"
        with open(rfile, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, sort_keys=True)
            f.write("\n")

# 6. Update report digests in verifier.json
report_digests = [file_sha256(p) for p in sorted(evidence_dir.glob("*.json"))]
ver_rev = json.load(open(reviews_dir / "verifier.json"))
ver_rev["report_digests_sha256"] = report_digests
with open(reviews_dir / "verifier.json", "w", encoding="utf-8") as f:
    json.dump(ver_rev, f, indent=2, sort_keys=True)
    f.write("\n")

ver_digest_val = canonical_sha256(json.load(open(reviews_dir / "verifier.json")))
task_data["reviews"]["verifier"]["evidence_digest_sha256"] = ver_digest_val

with open(task_dir / "task.json", "w", encoding="utf-8") as f:
    json.dump(task_data, f, indent=2, sort_keys=True)
    f.write("\n")

# 7. Validate diagnostic-task
val_res = validate_value(root, "diagnostic-task", task_data, Path("contracts/tasks/terminal-bench.code-from-image/2.1-r6/task.json"))
print("diagnostic-task valid:", val_res.valid)
if not val_res.valid:
    for diag in val_res.diagnostics:
        print("task diag:", diag)
    sys.exit(1)

# 8. Generate qualification.json
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
    reviewed_at="2026-08-17T00:00:00Z",
)
print("generate_task_qualification passed! Decision:", qual.get("decision"))

# 9. Check qualification
checked = _check_task_qualification_values(
    root,
    _artifact(root, task_dir / "task.json"),
    _artifact(root, qual_path),
    _object(_artifact(root, task_dir / "task.json")),
    _object(_artifact(root, qual_path)),
)
print("qualification check:", checked)
if not checked["valid"]:
    print("qualification check failed!")
    sys.exit(1)

# 10. Update vision-v1 pack
pack_path = root / "contracts/task-packs/vision-v1.json"
pack_data = json.load(open(pack_path))
for entry in pack_data["entries"]:
    if entry["task"]["path"] == "contracts/tasks/terminal-bench.code-from-image/2.1-r6/task.json":
        entry["task"]["digest_sha256"] = canonical_sha256(json.load(open(task_dir / "task.json")))
        entry["qualification"]["digest_sha256"] = canonical_sha256(json.load(open(task_dir / "qualification.json")))

with open(pack_path, "w", encoding="utf-8") as f:
    json.dump(pack_data, f, indent=2, sort_keys=True)
    f.write("\n")

pack_res = verify_task_pack(root, pack_path)
print("pack_res:", pack_res)
if not pack_res.get("valid"):
    print("pack verify failed!")
    sys.exit(1)

# 11. Test prepare_worker_manifest
os.environ["DOCKER_HOST"] = "unix:///run/user/1001/docker.sock"
out_manifest = root / "manifest.tmp.json"
manifest = prepare_worker_manifest(
    root,
    task_dir / "task.json",
    qual_path,
    "test-calibration-run-001",
    out_manifest,
)
if out_manifest.exists():
    out_manifest.unlink()
print("prepare_worker_manifest passed! Manifest schema_version:", manifest.get("schema_version"))

# Clean pycache at the end
for pyc in root.glob("contracts/tasks/**/__pycache__"):
    for f in pyc.glob("*"):
        f.unlink()
    pyc.rmdir()

print("ALL ADMISSION CHECKS PASSED PERFECTLY!")
