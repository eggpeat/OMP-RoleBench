from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import runpy
import re
import shutil
import subprocess
import tempfile
import unittest

from jsonschema import Draft202012Validator

from rolebench.contracts import (
    ContractError,
    canonical_digest,
    canonical_sha256,
    file_sha256,
    load_repository,
    task_content_sha256,
    task_execution_sha256,
    tree_sha256,
    validate_repository,
    validate_value,
)


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_RUNNER = "7" * 64
SHA_RUNNER_CONFIG = "8" * 64
TB_DATASET_DIGEST = "7d7bdc1cbedad549fc1140404bd4dc45e5fd0ea7c4186773687d177ad3a0699a"
TB_TASK_DIGEST = "a3d048d351136e48070696cda8bb79660dfd74db1fea3b6da88559f0332699c1"
IMAGE_A = f"example.invalid/agent@sha256:{SHA_A}"
IMAGE_RUNNER = f"example.invalid/runner@sha256:{SHA_RUNNER}"
IMAGE_B = f"example.invalid/verifier@sha256:{SHA_B}"
IMAGE_BASELINE = "example.invalid/baseline@sha256:" + "4" * 64
IMAGE_REFERENCE = "example.invalid/reference@sha256:" + "5" * 64
IMAGE_TAMPER = "example.invalid/tamper@sha256:" + "6" * 64


class TaskContractFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(PRODUCT_ROOT / "contracts", self.root / "contracts")

    def write_json(self, relative: str, value: dict[str, object]) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def messages(self, result: object) -> str:
        diagnostics = getattr(result, "diagnostics")
        return "\n".join(f"{item.file}:{item.json_path}: {item.message}" for item in diagnostics)

    def bind_review_evidence(
        self,
        task: dict[str, object],
        review_name: str,
        evidence: dict[str, object],
    ) -> None:
        review_path = (
            f"contracts/tasks/example/reviews/{review_name}.json"
        )
        self.write_json(review_path, evidence)
        reviews = task["reviews"]
        assert isinstance(reviews, dict)
        review = reviews[review_name]
        assert isinstance(review, dict)
        evidence_digest = canonical_sha256(evidence)
        review["evidence_path"] = review_path
        review["evidence_digest_sha256"] = evidence_digest
        if review_name == "split":
            split = task["split"]
            assert isinstance(split, dict)
            split["provenance_digest_sha256"] = evidence_digest

    def refresh_license_review_evidence(
        self,
        task: dict[str, object],
    ) -> None:
        path = self.root / "contracts/tasks/example/reviews/license.json"
        evidence = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(evidence, dict)
        scope = evidence["scope"]
        source = task["source"]
        assert isinstance(scope, dict)
        assert isinstance(source, dict)
        scope["source_task"] = source.get("task")
        scope["source_digest_sha256"] = source["digest_sha256"]
        self.bind_review_evidence(task, "license", evidence)

    def refresh_split_review_evidence(
        self,
        task: dict[str, object],
    ) -> None:
        path = self.root / "contracts/tasks/example/reviews/split.json"
        evidence = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(evidence, dict)
        evidence["capability_tags"] = task["capability_tags"]
        self.bind_review_evidence(task, "split", evidence)

    def make_task_and_qualification(self) -> tuple[dict[str, object], dict[str, object]]:
        public_root = self.root / "contracts/tasks/example/public"
        workspace = public_root / "workspace"
        workspace.mkdir(parents=True)
        prompt = public_root / "prompt.txt"
        prompt.write_text("repair the example\n", encoding="utf-8")
        (workspace / "input.txt").write_text("broken\n", encoding="utf-8")
        private_root = self.root / "contracts/tasks/example/private"
        private_root.mkdir(parents=True)
        verifier_file = private_root / "verify.py"
        verifier_file.write_text("raise SystemExit(0)\n", encoding="utf-8")
        baseline = self.root / "contracts/tasks/example/evidence/baseline-1.json"
        baseline_repeat = self.root / "contracts/tasks/example/evidence/baseline-2.json"
        reference = self.root / "contracts/tasks/example/evidence/reference-1.json"
        reference_repeat = self.root / "contracts/tasks/example/evidence/reference-2.json"
        tamper = self.root / "contracts/tasks/example/evidence/tamper-1.json"
        tamper_repeat = self.root / "contracts/tasks/example/evidence/tamper-2.json"
        baseline.parent.mkdir(parents=True)
        baseline.write_text('{"exit_code":1,"trial":1}\n', encoding="utf-8")
        baseline_repeat.write_text('{"exit_code":1,"trial":2}\n', encoding="utf-8")
        reference.write_text('{"exit_code":0,"trial":1}\n', encoding="utf-8")
        reference_repeat.write_text('{"exit_code":0,"trial":2}\n', encoding="utf-8")
        tamper.write_text('{"tamper_blocked":true,"trial":1}\n', encoding="utf-8")
        tamper_repeat.write_text('{"tamper_blocked":true,"trial":2}\n', encoding="utf-8")

        contract = json.loads((self.root / "contracts/roles/task.json").read_text(encoding="utf-8"))
        policy = json.loads((self.root / "contracts/scored-worker-policy-v2.json").read_text(encoding="utf-8"))
        public_digest = tree_sha256(public_root)
        private_digest = tree_sha256(private_root)
        platform = {"os": "linux", "architecture": "amd64", "variant": None}
        task: dict[str, object] = {
            "schema_version": "omp.diagnostic-task/v2",
            "task_id": "example/task",
            "task_version": "1.0.0",
            "content_digest_sha256": task_content_sha256(public_digest, private_digest),
            "routing_eligible": False,
            "family": {"family_id": "example-family", "variant_id": "repair-1"},
            "split": {
                "assignment_method": "independent-review",
                "author_id": "author-1",
                "reviewer_id": "split-reviewer-1",
                "provenance_digest_sha256": SHA_A,
                "confidentiality": "public",
            },
            "authorship": {"author": "author-1"},
            "reviews": {
                "privacy": {
                    "decision": "approved", "reviewer": "privacy-reviewer-1",
                    "reviewed_at": "2026-08-14T00:00:00Z", "evidence_digest_sha256": SHA_A,
                },
                "license": {
                    "decision": "approved", "reviewer": "license-reviewer-1",
                    "reviewed_at": "2026-08-14T00:00:00Z", "evidence_digest_sha256": SHA_B,
                    "expression": "MIT", "redistribution": "permitted",
                },
                "verifier": {
                    "decision": "approved", "reviewer": "verifier-reviewer-1",
                    "reviewed_at": "2026-08-14T00:00:00Z", "evidence_digest_sha256": SHA_C,
                    "runner_image": IMAGE_RUNNER, "runner_config_digest_sha256": SHA_RUNNER_CONFIG, "runner_platform": platform,
                    "verifier_image": IMAGE_B, "verifier_config_digest_sha256": SHA_B, "verifier_platform": platform,
                    "private_tree_digest_sha256": private_digest,
                },
                "split": {
                    "decision": "approved", "reviewer": "split-reviewer-1",
                    "reviewed_at": "2026-08-14T00:00:00Z", "evidence_digest_sha256": SHA_A,
                    "family_id": "example-family", "partition": "anchor",
                    "assignment_method": "independent-review", "confidentiality": "public",
                },
            },
            "role": "task",
            "role_contract": {
                "contract_id": contract["contract_id"],
                "digest_sha256": canonical_sha256(contract),
            },
            "task_mix": contract["task_mix"],
            "capability_tags": contract["required_capabilities"],
            "difficulty": "hard",
            "partition": "anchor",
            "source": {"kind": "authored", "version": "1", "digest_sha256": SHA_B},
            "license": {"expression": "MIT", "redistribution": "permitted"},
            "assets": {
                "public": {
                    "root": "contracts/tasks/example/public",
                    "digest_sha256": public_digest,
                    "prompt": {
                        "path": "contracts/tasks/example/public/prompt.txt",
                        "kind": "file",
                        "digest_sha256": file_sha256(prompt),
                    },
                    "workspace": {
                        "path": "contracts/tasks/example/public/workspace",
                        "kind": "tree",
                        "digest_sha256": tree_sha256(workspace),
                    },
                },
                "verifier_private": {
                    "root": "contracts/tasks/example/private",
                    "digest_sha256": private_digest,
                    "verifier": {
                        "path": "contracts/tasks/example/private/verify.py",
                        "kind": "file",
                        "digest_sha256": file_sha256(verifier_file),
                    },
                },
            },
            "policy": {
                "path": "contracts/scored-worker-policy-v2.json",
                "digest_sha256": canonical_sha256(policy),
            },
            "agent": {
                "image": IMAGE_A,
                "config_digest_sha256": SHA_A,
                "platform": platform,
                "asset_tree_digest_sha256": public_digest,
                "argv": ["/agent"],
            },
            "admission_agents": {
                "baseline": {
                    "image": IMAGE_BASELINE,
                    "config_digest_sha256": "1" * 64,
                    "platform": platform,
                    "asset_tree_digest_sha256": public_digest,
                    "argv": ["/baseline"],
                },
                "reference": {
                    "image": IMAGE_REFERENCE,
                    "config_digest_sha256": "2" * 64,
                    "platform": platform,
                    "asset_tree_digest_sha256": public_digest,
                    "argv": ["/reference"],
                },
                "tamper": {
                    "image": IMAGE_TAMPER,
                    "config_digest_sha256": "3" * 64,
                    "platform": platform,
                    "asset_tree_digest_sha256": public_digest,
                    "argv": ["/tamper"],
                },
            },
            "runner": {
                "image": IMAGE_RUNNER,
                "config_digest_sha256": SHA_RUNNER_CONFIG,
                "platform": platform,
                "asset_tree_digest_sha256": public_digest,
                "argv": ["/run"],
            },
            "verifier": {
                "image": IMAGE_B,
                "config_digest_sha256": SHA_B,
                "platform": platform,
                "asset_tree_digest_sha256": private_digest,
                "argv": ["/verify"],
            },
            "objective": {
                "mode": "executable-task-verifier",
                "scoring": "binary",
                "criteria": ["exit-zero"],
                "observation": {
                    "artifact_kind": "data-only",
                    "authority": "host-process",
                    "runner_output_trust": "untrusted",
                },
            },
            "unscored_failures": ["infrastructure", "provider", "runner", "verifier"],
        }
        common_review = {
            "schema_version": "omp.task-review-evidence/v1",
            "task_id": task["task_id"],
            "task_version": task["task_version"],
            "decision": "approved",
            "reviewed_at": "2026-08-14T00:00:00Z",
            "findings": [],
        }
        review_evidence: dict[str, dict[str, object]] = {
            "privacy": {
                **common_review,
                "review_type": "privacy",
                "reviewer": "privacy-reviewer-1",
                "checks": {"privacy": "pass"},
                "scope": {
                    "prompt_digest_sha256": file_sha256(prompt),
                    "public_tree_digest_sha256": public_digest,
                    "workspace_digest_sha256": tree_sha256(workspace),
                    "verifier_private_tree_digest_sha256": private_digest,
                    "verifier_digest_sha256": file_sha256(verifier_file),
                },
            },
            "license": {
                **common_review,
                "review_type": "license",
                "reviewer": "license-reviewer-1",
                "evidence": [{}],
                "license": task["license"],
                "requirements": ["Preserve the declared task license."],
                "scope": {
                    "source_task": None,
                    "source_digest_sha256": SHA_B,
                    "public_tree_digest_sha256": public_digest,
                    "verifier_private_tree_digest_sha256": private_digest,
                },
            },
            "verifier": {
                **common_review,
                "review_type": "verifier",
                "reviewer": "verifier-reviewer-1",
                "checks": {"reference_accepted": "pass"},
                "scope": {
                    "public_tree_digest_sha256": public_digest,
                    "runner_image": IMAGE_RUNNER,
                    "runner_config_digest_sha256": SHA_RUNNER_CONFIG,
                    "verifier_image": IMAGE_B,
                    "verifier_config_digest_sha256": SHA_B,
                    "verifier_private_tree_digest_sha256": private_digest,
                },
                "report_digests_sha256": [
                    file_sha256(baseline),
                    file_sha256(baseline_repeat),
                    file_sha256(reference),
                    file_sha256(reference_repeat),
                    file_sha256(tamper),
                    file_sha256(tamper_repeat),
                ],
            },
            "split": {
                **common_review,
                "review_type": "split",
                "reviewer": "split-reviewer-1",
                "assignment": {
                    "family_id": "example-family",
                    "partition": "anchor",
                    "assignment_method": "independent-review",
                    "confidentiality": "public",
                    "role": "task",
                },
                "capability_tags": task["capability_tags"],
                "rationale": "The task directly exercises the task role contract.",
            },
        }
        for review_name, evidence in review_evidence.items():
            self.bind_review_evidence(task, review_name, evidence)
        self.write_json("contracts/tasks/example/task.json", task)
        task_digest = task_execution_sha256(task)
        qualification: dict[str, object] = {
            "schema_version": "omp.task-qualification/v2",
            "task_id": task["task_id"],
            "task_version": task["task_version"],
            "content_digest_sha256": task["content_digest_sha256"],
            "source_task": {"path": "contracts/tasks/example/task.json", "digest_sha256": canonical_sha256(task)},
            "reviews": deepcopy(task["reviews"]),
            "evaluation_provenance": {
                "reviewer_id": "verifier-reviewer-1",
                "runner_image": IMAGE_RUNNER,
                "runner_config_digest_sha256": SHA_RUNNER_CONFIG,
                "runner_platform": platform,
                "verifier_image": IMAGE_B,
                "verifier_config_digest_sha256": SHA_B,
                "verifier_platform": platform,
                "review_digest_sha256": canonical_sha256(
                    review_evidence["verifier"]
                ),
            },
            "observed_mapping": {
                "task_digest_sha256": task_digest,
                "public_tree_digest_sha256": public_digest,
                "verifier_private_tree_digest_sha256": private_digest,
                "agent_config_digest_sha256": SHA_A,
                "runner_config_digest_sha256": SHA_RUNNER_CONFIG,
                "verifier_config_digest_sha256": SHA_B,
            },
            "checks": {
                "privacy": "pass",
                "license": "pass",
                "baseline_fails": {
                    "result": "pass",
                    "command_digest_sha256": "1" * 64,
                    "evidence": [
                        {"path": "contracts/tasks/example/evidence/baseline-1.json", "digest_sha256": file_sha256(baseline)},
                        {"path": "contracts/tasks/example/evidence/baseline-2.json", "digest_sha256": file_sha256(baseline_repeat)},
                    ],
                },
                "reference_passes": {
                    "result": "pass",
                    "command_digest_sha256": "2" * 64,
                    "evidence": [
                        {"path": "contracts/tasks/example/evidence/reference-1.json", "digest_sha256": file_sha256(reference)},
                        {"path": "contracts/tasks/example/evidence/reference-2.json", "digest_sha256": file_sha256(reference_repeat)},
                    ],
                },
                "runner_isolation": "pass",
                "verifier_isolation": "pass",
                "observation_authority": "pass",
                "tamper_resistance": {
                    "result": "pass",
                    "command_digest_sha256": "3" * 64,
                    "evidence": [
                        {"path": "contracts/tasks/example/evidence/tamper-1.json", "digest_sha256": file_sha256(tamper)},
                        {"path": "contracts/tasks/example/evidence/tamper-2.json", "digest_sha256": file_sha256(tamper_repeat)},
                    ],
                },
                "determinism": "pass",
                "infrastructure_classification": "pass",
                "discrimination": "calibration-required",
            },
            "decision": "calibration-required",
        }
        self.write_json("contracts/tasks/example/qualification.json", qualification)
        return task, qualification


class CanonicalFixedPackTests(TaskContractFixture):
    def test_repository_and_fixed_packs_are_canonical(self) -> None:
        result = validate_repository(self.root)
        self.assertTrue(result.valid, self.messages(result))
        repository = load_repository(self.root)
        packs = dict(repository.task_packs)
        expected_entries = {
            "default": 2,
            "smol": 1,
            "slow": 2,
            "vision": 2,
            "plan": 1,
            "designer": 1,
            "commit": 1,
            "tiny": 1,
            "task": 1,
            "advisor": 2,
        }
        self.assertEqual(
            tuple(role for role, _ in repository.task_packs),
            tuple(expected_entries),
        )
        for role, entry_count in expected_entries.items():
            pack = packs[role]
            self.assertEqual(pack["role"], role)
            self.assertEqual(pack["status"], "calibration")
            self.assertIs(pack["routing_eligible"], False)
            self.assertEqual(len(pack["entries"]), entry_count)

    def test_mapped_pack_changes_canonical_repository_digest(self) -> None:
        before = canonical_digest(load_repository(self.root))
        pack_path = self.root / "contracts/task-packs/task-v1.json"
        pack = json.loads(pack_path.read_text(encoding="utf-8"))
        pack["pack_id"] = "task-v1-changed"
        self.write_json("contracts/task-packs/task-v1.json", pack)
        self.assertNotEqual(before, canonical_digest(load_repository(self.root)))

    def test_each_new_schema_is_draft_2020_12_valid(self) -> None:
        names = ("task-candidate", "diagnostic-task", "task-qualification", "task-pack", "experiment-ledger-entry", "verifier-result")
        for name in names:
            schema = json.loads((self.root / f"contracts/schemas/{name}.schema.json").read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)


class DigestSafetyTests(TaskContractFixture):
    def test_file_and_tree_hashes_reject_links_special_files_and_parent_paths(self) -> None:
        regular = self.root / "regular"
        regular.write_bytes(b"data")
        link = self.root / "link"
        link.symlink_to(regular)
        with self.assertRaises(ContractError):
            file_sha256(link)
        oversized = self.root / "oversized"
        oversized.write_bytes(b"")
        os.truncate(oversized, 64 * 1024 * 1024 + 1)
        with self.assertRaises(ContractError):
            file_sha256(oversized)
        with self.assertRaises(ContractError):
            file_sha256(Path("x" * 4097))
        tree = self.root / "tree"
        tree.mkdir()
        (tree / "link").symlink_to(regular)
        with self.assertRaises(ContractError):
            tree_sha256(tree)
        with self.assertRaises(ContractError):
            file_sha256(Path("safe/../escape"))
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        with self.assertRaises(ContractError):
            file_sha256(fifo)

    def test_tree_digest_is_deterministic_and_binds_executable_mode(self) -> None:
        tree = self.root / "tree"
        tree.mkdir()
        executable = tree / "run"
        executable.write_text("exit 0\n", encoding="utf-8")
        first = tree_sha256(tree)
        self.assertEqual(first, tree_sha256(tree))
        executable.chmod(0o700)
        self.assertNotEqual(first, tree_sha256(tree))


class ArtifactSemanticTests(TaskContractFixture):
    def test_valid_task_qualification_and_calibration_pack(self) -> None:
        task, qualification = self.make_task_and_qualification()
        task_result = validate_value(self.root, "diagnostic-task", task, Path("contracts/tasks/example/task.json"))
        self.assertTrue(task_result.valid, self.messages(task_result))
        qualification_result = validate_value(self.root, "task-qualification", qualification, Path("contracts/tasks/example/qualification.json"))
        self.assertTrue(qualification_result.valid, self.messages(qualification_result))
        contract = json.loads((self.root / "contracts/roles/task.json").read_text(encoding="utf-8"))
        pack = {
            "schema_version": "omp.task-pack/v1",
            "pack_id": "task-calibration",
            "role": "task",
            "task_mix": "task-v1",
            "role_contract": {"contract_id": contract["contract_id"], "digest_sha256": canonical_sha256(contract)},
            "partition": "anchor",
            "status": "calibration",
            "routing_eligible": False,
            "selection_method": "fixed-anchor",
            "required_capabilities": contract["required_capabilities"],
            "split_review": {"reviewer_id": "pack-reviewer-1", "independent": True, "review_digest_sha256": SHA_A},
            "entries": [{
                "task": {"path": "contracts/tasks/example/task.json", "digest_sha256": canonical_sha256(task)},
                "qualification": {"path": "contracts/tasks/example/qualification.json", "digest_sha256": canonical_sha256(qualification)},
            }],
        }
        result = validate_value(self.root, "task-pack", pack, Path("private/task-pack.json"))
        self.assertTrue(result.valid, self.messages(result))

    def test_task_accepts_terminal_bench_source(self) -> None:
        task, _ = self.make_task_and_qualification()
        task["source"] = {
            "kind": "terminal-bench",
            "version": "terminal-bench/terminal-bench-2-1@6",
            "digest_sha256": TB_TASK_DIGEST,
            "task": "terminal-bench/cancel-async-tasks",
            "dataset_digest_sha256": TB_DATASET_DIGEST,
        }
        self.refresh_license_review_evidence(task)

        result = validate_value(
            self.root,
            "diagnostic-task",
            task,
            Path("contracts/tasks/example/task.json"),
        )

        self.assertTrue(result.valid, self.messages(result))

    def test_terminal_bench_source_accepts_harbor_package_punctuation(self) -> None:
        task, _ = self.make_task_and_qualification()
        task["source"] = {
            "kind": "terminal-bench",
            "version": "terminal-bench/terminal_bench.3@1",
            "digest_sha256": TB_TASK_DIGEST,
            "task": "terminal-bench/install-windows-3.11",
            "dataset_digest_sha256": TB_DATASET_DIGEST,
        }
        self.refresh_license_review_evidence(task)

        result = validate_value(
            self.root,
            "diagnostic-task",
            task,
            Path("contracts/tasks/example/task.json"),
        )

        self.assertTrue(result.valid, self.messages(result))

    def test_terminal_bench_source_rejects_mutable_dataset_reference(self) -> None:
        task, _ = self.make_task_and_qualification()
        task["source"] = {
            "kind": "terminal-bench",
            "version": "terminal-bench/terminal-bench-2-1@latest",
            "digest_sha256": TB_TASK_DIGEST,
            "task": "terminal-bench/cancel-async-tasks",
            "dataset_digest_sha256": TB_DATASET_DIGEST,
        }

        result = validate_value(
            self.root,
            "diagnostic-task",
            task,
            Path("contracts/tasks/example/task.json"),
        )

        self.assertFalse(result.valid)
        self.assertIn("$.source", self.messages(result))

    def test_task_capabilities_may_cover_role_subset(self) -> None:
        task, _ = self.make_task_and_qualification()
        task["capability_tags"] = ["terminal-tool-use"]
        self.refresh_split_review_evidence(task)

        result = validate_value(
            self.root,
            "diagnostic-task",
            task,
            Path("contracts/tasks/example/task.json"),
        )

        self.assertTrue(result.valid, self.messages(result))

    def test_task_rejects_capabilities_outside_role_contract(self) -> None:
        task, _ = self.make_task_and_qualification()
        task["capability_tags"] = ["image-input"]

        result = validate_value(
            self.root,
            "diagnostic-task",
            task,
            Path("contracts/tasks/example/task.json"),
        )

        self.assertFalse(result.valid)
        self.assertIn("outside the role contract", self.messages(result))

    def test_pack_rejects_missing_collective_capability_coverage(self) -> None:
        task, qualification = self.make_task_and_qualification()
        task["capability_tags"] = ["terminal-tool-use"]
        task_digest = canonical_sha256(task)
        qualification["source_task"]["digest_sha256"] = task_digest  # type: ignore[index]
        qualification["observed_mapping"]["task_digest_sha256"] = task_execution_sha256(task)  # type: ignore[index]
        self.write_json("contracts/tasks/example/task.json", task)
        self.write_json("contracts/tasks/example/qualification.json", qualification)
        contract = json.loads(
            (self.root / "contracts/roles/task.json").read_text(encoding="utf-8")
        )
        pack = {
            "schema_version": "omp.task-pack/v1",
            "pack_id": "task-calibration",
            "role": "task",
            "task_mix": "task-v1",
            "role_contract": {
                "contract_id": contract["contract_id"],
                "digest_sha256": canonical_sha256(contract),
            },
            "partition": "anchor",
            "status": "calibration",
            "routing_eligible": False,
            "selection_method": "fixed-anchor",
            "required_capabilities": contract["required_capabilities"],
            "split_review": {
                "reviewer_id": "pack-reviewer-1",
                "independent": True,
                "review_digest_sha256": SHA_A,
            },
            "entries": [
                {
                    "task": {
                        "path": "contracts/tasks/example/task.json",
                        "digest_sha256": task_digest,
                    },
                    "qualification": {
                        "path": "contracts/tasks/example/qualification.json",
                        "digest_sha256": canonical_sha256(qualification),
                    },
                }
            ],
        }

        result = validate_value(
            self.root,
            "task-pack",
            pack,
            Path("private/task-pack.json"),
        )

        self.assertFalse(result.valid)
        self.assertIn(
            "entries do not cover required capabilities",
            self.messages(result),
        )

    def test_v1_qualification_cannot_claim_admission(self) -> None:
        _, qualification = self.make_task_and_qualification()
        qualification["checks"]["discrimination"] = "pass"  # type: ignore[index]
        qualification["decision"] = "admitted"

        result = validate_value(
            self.root,
            "task-qualification",
            qualification,
            Path("contracts/tasks/example/qualification.json"),
        )

        self.assertFalse(result.valid)
        self.assertIn("$.checks.discrimination", self.messages(result))
        self.assertIn("$.decision", self.messages(result))

    def test_v1_task_and_qualification_validate_against_preserved_contracts(
        self,
    ) -> None:
        task, qualification = self.make_task_and_qualification()
        task["schema_version"] = "omp.diagnostic-task/v1"
        v1_policy = json.loads(
            (self.root / "contracts/scored-worker-policy.json").read_text(
                encoding="utf-8"
            )
        )
        task["policy"] = {
            "path": "contracts/scored-worker-policy.json",
            "digest_sha256": canonical_sha256(v1_policy),
        }
        task.pop("runner")
        objective = task["objective"]
        assert isinstance(objective, dict)
        objective.pop("observation")
        reviews = task["reviews"]
        assert isinstance(reviews, dict)
        for review in reviews.values():
            assert isinstance(review, dict)
            review.pop("evidence_path", None)
        verifier_review = reviews["verifier"]
        assert isinstance(verifier_review, dict)
        verifier_review["image"] = verifier_review.pop("verifier_image")
        verifier_review["config_digest_sha256"] = verifier_review.pop(
            "verifier_config_digest_sha256"
        )
        verifier_review["platform"] = verifier_review.pop("verifier_platform")
        for field in (
            "runner_image",
            "runner_config_digest_sha256",
            "runner_platform",
        ):
            verifier_review.pop(field)
        self.write_json("contracts/tasks/example/task.json", task)

        qualification["schema_version"] = "omp.task-qualification/v1"
        source_task = qualification["source_task"]
        assert isinstance(source_task, dict)
        source_task["digest_sha256"] = canonical_sha256(task)
        qualification["reviews"] = deepcopy(reviews)
        evaluation = qualification.pop("evaluation_provenance")
        assert isinstance(evaluation, dict)
        qualification["verifier_provenance"] = {
            "reviewer_id": evaluation["reviewer_id"],
            "verifier_image": evaluation["verifier_image"],
            "verifier_config_digest_sha256": evaluation[
                "verifier_config_digest_sha256"
            ],
            "verifier_platform": evaluation["verifier_platform"],
            "review_digest_sha256": evaluation["review_digest_sha256"],
        }
        observed = qualification["observed_mapping"]
        assert isinstance(observed, dict)
        observed["task_digest_sha256"] = canonical_sha256(task)
        observed.pop("runner_config_digest_sha256")
        checks = qualification["checks"]
        assert isinstance(checks, dict)
        checks.pop("runner_isolation")
        checks.pop("observation_authority")
        self.write_json(
            "contracts/tasks/example/qualification.json",
            qualification,
        )

        task_result = validate_value(
            self.root,
            "diagnostic-task",
            task,
            Path("contracts/tasks/example/task.json"),
        )
        qualification_result = validate_value(
            self.root,
            "task-qualification",
            qualification,
            Path("contracts/tasks/example/qualification.json"),
        )

        self.assertTrue(task_result.valid, self.messages(task_result))
        self.assertTrue(
            qualification_result.valid,
            self.messages(qualification_result),
        )

    def test_synthetic_fixture_cannot_enter_any_task_pack(self) -> None:
        task, qualification = self.make_task_and_qualification()
        task["source"]["kind"] = "synthetic-fixture"  # type: ignore[index]
        task_digest = canonical_sha256(task)
        qualification["source_task"]["digest_sha256"] = task_digest  # type: ignore[index]
        qualification["observed_mapping"]["task_digest_sha256"] = task_execution_sha256(task)  # type: ignore[index]
        self.write_json("contracts/tasks/example/task.json", task)
        self.write_json(
            "contracts/tasks/example/qualification.json",
            qualification,
        )
        contract = json.loads(
            (self.root / "contracts/roles/task.json").read_text(
                encoding="utf-8"
            )
        )
        pack = {
            "schema_version": "omp.task-pack/v1",
            "pack_id": "synthetic-fixture-pack",
            "role": "task",
            "task_mix": "task-v1",
            "role_contract": {
                "contract_id": contract["contract_id"],
                "digest_sha256": canonical_sha256(contract),
            },
            "partition": "anchor",
            "status": "frozen",
            "routing_eligible": True,
            "selection_method": "fixed-anchor",
            "required_capabilities": contract["required_capabilities"],
            "split_review": {
                "reviewer_id": "pack-reviewer-1",
                "independent": True,
                "review_digest_sha256": SHA_A,
            },
            "entries": [
                {
                    "task": {
                        "path": "contracts/tasks/example/task.json",
                        "digest_sha256": task_digest,
                    },
                    "qualification": {
                        "path": "contracts/tasks/example/qualification.json",
                        "digest_sha256": canonical_sha256(qualification),
                    },
                }
            ],
        }

        result = validate_value(
            self.root,
            "task-pack",
            pack,
            Path("private/task-pack.json"),
        )

        self.assertIn(
            "synthetic fixtures cannot enter task packs",
            self.messages(result),
        )

    def test_task_rejects_asset_image_policy_role_and_split_mismatches(self) -> None:
        task, _ = self.make_task_and_qualification()
        bad = deepcopy(task)
        bad["split"]["reviewer_id"] = bad["split"]["author_id"]  # type: ignore[index]
        bad["policy"]["digest_sha256"] = SHA_A  # type: ignore[index]
        bad["agent"]["asset_tree_digest_sha256"] = SHA_A  # type: ignore[index]
        bad["runner"]["asset_tree_digest_sha256"] = SHA_A  # type: ignore[index]
        bad["assets"]["verifier_private"]["root"] = bad["assets"]["public"]["root"]  # type: ignore[index]
        bad["runner"]["image"] = bad["agent"]["image"]  # type: ignore[index]
        bad["verifier"]["image"] = bad["agent"]["image"]  # type: ignore[index]
        bad["role_contract"]["digest_sha256"] = SHA_A  # type: ignore[index]
        bad["reviews"]["privacy"]["reviewer"] = "author-1"  # type: ignore[index]
        bad["reviews"]["license"]["expression"] = "Apache-2.0"  # type: ignore[index]
        bad["reviews"]["verifier"]["private_tree_digest_sha256"] = SHA_A  # type: ignore[index]
        bad["reviews"]["verifier"]["runner_image"] = bad["agent"]["image"]  # type: ignore[index]
        result = validate_value(self.root, "diagnostic-task", bad, Path("bad-task.json"))
        messages = self.messages(result)
        for expected in ("independent", "exact task license", "exact verifier execution", "disjoint", "canonical policy", "public asset tree", "differ from the agent", "canonical role-contract"):
            self.assertIn(expected, messages)

    def test_task_requires_runner_and_distinct_container_mappings(self) -> None:
        task, _ = self.make_task_and_qualification()
        without_runner = deepcopy(task)
        del without_runner["runner"]
        result = validate_value(self.root, "diagnostic-task", without_runner, Path("no-runner.json"))
        self.assertFalse(result.valid)
        self.assertTrue(any("runner" in item.message or item.json_path in ("$", "$.runner") for item in result.diagnostics))

        swapped = deepcopy(task)
        swapped["runner"]["asset_tree_digest_sha256"] = swapped["assets"]["verifier_private"]["digest_sha256"]  # type: ignore[index]
        swapped["verifier"]["asset_tree_digest_sha256"] = swapped["assets"]["public"]["digest_sha256"]  # type: ignore[index]
        swapped_result = validate_value(self.root, "diagnostic-task", swapped, Path("swapped.json"))
        self.assertFalse(swapped_result.valid)
        self.assertIn("must bind the public asset tree", self.messages(swapped_result))
        self.assertIn("must bind the verifier-private asset tree", self.messages(swapped_result))

    def test_task_observation_requires_source_separated_service_for_executable_artifacts(self) -> None:
        task, _ = self.make_task_and_qualification()
        bad_authority = deepcopy(task)
        bad_authority["objective"]["observation"]["artifact_kind"] = "executable"  # type: ignore[index]
        bad_authority["objective"]["observation"]["authority"] = "host-process"  # type: ignore[index]
        result = validate_value(self.root, "diagnostic-task", bad_authority, Path("bad-obs.json"))
        self.assertFalse(result.valid)
        self.assertIn("source-separated-service", self.messages(result))

        bad_trust = deepcopy(task)
        bad_trust["objective"]["observation"]["runner_output_trust"] = "trusted"  # type: ignore[index]
        trust_result = validate_value(self.root, "diagnostic-task", bad_trust, Path("bad-trust.json"))
        self.assertFalse(trust_result.valid)
        self.assertIn("runner_output_trust", self.messages(trust_result))

    def test_qualification_fails_closed_for_host_filesystem_authority(
        self,
    ) -> None:
        task, qualification = self.make_task_and_qualification()
        task["objective"]["observation"]["authority"] = (  # type: ignore[index]
            "host-filesystem"
        )
        qualification["source_task"]["digest_sha256"] = (  # type: ignore[index]
            canonical_sha256(task)
        )
        qualification["observed_mapping"]["task_digest_sha256"] = (  # type: ignore[index]
            task_execution_sha256(task)
        )
        self.write_json("contracts/tasks/example/task.json", task)

        passing = validate_value(
            self.root,
            "task-qualification",
            qualification,
            Path("contracts/tasks/example/qualification.json"),
        )
        self.assertFalse(passing.valid)
        self.assertIn(
            "unsupported authorities must record 'fail'",
            self.messages(passing),
        )
        self.assertIn(
            "must be rejected",
            self.messages(passing),
        )

        qualification["checks"]["observation_authority"] = "fail"  # type: ignore[index]
        qualification["decision"] = "rejected"
        fail_closed = validate_value(
            self.root,
            "task-qualification",
            qualification,
            Path("contracts/tasks/example/qualification.json"),
        )
        self.assertTrue(
            fail_closed.valid,
            self.messages(fail_closed),
        )

    def test_executable_task_with_reviewed_source_separated_observer_qualifies_and_enters_pack(self) -> None:
        task, qualification = self.make_task_and_qualification()
        task["objective"]["observation"]["artifact_kind"] = "executable"  # type: ignore[index]
        task["objective"]["observation"]["authority"] = "source-separated-service"  # type: ignore[index]
        verifier_evidence_path = self.root / "contracts/tasks/example/reviews/verifier.json"
        verifier_evidence = json.loads(verifier_evidence_path.read_text(encoding="utf-8"))
        verifier_evidence["checks"]["source_separated_observer"] = "pass"
        self.write_json("contracts/tasks/example/reviews/verifier.json", verifier_evidence)
        new_verifier_digest = canonical_sha256(verifier_evidence)
        task["reviews"]["verifier"]["evidence_digest_sha256"] = new_verifier_digest  # type: ignore[index]
        qualification["reviews"]["verifier"]["evidence_digest_sha256"] = new_verifier_digest  # type: ignore[index]
        qualification["evaluation_provenance"]["review_digest_sha256"] = new_verifier_digest  # type: ignore[index]
        task_digest = canonical_sha256(task)
        qualification["source_task"]["digest_sha256"] = task_digest  # type: ignore[index]
        qualification["observed_mapping"]["task_digest_sha256"] = task_execution_sha256(task)  # type: ignore[index]
        self.write_json("contracts/tasks/example/task.json", task)
        self.write_json("contracts/tasks/example/qualification.json", qualification)

        task_result = validate_value(self.root, "diagnostic-task", task, Path("contracts/tasks/example/task.json"))
        self.assertTrue(task_result.valid, self.messages(task_result))

        qual_result = validate_value(self.root, "task-qualification", qualification, Path("contracts/tasks/example/qualification.json"))
        self.assertTrue(qual_result.valid, self.messages(qual_result))

        contract = json.loads((self.root / "contracts/roles/task.json").read_text(encoding="utf-8"))
        pack = {
            "schema_version": "omp.task-pack/v1",
            "pack_id": "task-calibration",
            "role": "task",
            "task_mix": "task-v1",
            "role_contract": {"contract_id": contract["contract_id"], "digest_sha256": canonical_sha256(contract)},
            "partition": "anchor",
            "status": "calibration",
            "routing_eligible": False,
            "selection_method": "fixed-anchor",
            "required_capabilities": contract["required_capabilities"],
            "split_review": {"reviewer_id": "pack-reviewer-1", "independent": True, "review_digest_sha256": SHA_A},
            "entries": [{
                "task": {"path": "contracts/tasks/example/task.json", "digest_sha256": task_digest},
                "qualification": {"path": "contracts/tasks/example/qualification.json", "digest_sha256": canonical_sha256(qualification)},
            }],
        }
        pack_result = validate_value(self.root, "task-pack", pack, Path("private/task-pack.json"))
        self.assertTrue(pack_result.valid, self.messages(pack_result))

    def test_executable_tasks_without_reviewed_observer_fail_closed(self) -> None:
        task, qualification = self.make_task_and_qualification()
        task["objective"]["observation"]["artifact_kind"] = "executable"  # type: ignore[index]
        task["objective"]["observation"]["authority"] = "source-separated-service"  # type: ignore[index]
        task_digest = canonical_sha256(task)
        qualification["source_task"]["digest_sha256"] = task_digest  # type: ignore[index]
        qualification["observed_mapping"]["task_digest_sha256"] = task_execution_sha256(task)  # type: ignore[index]
        self.write_json("contracts/tasks/example/task.json", task)
        self.write_json("contracts/tasks/example/qualification.json", qualification)

        # 1. Unreviewed executable task fails diagnostic task validation
        task_result = validate_value(self.root, "diagnostic-task", task, Path("contracts/tasks/example/task.json"))
        self.assertFalse(task_result.valid)
        self.assertIn("source_separated_observer", self.messages(task_result))

        # 2. Qualification with observation_authority = pass fails qualification validation
        qual_result = validate_value(self.root, "task-qualification", qualification, Path("contracts/tasks/example/qualification.json"))
        self.assertFalse(qual_result.valid)
        self.assertIn(
            "unsupported authorities must record 'fail'",
            self.messages(qual_result),
        )
        self.assertIn(
            "must be rejected",
            self.messages(qual_result),
        )

        contract = json.loads((self.root / "contracts/roles/task.json").read_text(encoding="utf-8"))
        pack = {
            "schema_version": "omp.task-pack/v1",
            "pack_id": "task-calibration",
            "role": "task",
            "task_mix": "task-v1",
            "role_contract": {"contract_id": contract["contract_id"], "digest_sha256": canonical_sha256(contract)},
            "partition": "anchor",
            "status": "calibration",
            "routing_eligible": False,
            "selection_method": "fixed-anchor",
            "required_capabilities": contract["required_capabilities"],
            "split_review": {"reviewer_id": "pack-reviewer-1", "independent": True, "review_digest_sha256": SHA_A},
            "entries": [{
                "task": {"path": "contracts/tasks/example/task.json", "digest_sha256": task_digest},
                "qualification": {"path": "contracts/tasks/example/qualification.json", "digest_sha256": canonical_sha256(qualification)},
            }],
        }
        pack_result = validate_value(self.root, "task-pack", pack, Path("private/task-pack.json"))
        self.assertFalse(pack_result.valid)

        # 3. Failing review check (source_separated_observer: fail) also fails closed
        verifier_evidence_path = self.root / "contracts/tasks/example/reviews/verifier.json"
        verifier_evidence = json.loads(verifier_evidence_path.read_text(encoding="utf-8"))
        verifier_evidence["checks"]["source_separated_observer"] = "fail"
        self.write_json("contracts/tasks/example/reviews/verifier.json", verifier_evidence)
        new_verifier_digest = canonical_sha256(verifier_evidence)
        task["reviews"]["verifier"]["evidence_digest_sha256"] = new_verifier_digest  # type: ignore[index]
        self.write_json("contracts/tasks/example/task.json", task)
        task_fail_check = validate_value(self.root, "diagnostic-task", task, Path("contracts/tasks/example/task.json"))
        self.assertFalse(task_fail_check.valid)
    def test_qualification_rejects_non_deterministic_decision_and_stale_evidence_mapping(self) -> None:
        _, qualification = self.make_task_and_qualification()
        bad = deepcopy(qualification)
        bad["checks"]["privacy"] = "fail"  # type: ignore[index]
        bad["decision"] = "calibration-required"
        bad["observed_mapping"]["public_tree_digest_sha256"] = SHA_A  # type: ignore[index]
        bad["observed_mapping"]["runner_config_digest_sha256"] = SHA_A  # type: ignore[index]
        bad["checks"]["baseline_fails"]["evidence"][0]["digest_sha256"] = SHA_A  # type: ignore[index]
        bad["reviews"]["privacy"]["evidence_digest_sha256"] = SHA_C  # type: ignore[index]
        result = validate_value(self.root, "task-qualification", bad, Path("bad-qualification.json"))
        messages = self.messages(result)
        self.assertIn("must be 'rejected'", messages)
        self.assertIn("source task execution mapping", messages)
        self.assertIn("evidence file SHA-256", messages)
        self.assertIn("human review attestations", messages)

    def test_task_execution_digest_excludes_review_attestations(self) -> None:
        task, _ = self.make_task_and_qualification()
        changed = deepcopy(task)
        changed["reviews"]["verifier"]["evidence_digest_sha256"] = SHA_C  # type: ignore[index]

        self.assertNotEqual(canonical_sha256(task), canonical_sha256(changed))
        self.assertEqual(
            task_execution_sha256(task),
            task_execution_sha256(changed),
        )

    def test_task_execution_digest_excludes_split_review_binding(self) -> None:
        task, _ = self.make_task_and_qualification()
        changed = deepcopy(task)
        changed["reviews"]["split"]["reviewer"] = "reviewer-2"  # type: ignore[index]
        changed["reviews"]["split"]["evidence_digest_sha256"] = SHA_B  # type: ignore[index]
        changed["split"]["reviewer_id"] = "reviewer-2"  # type: ignore[index]
        changed["split"]["provenance_digest_sha256"] = SHA_B  # type: ignore[index]

        self.assertNotEqual(canonical_sha256(task), canonical_sha256(changed))
        self.assertEqual(
            task_execution_sha256(task),
            task_execution_sha256(changed),
        )

    def test_qualification_reports_must_match_verifier_review(self) -> None:
        _, qualification = self.make_task_and_qualification()
        report_path = (
            self.root
            / "contracts/tasks/example/evidence/baseline-1.json"
        )
        report_path.write_text(
            report_path.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        qualification["checks"]["baseline_fails"]["evidence"][0][  # type: ignore[index]
            "digest_sha256"
        ] = file_sha256(report_path)

        result = validate_value(
            self.root,
            "task-qualification",
            qualification,
            Path("contracts/tasks/example/qualification.json"),
        )

        self.assertFalse(result.valid)
        self.assertIn(
            "must exactly match the qualification report file digests",
            self.messages(result),
        )

    def test_verifier_review_requires_exactly_six_report_digests(self) -> None:
        task, _ = self.make_task_and_qualification()
        review_relative = Path(
            task["reviews"]["verifier"]["evidence_path"]  # type: ignore[index]
        )
        review = json.loads(
            (self.root / review_relative).read_text(encoding="utf-8")
        )
        review["report_digests_sha256"].append(SHA_A)

        result = validate_value(
            self.root,
            "task-review-evidence",
            review,
            review_relative,
        )

        self.assertFalse(result.valid)
        self.assertIn("is too long", self.messages(result))

    def test_qualification_evaluation_provenance_must_match_task_runner_and_verifier(self) -> None:
        _, qualification = self.make_task_and_qualification()
        bad_runner = deepcopy(qualification)
        bad_runner["evaluation_provenance"]["runner_image"] = IMAGE_A  # type: ignore[index]
        result = validate_value(self.root, "task-qualification", bad_runner, Path("bad-prov-runner.json"))
        self.assertIn("must match the source task runner", self.messages(result))

        bad_verifier = deepcopy(qualification)
        bad_verifier["evaluation_provenance"]["verifier_image"] = IMAGE_A  # type: ignore[index]
        result = validate_value(self.root, "task-qualification", bad_verifier, Path("bad-prov-verifier.json"))
        self.assertIn("must match the source task verifier", self.messages(result))

    def test_qualification_requires_passing_observation_authority(self) -> None:
        _, qualification = self.make_task_and_qualification()
        bad_check = deepcopy(qualification)
        bad_check["checks"]["observation_authority"] = "fail"  # type: ignore[index]
        bad_check["decision"] = "calibration-required"
        result = validate_value(self.root, "task-qualification", bad_check, Path("bad-obs-auth.json"))
        self.assertFalse(result.valid)
        self.assertIn("must be 'rejected'", self.messages(result))

    def test_noassertion_cannot_be_admitted(self) -> None:
        task, qualification = self.make_task_and_qualification()
        task["license"] = {"expression": "NOASSERTION", "redistribution": "prohibited"}
        self.write_json("contracts/tasks/example/task.json", task)
        qualification["source_task"]["digest_sha256"] = canonical_sha256(task)  # type: ignore[index]
        qualification["observed_mapping"]["task_digest_sha256"] = task_execution_sha256(task)  # type: ignore[index]
        result = validate_value(self.root, "task-qualification", qualification, Path("qualification.json"))
        self.assertIn("without approved redistribution", self.messages(result))

    def test_observation_lifecycle_gaps_and_runner_evidence_are_rejected(self) -> None:
        observation = {
            "schema_version": "omp.attempt-observation/v2",
            "observation_id": "obs-001",
            "observed_at": "2026-08-14T00:00:00Z",
            "attempt": {"attempt_id": "att-001", "number": 1, "previous_attempt_id": None},
            "stage": "complete",
            "lifecycle": {
                "environment_started": True,
                "agent_started": True,
                "agent_finished": True,
                "artifact_frozen": True,
                "runner_started": False,
                "runner_finished": True,
                "runner_evidence_frozen": True,
                "verifier_started": True,
                "verifier_finished": True,
            },
            "readiness": {"environment": "ready", "runner": "healthy", "provider": "available"},
            "issues": [],
            "provider": {"request_started": True, "http_status": 200},
            "termination": {"kind": "completed", "exit_code": 0, "signal": None, "oom_scope": "none"},
            "verifier": {"outcome": "accepted", "result_valid": True, "reward": 1},
            "integrity": {"state": "verified"},
            "digests": {
                "task": "1" * 64,
                "config": "2" * 64,
                "agent_image": "3" * 64,
                "runner_image": "4" * 64,
                "verifier_image": "5" * 64,
                "runtime_policy": "6" * 64,
                "artifact": "7" * 64,
                "runner_evidence": None,
                "trajectory": "9" * 64,
            },
        }
        result = validate_value(self.root, "attempt-observation", observation, Path("obs.json"))
        self.assertFalse(result.valid)
        messages = self.messages(result)
        self.assertIn("runner_finished requires runner_started", messages)
        self.assertIn("a frozen runner evidence requires its digest", messages)

    def test_verifier_result_schema_validates_strict_clean_cutover_shape(self) -> None:
        valid_result = {
            "schema_version": "omp.verifier-result/v1",
            "run_id": "run-001",
            "attempt_nonce": "0" * 64,
            "outcome": "accepted",
            "reward": 1,
            "artifact_digest_sha256": "1" * 64,
            "runner_evidence_digest_sha256": "2" * 64,
            "evaluation_request_digest_sha256": "3" * 64,
            "verifier_image_digest_sha256": "4" * 64,
        }
        result = validate_value(self.root, "verifier-result", valid_result, Path("res.json"))
        self.assertTrue(result.valid, self.messages(result))

        missing_nonce = deepcopy(valid_result)
        del missing_nonce["attempt_nonce"]
        res_missing = validate_value(self.root, "verifier-result", missing_nonce, Path("res-missing.json"))
        self.assertFalse(res_missing.valid)

        mismatched_reward = deepcopy(valid_result)
        mismatched_reward["outcome"] = "rejected"
        mismatched_reward["reward"] = 1
        res_mismatched = validate_value(self.root, "verifier-result", mismatched_reward, Path("res-mismatched.json"))
        self.assertFalse(res_mismatched.valid)

    def test_authoring_and_calibration_packs_cannot_enter_routing(self) -> None:
        contract = json.loads((self.root / "contracts/roles/task.json").read_text(encoding="utf-8"))
        base = {
            "schema_version": "omp.task-pack/v1",
            "pack_id": "unsafe",
            "role": "task",
            "task_mix": "task-v1",
            "role_contract": {"contract_id": contract["contract_id"], "digest_sha256": canonical_sha256(contract)},
            "partition": "anchor",
            "status": "authoring",
            "routing_eligible": True,
            "selection_method": "fixed-anchor",
            "required_capabilities": contract["required_capabilities"],
            "entries": [],
        }
        for status in ("authoring", "calibration"):
            bad = deepcopy(base)
            bad["status"] = status
            result = validate_value(self.root, "task-pack", bad, Path("private/unsafe.json"))
            self.assertFalse(result.valid)
            self.assertIn("routing_eligible", self.messages(result))

    def test_public_holdout_manifest_is_rejected(self) -> None:
        contract = json.loads((self.root / "contracts/roles/task.json").read_text(encoding="utf-8"))
        pack = {
            "schema_version": "omp.task-pack/v1",
            "pack_id": "holdout",
            "role": "task",
            "task_mix": "task-v1",
            "role_contract": {"contract_id": contract["contract_id"], "digest_sha256": canonical_sha256(contract)},
            "partition": "holdout",
            "status": "authoring",
            "routing_eligible": False,
            "selection_method": "sealed-holdout",
            "required_capabilities": contract["required_capabilities"],
            "entries": [],
        }
        result = validate_value(self.root, "task-pack", pack, Path("contracts/task-packs/holdout.json"))
        self.assertIn("must not be holdout", self.messages(result))

    def test_session_candidate_has_opaque_ref_not_stable_source_digest(self) -> None:
        candidate = {
            "schema_version": "omp.task-candidate/v1",
            "origin": {"kind": "omp-session", "source_version": "3", "candidate_ref": "12345678-1234-4123-8123-123456789abc"},
            "capability_hints": [],
            "review": {"privacy": "required", "license": "required", "publication": "prohibited"},
            "entry_count": 3,
            "signals": [{"ordinal": 2, "kind": "late-test-failure"}],
        }
        self.assertTrue(validate_value(self.root, "task-candidate", candidate, Path("candidate.json")).valid)
        candidate["origin"]["digest_sha256"] = SHA_A  # type: ignore[index]
        self.assertFalse(validate_value(self.root, "task-candidate", candidate, Path("candidate.json")).valid)

    def test_ledger_is_non_authoritative_and_binds_normalized_payload(self) -> None:
        payload = {
            "schema_version": "omp.task-candidate/v1"
        }
        entry = {
            "schema_version": "omp.experiment-ledger-entry/v1",
            "authority": "routing-authority",
            "sequence": 0,
            "timestamp": "2026-08-14T00:00:00Z",
            "previous_entry_sha256": None,
            "payload_schema": "task-qualification",
            "payload_digest_sha256": canonical_sha256(payload),
            "payload": payload,
        }
        result = validate_value(self.root, "experiment-ledger-entry", entry, Path("ledger.json"))
        self.assertFalse(result.valid)
        self.assertIn("local-non-authoritative", self.messages(result))
        entry["authority"] = "local-non-authoritative"
        entry["payload_digest_sha256"] = SHA_A
        result = validate_value(self.root, "experiment-ledger-entry", entry, Path("ledger.json"))
        self.assertIn("canonical payload", self.messages(result))


class SanitizerRunnerTests(unittest.TestCase):
    def test_accepts_standard_git_diff_preamble_and_binds_paths(
        self,
    ) -> None:
        namespace = runpy.run_path(
            str(
                PRODUCT_ROOT
                / "contracts/tasks/terminal-bench.sanitize-git-repo"
                / "2.1-r6/runner.py"
            )
        )
        apply_patch = namespace["_apply_patch"]
        standard_patch = (
            "diff --git a/first.txt b/first.txt\n"
            "index 1111111..2222222 100644\n"
            "--- a/first.txt\n"
            "+++ b/first.txt\n"
            "@@ -1 +1 @@\n"
            "-alpha=old\n"
            "+alpha=new\n"
            "diff --git a/second.txt b/second.txt\n"
            "index aaaaaaa..bbbbbbb 100644\n"
            "--- a/second.txt\n"
            "+++ b/second.txt\n"
            "@@ -1 +1 @@\n"
            "-beta=old\n"
            "+beta=new\n"
        )

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            first = workspace / "first.txt"
            second = workspace / "second.txt"
            first.write_text("alpha=old\n", encoding="utf-8")
            second.write_text("beta=old\n", encoding="utf-8")

            applied, error = apply_patch(standard_patch, workspace)

            self.assertTrue(applied, error)
            self.assertIsNone(error)
            self.assertEqual(
                first.read_text(encoding="utf-8"),
                "alpha=new\n",
            )
            self.assertEqual(
                second.read_text(encoding="utf-8"),
                "beta=new\n",
            )

            first.write_text("alpha=old\n", encoding="utf-8")
            second.write_text("beta=old\n", encoding="utf-8")
            mismatched = standard_patch.replace(
                "diff --git a/first.txt b/first.txt",
                "diff --git a/first.txt b/unrelated.txt",
                1,
            )
            applied, error = apply_patch(mismatched, workspace)

            self.assertFalse(applied)
            self.assertEqual(
                error,
                "git diff paths do not match file headers",
            )
            self.assertEqual(
                first.read_text(encoding="utf-8"),
                "alpha=old\n",
            )
            self.assertEqual(
                second.read_text(encoding="utf-8"),
                "beta=old\n",
            )

    def test_hunk_counts_use_git_defaults_and_preserve_explicit_zero(
        self,
    ) -> None:
        namespace = runpy.run_path(
            str(
                PRODUCT_ROOT
                / "contracts/tasks/terminal-bench.sanitize-git-repo"
                / "2.1-r6/runner.py"
            )
        )
        apply_patch = namespace["_apply_patch"]

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            target = workspace / "sample.txt"
            target.write_text("middle\n", encoding="utf-8")

            inserted, error = apply_patch(
                "--- a/sample.txt\n"
                "+++ b/sample.txt\n"
                "@@ -0,0 +1 @@\n"
                "+first\n",
                workspace,
            )

            self.assertTrue(inserted, error)
            self.assertIsNone(error)
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "first\nmiddle\n",
            )

            deleted, error = apply_patch(
                "--- a/sample.txt\n"
                "+++ b/sample.txt\n"
                "@@ -1 +0,0 @@\n"
                "-first\n",
                workspace,
            )

            self.assertTrue(deleted, error)
            self.assertIsNone(error)
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "middle\n",
            )

    def test_rejects_inconsistent_unified_diff_hunk_metadata(self) -> None:
        namespace = runpy.run_path(
            str(
                PRODUCT_ROOT
                / "contracts/tasks/terminal-bench.sanitize-git-repo"
                / "2.1-r6/runner.py"
            )
        )
        apply_patch = namespace["_apply_patch"]
        valid_patch = (
            "--- a/sample.txt\n"
            "+++ b/sample.txt\n"
            "@@ -1 +1 @@\n"
            "-secret=old\n"
            "+secret=new\n"
        )
        malformed_patches = {
            "false line counts": valid_patch.replace(
                "@@ -1 +1 @@",
                "@@ -1,999 +1,999 @@",
            ),
            "false new-file position": valid_patch.replace(
                "@@ -1 +1 @@",
                "@@ -1 +2 @@",
            ),
            "mismatched file paths": valid_patch.replace(
                "--- a/sample.txt",
                "--- a/unrelated.txt",
            ),
            "file creation header": valid_patch.replace(
                "--- a/sample.txt",
                "--- /dev/null",
            ),
            "file deletion header": valid_patch.replace(
                "+++ b/sample.txt",
                "+++ /dev/null",
            ),
            "content before first hunk": valid_patch.replace(
                "@@ -1 +1 @@\n",
                "garbage\n@@ -1 +1 @@\n",
            ),
            "duplicate target section": valid_patch + valid_patch,
            "oversized hunk coordinate": valid_patch.replace(
                "@@ -1 +1 @@",
                f"@@ -{'9' * 5000} +1 @@",
            ),
            "overlong patch path": valid_patch.replace(
                "sample.txt",
                "/".join(["segment"] * 150),
            ),
        }

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            target = workspace / "sample.txt"
            for label, patch in malformed_patches.items():
                with self.subTest(label):
                    target.write_text("secret=old\n", encoding="utf-8")
                    applied, error = apply_patch(patch, workspace)
                    self.assertFalse(applied)
                    self.assertIsNotNone(error)
                    self.assertEqual(
                        target.read_text(encoding="utf-8"),
                        "secret=old\n",
                    )

    def test_enforces_no_newline_markers(self) -> None:
        namespace = runpy.run_path(
            str(
                PRODUCT_ROOT
                / "contracts/tasks/terminal-bench.sanitize-git-repo"
                / "2.1-r6/runner.py"
            )
        )
        apply_patch = namespace["_apply_patch"]
        patch_prefix = (
            "--- a/sample.txt\n"
            "+++ b/sample.txt\n"
            "@@ -1 +1 @@\n"
            "-secret=old\n"
        )
        no_newline_marker = "\\ No newline at end of file\n"

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            target = workspace / "sample.txt"

            target.write_bytes(b"secret=old\n")
            applied, error = apply_patch(
                patch_prefix
                + no_newline_marker
                + "+secret=new\n",
                workspace,
            )
            self.assertFalse(applied)
            self.assertIsNotNone(error)
            self.assertEqual(target.read_bytes(), b"secret=old\n")

            target.write_bytes(b"secret=old\n")
            split_addition_patch = (
                "--- a/sample.txt\n"
                "+++ b/sample.txt\n"
                "@@ -1 +1,2 @@\n"
                "-secret=old\n"
                "+secret=\n"
                + no_newline_marker
                + "+new\n"
            )
            applied, error = apply_patch(split_addition_patch, workspace)
            self.assertFalse(applied)
            self.assertIsNotNone(error)
            self.assertEqual(target.read_bytes(), b"secret=old\n")

            target.write_bytes(b"secret=old\n")
            applied, error = apply_patch(
                patch_prefix + "+secret=new",
                workspace,
            )
            self.assertFalse(applied)
            self.assertIsNotNone(error)
            self.assertEqual(target.read_bytes(), b"secret=old\n")

            target.write_bytes(b"secret=old")
            applied, error = apply_patch(
                patch_prefix
                + no_newline_marker
                + "+secret=new\n"
                + no_newline_marker,
                workspace,
            )
            self.assertTrue(applied, error)
            self.assertIsNone(error)
            self.assertEqual(target.read_bytes(), b"secret=new")

            target.write_bytes(b"secret=old")
            applied, error = apply_patch(
                patch_prefix
                + no_newline_marker
                + no_newline_marker
                + "+secret=new\n"
                + no_newline_marker,
                workspace,
            )
            self.assertFalse(applied)
            self.assertIsNotNone(error)
            self.assertEqual(target.read_bytes(), b"secret=old")

    def test_bare_unified_diff_handles_double_hyphen_deletion_line(
        self,
    ) -> None:
        namespace = runpy.run_path(
            str(
                PRODUCT_ROOT
                / "contracts/tasks/terminal-bench.sanitize-git-repo"
                / "2.1-r6/runner.py"
            )
        )
        apply_patch = namespace["_apply_patch"]

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            target = workspace / "config.yaml"
            target.write_text(
                "server:\n-- port: 8080\n-- debug: true\nmode: production\n",
                encoding="utf-8",
            )

            bare_patch = (
                "--- a/config.yaml\n"
                "+++ b/config.yaml\n"
                "@@ -1,4 +1,4 @@\n"
                " server:\n"
                "--- port: 8080\n"
                "+-- port: 9090\n"
                "--- debug: true\n"
                "+-- debug: false\n"
                " mode: production\n"
            )
            applied, error = apply_patch(bare_patch, workspace)
            self.assertTrue(applied, error)
            self.assertIsNone(error)
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "server:\n-- port: 9090\n-- debug: false\nmode: production\n",
            )

    def test_bare_unified_diff_multi_file_structural_parsing(self) -> None:
        namespace = runpy.run_path(
            str(
                PRODUCT_ROOT
                / "contracts/tasks/terminal-bench.sanitize-git-repo"
                / "2.1-r6/runner.py"
            )
        )
        apply_patch = namespace["_apply_patch"]

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            first = workspace / "first.txt"
            second = workspace / "second.txt"
            first.write_text(
                "alpha=1\n-- flag: old\nalpha=3\n",
                encoding="utf-8",
            )
            second.write_text(
                "beta=1\nbeta=2\nbeta=3\n",
                encoding="utf-8",
            )

            multi_patch = (
                "--- a/first.txt\n"
                "+++ b/first.txt\n"
                "@@ -1,3 +1,3 @@\n"
                " alpha=1\n"
                "--- flag: old\n"
                "+-- flag: new\n"
                " alpha=3\n"
                "--- a/second.txt\n"
                "+++ b/second.txt\n"
                "@@ -1,3 +1,3 @@\n"
                " beta=1\n"
                "-beta=2\n"
                "+beta=updated\n"
                " beta=3\n"
            )
            applied, error = apply_patch(multi_patch, workspace)
            self.assertTrue(applied, error)
            self.assertIsNone(error)
            self.assertEqual(
                first.read_text(encoding="utf-8"),
                "alpha=1\n-- flag: new\nalpha=3\n",
            )
            self.assertEqual(
                second.read_text(encoding="utf-8"),
                "beta=1\nbeta=updated\nbeta=3\n",
            )

    def test_bare_unified_diff_malformed_and_truncated_boundaries(
        self,
    ) -> None:
        namespace = runpy.run_path(
            str(
                PRODUCT_ROOT
                / "contracts/tasks/terminal-bench.sanitize-git-repo"
                / "2.1-r6/runner.py"
            )
        )
        apply_patch = namespace["_apply_patch"]

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            first = workspace / "first.txt"
            second = workspace / "second.txt"
            first.write_text("alpha=1\n-- flag: old\nalpha=3\n", encoding="utf-8")
            second.write_text("beta=1\nbeta=2\nbeta=3\n", encoding="utf-8")

            # 1. Truncated hunk body
            p_trunc = (
                "--- a/first.txt\n"
                "+++ b/first.txt\n"
                "@@ -1,3 +1,3 @@\n"
                " alpha=1\n"
                "--- flag: old\n"
            )
            applied, error = apply_patch(p_trunc, workspace)
            self.assertFalse(applied)
            self.assertIsNotNone(error)
            self.assertEqual(
                first.read_text(encoding="utf-8"),
                "alpha=1\n-- flag: old\nalpha=3\n",
            )

            # 2. Garbage line between bare file patches
            p_garbage = (
                "--- a/first.txt\n"
                "+++ b/first.txt\n"
                "@@ -1,3 +1,3 @@\n"
                " alpha=1\n"
                "--- flag: old\n"
                "+-- flag: new\n"
                " alpha=3\n"
                "invalid inter-patch content\n"
                "--- a/second.txt\n"
                "+++ b/second.txt\n"
                "@@ -1,3 +1,3 @@\n"
                " beta=1\n"
                "-beta=2\n"
                "+beta=updated\n"
                " beta=3\n"
            )
            applied, error = apply_patch(p_garbage, workspace)
            self.assertFalse(applied)
            self.assertIsNotNone(error)
            self.assertEqual(
                first.read_text(encoding="utf-8"),
                "alpha=1\n-- flag: old\nalpha=3\n",
            )
            self.assertEqual(
                second.read_text(encoding="utf-8"),
                "beta=1\nbeta=2\nbeta=3\n",
            )

            # 3. Truncated file header
            applied, error = apply_patch("--- a/first.txt\n", workspace)
            self.assertFalse(applied)
            self.assertEqual(error, "truncated file patch header")

            # 4. Invalid second header line
            applied, error = apply_patch("--- a/first.txt\n@@ -1 +1 @@\n", workspace)
            self.assertFalse(applied)
            self.assertEqual(error, "invalid file patch header")

            # 5. Unexpected prefix in hunk
            p_bad_prefix = (
                "--- a/first.txt\n"
                "+++ b/first.txt\n"
                "@@ -1,3 +1,3 @@\n"
                " alpha=1\n"
                "?-- flag: old\n"
                " alpha=3\n"
            )
            applied, error = apply_patch(p_bad_prefix, workspace)
            self.assertFalse(applied)
            self.assertIsNotNone(error)
            self.assertEqual(
                first.read_text(encoding="utf-8"),
                "alpha=1\n-- flag: old\nalpha=3\n",
            )



class RoleAnchorSemanticTests(unittest.TestCase):
    def test_cad_reference_covers_every_visible_dimension(self) -> None:
        task_dir = PRODUCT_ROOT / "contracts/tasks/terminal-bench.cad-model/3.0-r1"
        expected = runpy.run_path(
            str(task_dir / "verifier-private/verifier.py")
        )["_expected_submission"]()
        expected_dimensions = {
            "base_flange": {
                "length": 73,
                "width": 75,
                "thickness": 13,
                "corner_radius": 17,
            },
            "base_mounting_holes": {
                "through_diameter": 6,
                "counterbore_diameter": 12,
                "counterbore_depth": 4,
            },
            "vertical_rib": {
                "thickness": 13,
                "top_radius": 15,
                "height": 55,
                "included_angle_degrees": 75,
            },
            "vertical_rib_hole": {"diameter": 12},
            "inclined_tab": {
                "width": 75,
                "thickness": 7,
                "end_radius": 37.5,
                "angle_degrees": 45,
                "transition_radius": 16,
                "vertical_drop": 45,
            },
            "inclined_tab_hole": {"diameter": 33},
        }

        for probe in ("candidate", "reference"):
            with self.subTest(probe=probe):
                submission = runpy.run_path(str(task_dir / f"probes/{probe}.py"))[
                    "submission"
                ]
                self.assertEqual(submission, expected)
                actual_dimensions = {
                    feature["id"]: feature["dimensions"]
                    for feature in submission["features"]
                }
                self.assertEqual(actual_dimensions, expected_dimensions)

    def test_vim_runner_rejects_function_calls_inside_macros(self) -> None:
        task_dir = (
            PRODUCT_ROOT
            / "contracts/tasks/terminal-bench.large-scale-text-editing/2.1-r6"
        )
        namespace = runpy.run_path(str(task_dir / "runner.py"))
        validate_script = namespace["_validate_script"]
        submission_error = namespace["SubmissionError"]
        valid_script = runpy.run_path(str(task_dir / "probes/reference.py"))["script"]

        validate_script(valid_script)
        lines = valid_script.splitlines()
        lines[0] = (
            "call setreg('a', \":call setline('.', 'bypass')"
            "\\<CR>j\")"
        )
        with self.assertRaisesRegex(
            submission_error,
            "Vimscript function calls are forbidden",
        ):
            validate_script("\n".join(lines) + "\n")

    def test_vim_runner_decodes_character_keys_before_safety_checks(self) -> None:
        task_dir = (
            PRODUCT_ROOT
            / "contracts/tasks/terminal-bench.large-scale-text-editing/2.1-r6"
        )
        namespace = runpy.run_path(str(task_dir / "runner.py"))
        decode_vim_string = namespace["_decode_vim_string"]
        validate_script = namespace["_validate_script"]
        submission_error = namespace["SubmissionError"]
        valid_script = runpy.run_path(str(task_dir / "probes/reference.py"))["script"]

        self.assertEqual(decode_vim_string(r"\<Char-40>"), "(")
        self.assertEqual(decode_vim_string(r"\\<Char-40>"), r"\<Char-40>")
        self.assertEqual(decode_vim_string(r"\<Bar>"), "|")
        with self.assertRaisesRegex(
            submission_error,
            "encoded Vim command separators are forbidden",
        ):
            decode_vim_string(r"\<Char-124>")

        lines = valid_script.splitlines()
        lines[0] = (
            "call setreg('a', \":call setline\\<Char-40>'.', 'bypass'"
            "\\<Char-41>\\<CR>j\")"
        )
        with self.assertRaisesRegex(
            submission_error,
            "Vimscript function calls are forbidden",
        ):
            validate_script("\n".join(lines) + "\n")

    def test_vim_runner_rejects_ranged_shell_filters(self) -> None:
        task_dir = (
            PRODUCT_ROOT
            / "contracts/tasks/terminal-bench.large-scale-text-editing/2.1-r6"
        )
        namespace = runpy.run_path(str(task_dir / "runner.py"))
        validate_script = namespace["_validate_script"]
        submission_error = namespace["SubmissionError"]
        valid_script = runpy.run_path(str(task_dir / "probes/reference.py"))["script"]

        lines = valid_script.splitlines()
        lines[0] = (
            "call setreg('a', \":set shell=/bin/sh\\<CR>"
            ":.!awk '{print toupper($0)}'\\<CR>j\")"
        )
        with self.assertRaisesRegex(
            submission_error,
            "macro contains a forbidden command or character",
        ):
            validate_script("\n".join(lines) + "\n")

    def test_vim_runner_rejects_abbreviated_and_aliased_file_reads(self) -> None:
        task_dir = (
            PRODUCT_ROOT
            / "contracts/tasks/terminal-bench.large-scale-text-editing/2.1-r6"
        )
        namespace = runpy.run_path(str(task_dir / "runner.py"))
        validate_script = namespace["_validate_script"]
        submission_error = namespace["SubmissionError"]
        valid_script = runpy.run_path(str(task_dir / "probes/reference.py"))["script"]

        for command in (
            r":r /etc/hostname\<CR>u",
            r":e /etc/passwd\<CR>u",
            r":so /tmp/foo\<CR>u",
            r":view /etc/hosts\<CR>u",
            r":sp /etc/shadow\<CR>u",
            r":vs /tmp/file\<CR>u",
            r":fin foo\<CR>u",
            r":tabe /tmp/file\<CR>u",
            r":b /tmp/file\<CR>u",
            r":w /tmp/out\<CR>u",
        ):
            with self.subTest(command=command):
                lines = valid_script.splitlines()
                lines[0] = f"call setreg('a', \"{command}j\")"
                with self.assertRaisesRegex(
                    submission_error,
                    "forbidden",
                ):
                    validate_script("\n".join(lines) + "\n")

    def test_commit_verifier_rejects_negated_required_actions(self) -> None:
        task_dir = (
            PRODUCT_ROOT
            / "contracts/tasks/omp-native.diff-commit-message/1.0.0"
        )
        valid_submission = runpy.run_path(str(task_dir / "probes/reference.py"))[
            "submission"
        ]
        verifier = runpy.run_path(str(task_dir / "verifier-private/verifier.py"))
        is_valid = verifier["_valid_submission"]

        self.assertTrue(is_valid(valid_submission))
        self.assertEqual(
            valid_submission["evidence"],
            ["change.patch:8-9", "change.patch:10-11"],
        )
        wider_evidence = deepcopy(valid_submission)
        wider_evidence["evidence"][0] = "change.patch:7-9"
        self.assertFalse(is_valid(wider_evidence))
        for subject in (
            "do not invalidate l1 before backing-store deletion",
            "do not ever invalidate l1 before backing-store deletion",
            "never invalidate l1 before backing-store deletion",
            "no l1 invalidation before backing-store deletion",
            "avoid invalidating l1 before backing-store deletion",
            "unable to invalidate l1 before backing-store deletion",
            "turn off l1 invalidation before backing-store deletion",
            "invalidate l1 but do not delete from the backing store",
            "invalidate l1 before backing-store deletionless",
            (
                "invalidate l1 before backing-store deletion "
                "to not delete the store"
            ),
            (
                "invalidate l1 after backing-store deletion, "
                "then delete backing-store"
            ),
        ):
            with self.subTest(subject=subject):
                negated = deepcopy(valid_submission)
                negated["subject"] = subject
                self.assertFalse(is_valid(negated))

        for sentence in (
            "L1 invalidation before backing store deletion is impossible.",
            "Stop invalidating L1 before deleting from the backing store.",
            "Turn off L1 invalidation before backing-store deletion.",
            "L1 invalidation is preceded by backing-store deletion.",
            "Backing-store deletion is followed by L1 invalidation.",
            "Invalidate L1 before deleting metrics; preserve the backing store.",
            "Invalidate L1 before canceling backing-store deletion.",
            "L1 invalidation before backing-store deletion is false.",
            "L1 invalidation before backing-store deletion is untrue.",
            "Undo L1 invalidation before backing-store deletion.",
            "Reverse L1 invalidation before backing-store deletion.",
            "Prohibit L1 invalidation before backing-store deletion.",
            "Reject L1 invalidation before backing-store deletion.",
            "L1 invalidation precedes backing-store deletionless.",
            "L1 invalidationless precedes backing-store deletion.",
            (
                "L1 invalidation precedes backing-store deletion "
                "to avoid both actions."
            ),
            (
                "Invalidate L1 after backing-store deletion, "
                "then delete from backing store."
            ),
            (
                "Invalidate L1 before deleting the backing store, but "
                "backing-store deletion precedes L1 invalidation."
            ),
        ):
            with self.subTest(sentence=sentence):
                negated = deepcopy(valid_submission)
                negated["body"][0] = sentence
                self.assertFalse(is_valid(negated))

        for sentence in (
            "Do not record hit and miss outcomes for cache deletions.",
            "Never track hit and miss outcomes for cache deletions.",
            "Record no hit and miss outcomes for cache deletions.",
            "Avoid logging hit and miss outcomes for cache deletions.",
            "Capturing hit and miss outcomes for cache deletions is disabled.",
            "Stop recording hit and miss outcomes for cache deletions.",
            "Recording hit and miss outcomes for cache deletions isn't enabled.",
            "Ignore hit and miss outcomes in cache deletion logic.",
            "Recording hit and miss outcomes for cache deletions is impossible.",
            "Turn off recording hit and miss outcomes for cache deletions.",
            "Opt out of recording hit and miss outcomes for cache deletions.",
            "Track cache deletion logic rather than hit and miss outcomes.",
        ):
            with self.subTest(sentence=sentence):
                negated = deepcopy(valid_submission)
                negated["body"][1] = sentence
                self.assertFalse(is_valid(negated))

        reversed_order = deepcopy(valid_submission)
        reversed_order["subject"] = "invalidate l1 after backing-store deletion"
        self.assertFalse(is_valid(reversed_order))

        unbound_deletion = deepcopy(valid_submission)
        unbound_deletion["subject"] = "invalidate l1 then delete cache"
        self.assertFalse(is_valid(unbound_deletion))

        unbound_deletion = deepcopy(valid_submission)
        unbound_deletion["subject"] = (
            "invalidate l1 before deleting metrics and preserving backing store"
        )
        self.assertFalse(is_valid(unbound_deletion))

        reversed_order = deepcopy(valid_submission)
        reversed_order["body"][0] = (
            "Delete from the backing store before invalidating the L1 entry."
        )
        self.assertFalse(is_valid(reversed_order))

        affirmative = deepcopy(valid_submission)
        affirmative["body"][0] = (
            "Invalidate the L1 entry before deleting from the backing store to "
            "preserve cache consistency."
        )
        affirmative["body"][1] = (
            "Track hit and miss outcomes for cache deletions."
        )
        self.assertTrue(is_valid(affirmative))

        for subject, sentence in (
            (
                "invalidate l1 then delete from backing store",
                "Invalidate L1, then delete from the backing store.",
            ),
            (
                "delete backing-store entry after invalidating l1",
                "Delete from the backing store after invalidating L1.",
            ),
            (
                "delete from backing store following l1 invalidation",
                "After invalidating L1, delete from the backing store.",
            ),
        ):
            with self.subTest(subject=subject):
                ordered = deepcopy(valid_submission)
                ordered["subject"] = subject
                ordered["body"][0] = sentence
                self.assertTrue(is_valid(ordered))

        for sentence in (
            "L1 invalidation precedes backing-store deletion.",
            "Backing-store deletion follows L1 invalidation.",
            "L1 invalidation is followed by backing-store deletion.",
            "Backing-store deletion is preceded by L1 invalidation.",
        ):
            with self.subTest(sentence=sentence):
                ordered = deepcopy(valid_submission)
                ordered["body"][0] = sentence
                self.assertTrue(is_valid(ordered))

        for purpose in (
            "to prevent stale reads",
            "to preserve cache consistency",
            "to avoid stale data",
            "so that cache state remains consistent",
            "so as to preserve cache consistency",
            "rather than leave stale data",
        ):
            sentence = (
                "L1 invalidation precedes backing-store deletion "
                f"{purpose}."
            )
            with self.subTest(sentence=sentence):
                purposeful = deepcopy(valid_submission)
                purposeful["body"][0] = sentence
                self.assertTrue(is_valid(purposeful))

        for subject, sentence in (
            (
                (
                    "invalidate l1 before deleting from backing store "
                    "to prevent stale reads"
                ),
                (
                    "Invalidate L1 before deleting from the backing store "
                    "rather than leave stale data."
                ),
            ),
            (
                "invalidate l1 after lookup, then delete from backing store",
                (
                    "Invalidate L1 after lookup, then delete from the "
                    "backing store."
                ),
            ),
        ):
            with self.subTest(subject=subject):
                purposeful = deepcopy(valid_submission)
                purposeful["subject"] = subject
                purposeful["body"][0] = sentence
                self.assertTrue(is_valid(purposeful))

        for action in (
            "capture",
            "collect",
            "count",
            "emit",
            "expose",
            "increment",
            "log",
            "measure",
            "observe",
            "persist",
            "publish",
            "record",
            "report",
            "store",
            "track",
        ):
            sentence = (
                f"{action.capitalize()} hit and miss outcomes for cache deletions."
            )
            with self.subTest(sentence=sentence):
                alternative = deepcopy(valid_submission)
                alternative["body"][1] = sentence
                self.assertTrue(is_valid(alternative))

        alternative = deepcopy(valid_submission)
        alternative["body"][1] = (
            "Increment cache deletion metrics with hit and miss outcomes."
        )
        self.assertTrue(is_valid(alternative))
        tamper = runpy.run_path(str(task_dir / "probes/tamper.py"))["submission"]
        self.assertFalse(is_valid(tamper))


class ResponsiveIncidentRunnerTests(unittest.TestCase):
    def test_rejects_character_reference_obfuscated_remote_urls(self) -> None:
        task_dir = (
            PRODUCT_ROOT
            / "contracts/tasks/omp-native.responsive-incident-console/1.0.0"
        )
        namespace = runpy.run_path(str(task_dir / "runner.py"))
        validate_submission = namespace["_validate_submission"]
        submission_error = namespace["SubmissionError"]

        for markup in (
            '<a href="java&#x73;cript:alert(1)">link</a>',
            '<a href="h&#x74;tps://example.invalid/asset">link</a>',
            '<a href="&#x2f;&#x2f;example.invalid/asset">link</a>',
            '<img srcset="local.png 1x, h&#x74;tps://example.invalid/a 2x">',
            '<form action="java&#x73;cript:alert(1)"></form>',
            '<meta http-equiv="refresh" content="0;url=/elsewhere">',
            '<base href="/elsewhere/">',
            '<div style="background-image:u&#x72;l(//example.invalid/a)"></div>',
        ):
            with self.subTest(markup=markup):
                submission = {
                    "schema_version": "rolebench.ui-implementation/v1",
                    "files": {
                        "index.html": (
                            "<!doctype html><html><head>"
                            '<link rel="stylesheet" href="styles.css">'
                            f"</head><body>{markup}</body></html>"
                        ),
                        "styles.css": "body { color: #111; background: #fff; }",
                    },
                }
                with self.assertRaisesRegex(
                    submission_error,
                    "forbidden active or remote content",
                ):
                    validate_submission(submission, {})

    def test_binds_navigation_and_menu_semantics_to_audit_hooks(self) -> None:
        task_dir = (
            PRODUCT_ROOT
            / "contracts/tasks/omp-native.responsive-incident-console/1.0.0"
        )
        namespace = runpy.run_path(str(task_dir / "runner.py"))
        validate_submission = namespace["_validate_submission"]
        reference = runpy.run_path(str(task_dir / "probes/reference.py"))
        brief = json.loads(
            (task_dir / "public/workspace/brief.json").read_text(encoding="utf-8")
        )
        valid_html = reference["html"]
        valid = {
            "schema_version": "rolebench.ui-implementation/v1",
            "files": {
                "index.html": valid_html,
                "styles.css": reference["css"],
            },
        }
        _, _, checks = validate_submission(valid, brief)
        self.assertTrue(checks["active_navigation_labelled"])
        self.assertTrue(checks["mobile_menu_labelled"])
        self.assertTrue(checks["audit_hooks_present"])

        unrelated_current = valid_html.replace(
            ' aria-current="page"',
            "",
            1,
        ).replace(
            '<div class="brand">',
            '<div class="brand" aria-current="page">',
            1,
        )
        menu_element = (
            '<button class="menu-button" type="button" '
            'data-role="mobile-menu" aria-label="Open primary navigation">'
            "Menu</button>"
        )
        tampered_cases = {
            "unrelated aria-current": (
                unrelated_current,
                "active_navigation_labelled",
            ),
            "non-button menu hook": (
                valid_html.replace(
                    menu_element,
                    '<div class="menu-button" data-role="mobile-menu" '
                    'aria-label="Open primary navigation">Menu</div>',
                    1,
                ),
                "mobile_menu_labelled",
            ),
            "disabled menu button": (
                valid_html.replace(
                    'type="button" data-role="mobile-menu"',
                    'type="button" disabled data-role="mobile-menu"',
                    1,
                ),
                "mobile_menu_labelled",
            ),
            "duplicate menu hook": (
                valid_html.replace(
                    "</header>",
                    '<button data-role="mobile-menu" aria-label="Other menu">'
                    "Other</button></header>",
                    1,
                ),
                "audit_hooks_present",
            ),
            "non-filter primary action hook": (
                valid_html.replace(
                    'data-role="primary-action"',
                    "",
                    1,
                ).replace(
                    '<button class="menu-button"',
                    '<button class="menu-button" data-role="primary-action"',
                    1,
                ),
                "audit_hooks_present",
            ),
        }
        for name, (html, failed_check) in tampered_cases.items():
            with self.subTest(name=name):
                tampered = {
                    "schema_version": "rolebench.ui-implementation/v1",
                    "files": {
                        "index.html": html,
                        "styles.css": reference["css"],
                    },
                }
                _, _, tampered_checks = validate_submission(tampered, brief)
                self.assertFalse(tampered_checks[failed_check])

    def test_disclosure_exercise_accepts_either_initial_state(self) -> None:
        task_dir = (
            PRODUCT_ROOT
            / "contracts/tasks/omp-native.responsive-incident-console/1.0.0"
        )
        namespace = runpy.run_path(str(task_dir / "runner.py"))
        exercise_disclosures = namespace["_exercise_disclosures"]

        class DisclosureClient:
            def __init__(self) -> None:
                self.states = [False, True]
                self.focused_index = 0

            @staticmethod
            def _result(value: object) -> dict[str, object]:
                return {"result": {"value": value}}

            def command(
                self,
                method: str,
                params: dict[str, object] | None = None,
            ) -> dict[str, object]:
                params = params or {}
                if method == "Input.dispatchKeyEvent":
                    if params.get("type") == "keyDown":
                        self.states[self.focused_index] = not self.states[
                            self.focused_index
                        ]
                    return {}
                self.assert_runtime_evaluate(method)
                expression = str(params.get("expression", ""))
                if expression == "document.querySelectorAll('details > summary').length":
                    return self._result(len(self.states))
                if "return {focused:" in expression:
                    match = re.search(
                        r"document\.querySelectorAll\('details'\)\[([0-9]+)\]",
                        expression,
                    )
                    if match is None:
                        raise AssertionError("missing disclosure index")
                    self.focused_index = int(match.group(1))
                    return self._result(
                        {
                            "focused": True,
                            "open": self.states[self.focused_index],
                        }
                    )
                if expression == "scrollTo(0, 0); true":
                    return self._result(True)
                match = re.fullmatch(
                    r"document\.querySelectorAll\('details'\)\[([0-9]+)\]\.open",
                    expression,
                )
                if match is None:
                    raise AssertionError(f"unexpected expression: {expression}")
                return self._result(self.states[int(match.group(1))])

            @staticmethod
            def assert_runtime_evaluate(method: str) -> None:
                if method != "Runtime.evaluate":
                    raise AssertionError(f"unexpected method: {method}")

        client = DisclosureClient()
        exercise_disclosures(client, 2)
        self.assertEqual(client.states, [True, True])


class PublishedCandidateRegressionTests(unittest.TestCase):
    def test_memory_candidate_meets_verifier_narrative_gates(self) -> None:
        task_dir = (
            PRODUCT_ROOT
            / "contracts/tasks/terminal-bench.custom-memory-heap-crash/2.1-r6"
        )
        candidate = runpy.run_path(str(task_dir / "probes/candidate.py"))[
            "CANDIDATE_DIAGNOSIS"
        ]
        is_substantive_text = runpy.run_path(
            str(task_dir / "verifier-private/verifier.py")
        )["_is_substantive_text"]

        technical_notes = candidate["technical_notes"]
        for field in ("root_cause_explanation", "recovery_explanation"):
            with self.subTest(field=field):
                self.assertTrue(
                    is_substantive_text(
                        technical_notes[field],
                        min_chars=120,
                        min_words=15,
                    )
                )

    def test_memory_release_requires_custom_runtime(self) -> None:
        task_dir = (
            PRODUCT_ROOT
            / "contracts/tasks/terminal-bench.custom-memory-heap-crash/2.1-r6"
        )
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(task_dir / "public/workspace", workspace)
            missing_runtime = workspace / "missing-release-runtime"
            empty_runtime = workspace / "empty-release-runtime"
            empty_runtime.mkdir()

            for release_runtime in (missing_runtime, empty_runtime):
                with self.subTest(release_runtime=release_runtime):
                    result = subprocess.run(
                        [
                            "make",
                            "release",
                            f"RELEASE_LIB={release_runtime}",
                        ],
                        cwd=workspace,
                        capture_output=True,
                        check=False,
                        text=True,
                    )

                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(
                        "Required custom release runtime archive "
                        f"{release_runtime}/libstdc++.a not found.",
                        result.stderr,
                    )
                    self.assertFalse((workspace / "release").exists())

    def test_scheduler_candidate_satisfies_runner_contract(self) -> None:
        task_dir = (
            PRODUCT_ROOT
            / "contracts/tasks/terminal-bench.llm-inference-batching-scheduler/2.1-r6"
        )
        candidate = runpy.run_path(str(task_dir / "probes/candidate.py"))[
            "CANDIDATE_PLAN"
        ]
        validate_submission = runpy.run_path(str(task_dir / "runner.py"))[
            "_validate_submission"
        ]

        normalized = validate_submission(candidate)
        expected = json.loads(
            (
                task_dir / "verifier-private/expected_plan_graph.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            normalized["service_level_model"],
            expected["required_service_level_model"],
        )

    def test_scheduler_verifier_rejects_non_rollback_gate_fallback(self) -> None:
        task_dir = (
            PRODUCT_ROOT
            / "contracts/tasks/terminal-bench.llm-inference-batching-scheduler/2.1-r6"
        )
        candidate = runpy.run_path(str(task_dir / "probes/candidate.py"))[
            "CANDIDATE_PLAN"
        ]
        validate_submission = runpy.run_path(str(task_dir / "runner.py"))[
            "_validate_submission"
        ]
        verify_plan = runpy.run_path(
            str(task_dir / "verifier-private/verifier.py")
        )["_verify_plan"]
        expected = json.loads(
            (
                task_dir / "verifier-private/expected_plan_graph.json"
            ).read_text(encoding="utf-8")
        )
        normalized = validate_submission(candidate)
        self.assertTrue(verify_plan(normalized, expected))

        weakened = deepcopy(normalized)
        weakened["risks_and_mitigations"]["gates"][0][
            "fallback_action"
        ] = "FAIL_CLOSED"
        self.assertFalse(verify_plan(weakened, expected))


class CancelAsyncRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        namespace = runpy.run_path(
            str(
                PRODUCT_ROOT
                / "contracts/tasks/terminal-bench.cancel-async-tasks"
                / "2.1-r6/runner.py"
            )
        )
        self.validate_source = namespace["_validate_candidate_source"]
        self.validation_error = namespace["SourceValidationError"]

    def test_accepts_running_loop_api(self) -> None:
        sources = (
            (
                "import asyncio\n\n"
                "async def run_tasks(tasks, max_concurrent):\n"
                "    asyncio.get_running_loop()\n"
            ),
            (
                "from asyncio import get_running_loop\n\n"
                "async def run_tasks(tasks, max_concurrent):\n"
                "    get_running_loop()\n"
            ),
        )
        for source in sources:
            with self.subTest(source=source):
                self.validate_source(source)

    def test_accepts_direct_exact_type_identity_check(self) -> None:
        self.validate_source(
            "async def run_tasks(tasks, max_concurrent):\n"
            "    if type(max_concurrent) is not int:\n"
            "        raise ValueError('invalid concurrency')\n"
        )

    def test_type_result_cannot_escape_exact_identity_check(self) -> None:
        sources = (
            "async def run_tasks(tasks, max_concurrent):\n"
            "    value_type = type(max_concurrent)\n",
            "async def run_tasks(tasks, max_concurrent):\n"
            "    if type(max_concurrent, (), {}) is int:\n"
            "        return None\n",
            "async def run_tasks(tasks, max_concurrent):\n"
            "    if type(max_concurrent) == int:\n"
            "        return None\n",
            "async def run_tasks(tasks, max_concurrent):\n"
            "    if type(max_concurrent) is bool:\n"
            "        return None\n",
        )
        for source in sources:
            with self.subTest(source=source):
                with self.assertRaises(self.validation_error):
                    self.validate_source(source)

    def test_accepts_safe_module_typing_aliases(self) -> None:
        sources = (
            (
                "from typing import Awaitable, Callable\n\n"
                "TaskFactory = Callable[[], Awaitable[None]]\n\n"
                "async def run_tasks(tasks, max_concurrent):\n"
                "    pass\n"
            ),
            (
                "from typing import Callable as C, Awaitable as A\n\n"
                "TaskFactory = C[[], A[None]]\n\n"
                "async def run_tasks(tasks, max_concurrent):\n"
                "    pass\n"
            ),
            (
                "import typing as t\n\n"
                "TaskFactory = t.Callable[[], t.Awaitable[None]]\n"
                "TaskAlias = TaskFactory | None\n\n"
                "async def run_tasks(tasks, max_concurrent):\n"
                "    pass\n"
            ),
            (
                "import typing as t\n"
                "TaskAlias = t.Callable\n"
                "import asyncio as t\n\n"
                "async def run_tasks(tasks, max_concurrent):\n"
                "    pass\n"
            ),
        )
        for source in sources:
            with self.subTest(source=source):
                self.validate_source(source)

    def test_rejects_module_assignment_runtime_call_and_non_alias(self) -> None:
        sources = (
            (
                "import asyncio\n\n"
                "TaskFactory = asyncio.sleep(0)\n\n"
                "async def run_tasks(tasks, max_concurrent):\n"
                "    pass\n"
            ),
            (
                "from typing import Callable\n\n"
                "A = B = Callable[[], None]\n\n"
                "async def run_tasks(tasks, max_concurrent):\n"
                "    pass\n"
            ),
            (
                "import asyncio\n\n"
                "TaskFactory = asyncio.Queue\n\n"
                "async def run_tasks(tasks, max_concurrent):\n"
                "    pass\n"
            ),
            (
                "import asyncio\n\n"
                "(asyncio.create_task,) = (None,)\n\n"
                "async def run_tasks(tasks, max_concurrent):\n"
                "    pass\n"
            ),
            (
                "from typing import Callable\n\n"
                "def poison():\n"
                "    global Callable\n"
                '    Callable = {"value": int}\n'
                "    return {}\n\n"
                'poison()["x"] = 1\n'
                'TaskAlias = Callable["value"]\n\n'
                "async def run_tasks(tasks, max_concurrent):\n"
                "    pass\n"
            ),
            (
                "from typing import Callable\n\n"
                "def poison():\n"
                "    global Callable\n"
                '    Callable = {"value": int}\n'
                "    return {}\n\n"
                'poison()["x"]: int\n'
                'TaskAlias = Callable["value"]\n\n'
                "async def run_tasks(tasks, max_concurrent):\n"
                "    pass\n"
            ),
        )
        for source in sources:
            with self.subTest(source=source):
                with self.assertRaises(self.validation_error):
                    self.validate_source(source)

    def test_rejects_type_names_after_unsafe_rebinding(self) -> None:
        sources = (
            (
                'list = {"value": "not-a-type"}\n'
                'TaskFactory = list["value"]\n\n'
                "async def run_tasks(tasks, max_concurrent):\n"
                "    pass\n"
            ),
            (
                "from typing import Callable\n\n"
                'Callable = {"value": "not-a-type"}\n'
                'TaskFactory = Callable["value"]\n\n'
                "async def run_tasks(tasks, max_concurrent):\n"
                "    pass\n"
            ),
            (
                "from typing import Callable\n\n"
                "TaskFactory = Callable[[], None]\n"
                'TaskFactory = {"value": "not-a-type"}\n'
                'TaskAlias = TaskFactory["value"]\n\n'
                "async def run_tasks(tasks, max_concurrent):\n"
                "    pass\n"
            ),
            (
                "def list(value):\n"
                "    return value\n\n"
                "TaskFactory = list[int]\n\n"
                "async def run_tasks(tasks, max_concurrent):\n"
                "    pass\n"
            ),
            (
                'TaskFactory = "not-a-type"\n'
                "TaskAlias = TaskFactory[0]\n\n"
                "async def run_tasks(tasks, max_concurrent):\n"
                "    pass\n"
            ),
            (
                "TaskFactory = [int]\n"
                "TaskAlias = TaskFactory[0]\n\n"
                "async def run_tasks(tasks, max_concurrent):\n"
                "    pass\n"
            ),
            (
                "TaskFactory = (int, str)\n"
                "TaskAlias = TaskFactory[0]\n\n"
                "async def run_tasks(tasks, max_concurrent):\n"
                "    pass\n"
            ),
            (
                "from typing import Callable\n\n"
                "TaskFactory = Callable[[], None]\n"
                "from asyncio import sleep as TaskFactory\n"
                "TaskAlias = TaskFactory[int]\n\n"
                "async def run_tasks(tasks, max_concurrent):\n"
                "    pass\n"
            ),
            (
                "from typing import Callable\n"
                '*Callable, = ["runtime"]\n'
                "TaskAlias = Callable\n\n"
                "async def run_tasks(tasks, max_concurrent):\n"
                "    pass\n"
            ),
            (
                "import typing as t\n"
                "TaskAlias = t.Callable\n"
                "import asyncio as t\n\n"
                "async def run_tasks(tasks, max_concurrent):\n"
                "    return t.Callable\n"
            ),
        )
        for source in sources:
            with self.subTest(source=source):
                with self.assertRaises(self.validation_error):
                    self.validate_source(source)


if __name__ == "__main__":
    unittest.main()
